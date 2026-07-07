//! wry bindings for embedding a WebView into a Tkinter host window.

#[cfg(target_os = "macos")]
mod macos_focus;

use pyo3::prelude::*;
#[cfg(target_os = "macos")]
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::{Arc, Mutex};

fn make_rect(x: f64, y: f64, width: f64, height: f64) -> wry::Rect {
    wry::Rect {
        position: wry::dpi::Position::Logical(wry::dpi::LogicalPosition::new(x, y)),
        size: wry::dpi::Size::Logical(wry::dpi::LogicalSize::new(width, height)),
    }
}

/// Maximum number of buffered page-load events. When no Python handler is
/// draining the queue, older events are discarded to prevent unbounded growth.
const MAX_PAGE_LOAD_PENDING: usize = 256;
const MAX_IPC_PENDING: usize = 256;
const MAX_TITLE_PENDING: usize = 256;
const MAX_DRAG_DROP_PENDING: usize = 256;

/// Maximum IPC message size (10 MiB). Messages exceeding this are dropped.
const MAX_IPC_MESSAGE_BYTES: usize = 10 * 1024 * 1024;

/// Print a Python exception (with traceback) to stderr from a Rust callback.
fn report_py_error(py: Python<'_>, err: PyErr) {
    err.print(py);
}

fn push_bounded<T>(pending: &Arc<Mutex<Vec<T>>>, item: T, max: usize, label: &str) {
    if let Ok(mut queue) = pending.lock() {
        if queue.len() >= max {
            let half = queue.len() / 2;
            eprintln!(
                "tkwry: discarding {half} oldest {label} event(s) (pending queue exceeded {max} event limit)"
            );
            queue.drain(..half);
        }
        queue.push(item);
    }
}

fn extract_py_bool(result: &Bound<'_, PyAny>, context: &str) -> Option<bool> {
    match result.extract::<bool>() {
        Ok(value) => Some(value),
        Err(err) => {
            eprintln!("tkwry: {context}: callback must return bool ({err})");
            None
        }
    }
}

fn extract_new_window_response(
    result: &Bound<'_, PyAny>,
    context: &str,
) -> Option<NewWindowResponse> {
    match result.extract::<NewWindowResponse>() {
        Ok(value) => Some(value),
        Err(err) => {
            eprintln!("tkwry: {context}: callback must return NewWindowResponse ({err})");
            None
        }
    }
}

fn call_sync_bool_callback(
    py: Python<'_>,
    func: &Py<PyAny>,
    url: &str,
    context: &str,
    default_on_error: bool,
) -> bool {
    match func.call1(py, (url,)) {
        Ok(result) => extract_py_bool(&result.bind(py), context).unwrap_or(default_on_error),
        Err(err) => {
            report_py_error(py, err);
            default_on_error
        }
    }
}

#[pyclass(eq, eq_int, frozen, skip_from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum PageLoadEvent {
    Started,
    Finished,
}

type PyCallback = Arc<Mutex<Option<Py<PyAny>>>>;
type PageLoadPending = Arc<Mutex<Vec<(PageLoadEvent, String)>>>;
type IpcPending = Arc<Mutex<Vec<String>>>;
type TitlePending = Arc<Mutex<Vec<String>>>;
type DragDropPendingItem = (DragDropEvent, Vec<String>, (i32, i32));
type DragDropPending = Arc<Mutex<Vec<DragDropPendingItem>>>;

const THREAD_ERROR: &str = "tkwry must be called from the thread that created the Tk application (the thread that runs the Tk event loop)";

fn python_thread_id() -> PyResult<u64> {
    Python::attach(|py| {
        py.import("threading")?
            .getattr("get_ident")?
            .call0()?
            .extract()
    })
}

#[pyclass(unsendable)]
struct WebView {
    /// Python ``threading.get_ident()`` for the owning Tk thread.
    owner_thread: u64,
    /// macOS focus monitor clones this; GTK WebView is UI-thread-only.
    #[allow(clippy::arc_with_non_send_sync)]
    inner: Arc<Mutex<Option<wry::WebView>>>,
    page_load_pending: PageLoadPending,
    ipc_pending: IpcPending,
    title_pending: TitlePending,
    drag_drop_pending: DragDropPending,
    #[cfg(target_os = "macos")]
    _focus_sync: Mutex<Option<macos_focus::FocusSyncGuard>>,
    #[cfg(target_os = "macos")]
    web_wants_keyboard: Arc<AtomicBool>,
    #[cfg(target_os = "macos")]
    mac_tk_unfocus: Arc<AtomicBool>,
    #[cfg(target_os = "macos")]
    wakeup_write_fd: Arc<AtomicI32>,
    ipc_cb: PyCallback,
    nav_cb: PyCallback,
    title_cb: PyCallback,
    newwin_cb: PyCallback,
    drag_drop_cb: PyCallback,
}

impl WebView {
    fn require_owner_thread(&self) -> PyResult<()> {
        let current = python_thread_id()?;
        if current != self.owner_thread {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(THREAD_ERROR));
        }
        Ok(())
    }
}

#[pymethods]
impl WebView {
    #[new]
    #[pyo3(signature = (
        parent,
        *,
        owner_thread = None,
        width = 800,
        height = 600,
        url = None,
        html = None,
        visible = true,
        devtools = false,
        focused = true,
        background_color = None,
        user_agent = None,
        initialization_script = None,
        ipc_handler = None,
        on_navigation = None,
        on_title_changed = None,
        on_new_window = None,
        drag_drop_handler = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        parent: isize,
        owner_thread: Option<u64>,
        width: u32,
        height: u32,
        url: Option<String>,
        html: Option<String>,
        visible: bool,
        devtools: bool,
        focused: bool,
        background_color: Option<(u8, u8, u8, u8)>,
        user_agent: Option<String>,
        initialization_script: Option<String>,
        ipc_handler: Option<Py<PyAny>>,
        on_navigation: Option<Py<PyAny>>,
        on_title_changed: Option<Py<PyAny>>,
        on_new_window: Option<Py<PyAny>>,
        drag_drop_handler: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let owner_thread = match owner_thread {
            Some(id) => id,
            None => python_thread_id()?,
        };

        #[cfg(target_os = "windows")]
        let window_handle = {
            use raw_window_handle::{RawWindowHandle, Win32WindowHandle};
            use std::num::NonZero;
            let hwnd = NonZero::new(parent as _)
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("parent handle is null"))?;
            let raw = RawWindowHandle::Win32(Win32WindowHandle::new(hwnd));
            unsafe { raw_window_handle::WindowHandle::borrow_raw(raw) }
        };

        #[cfg(target_os = "macos")]
        let (window_handle, parent_ns_view) = {
            use objc2_app_kit::NSView;
            use raw_window_handle::{AppKitWindowHandle, RawWindowHandle};
            use std::ptr::NonNull;
            let ptr = parent as *mut std::ffi::c_void;
            let ns_view = NonNull::new(ptr)
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("parent handle is null"))?;
            let parent_ns_view = unsafe { NonNull::new_unchecked(ptr.cast::<NSView>()) };
            let raw = RawWindowHandle::AppKit(AppKitWindowHandle::new(ns_view));
            let handle = unsafe { raw_window_handle::WindowHandle::borrow_raw(raw) };
            (handle, parent_ns_view)
        };

        #[cfg(all(unix, not(target_os = "macos")))]
        let window_handle = {
            use raw_window_handle::{RawWindowHandle, XlibWindowHandle};
            if parent == 0 {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "parent handle is null",
                ));
            }
            let raw = RawWindowHandle::Xlib(XlibWindowHandle::new(parent as u64));
            unsafe { raw_window_handle::WindowHandle::borrow_raw(raw) }
        };

        let ipc_cb: PyCallback = Arc::new(Mutex::new(ipc_handler));
        let nav_cb: PyCallback = Arc::new(Mutex::new(on_navigation));
        let title_cb: PyCallback = Arc::new(Mutex::new(on_title_changed));
        let newwin_cb: PyCallback = Arc::new(Mutex::new(on_new_window));
        let drag_drop_cb: PyCallback = Arc::new(Mutex::new(drag_drop_handler));
        let page_load_pending: PageLoadPending = Arc::new(Mutex::new(Vec::new()));
        let ipc_pending: IpcPending = Arc::new(Mutex::new(Vec::new()));
        let title_pending: TitlePending = Arc::new(Mutex::new(Vec::new()));
        let drag_drop_pending: DragDropPending = Arc::new(Mutex::new(Vec::new()));

        let ipc_pending_clone = ipc_pending.clone();
        let ipc_handler_wry = move |req: wry::http::Request<String>| {
            let body = req.body().clone();
            if body.len() > MAX_IPC_MESSAGE_BYTES {
                eprintln!(
                    "tkwry: IPC message dropped ({} bytes exceeds {} byte limit)",
                    body.len(),
                    MAX_IPC_MESSAGE_BYTES
                );
                return;
            }
            push_bounded(&ipc_pending_clone, body, MAX_IPC_PENDING, "IPC");
        };

        let nav_cb_clone = nav_cb.clone();
        let nav_handler = move |url: String| -> bool {
            Python::attach(|py| {
                if let Ok(guard) = nav_cb_clone.lock() {
                    if let Some(ref func) = *guard {
                        return call_sync_bool_callback(
                            py,
                            func,
                            url.as_str(),
                            "on_navigation",
                            false,
                        );
                    }
                }
                true
            })
        };

        let page_load_pending_clone = page_load_pending.clone();
        let pageload_handler = move |event: wry::PageLoadEvent, url: String| {
            let evt = match event {
                wry::PageLoadEvent::Started => PageLoadEvent::Started,
                wry::PageLoadEvent::Finished => PageLoadEvent::Finished,
            };
            push_bounded(
                &page_load_pending_clone,
                (evt, url),
                MAX_PAGE_LOAD_PENDING,
                "page-load",
            );
        };

        let title_pending_clone = title_pending.clone();
        let title_handler = move |title: String| {
            push_bounded(&title_pending_clone, title, MAX_TITLE_PENDING, "title-changed");
        };

        let newwin_cb_clone = newwin_cb.clone();
        let newwin_handler = move |url: String,
                                   _features: wry::NewWindowFeatures|
              -> wry::NewWindowResponse {
            Python::attach(|py| {
                if let Ok(guard) = newwin_cb_clone.lock() {
                    if let Some(ref func) = *guard {
                        match func.call1(py, (url.as_str(),)) {
                            Ok(result) => {
                                if let Some(resp) =
                                    extract_new_window_response(&result.bind(py), "on_new_window")
                                {
                                    return match resp {
                                        NewWindowResponse::Deny => wry::NewWindowResponse::Deny,
                                        NewWindowResponse::Allow => wry::NewWindowResponse::Allow,
                                    };
                                }
                                return wry::NewWindowResponse::Deny;
                            }
                            Err(err) => report_py_error(py, err),
                        }
                    }
                }
                wry::NewWindowResponse::Allow
            })
        };

        let drag_drop_pending_clone = drag_drop_pending.clone();
        let drag_drop_handler = move |event: wry::DragDropEvent| -> bool {
            let (evt_type, paths, position) = match &event {
                wry::DragDropEvent::Enter { paths, position } => {
                    (DragDropEvent::Enter, paths.clone(), *position)
                }
                wry::DragDropEvent::Over { position } => (DragDropEvent::Over, vec![], *position),
                wry::DragDropEvent::Drop { paths, position } => {
                    (DragDropEvent::Drop, paths.clone(), *position)
                }
                wry::DragDropEvent::Leave => (DragDropEvent::Leave, vec![], (0, 0)),
                _ => (DragDropEvent::Unknown, vec![], (0, 0)),
            };
            let paths_str: Vec<String> = paths
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect();
            let pos = (position.0, position.1);
            push_bounded(
                &drag_drop_pending_clone,
                (evt_type, paths_str, pos),
                MAX_DRAG_DROP_PENDING,
                "drag-drop",
            );
            true
        };

        let mut builder = wry::WebViewBuilder::new()
            .with_bounds(make_rect(0.0, 0.0, width as f64, height as f64))
            .with_visible(visible)
            .with_devtools(devtools)
            .with_focused(focused)
            .with_ipc_handler(ipc_handler_wry)
            .with_navigation_handler(nav_handler)
            .with_on_page_load_handler(pageload_handler)
            .with_document_title_changed_handler(title_handler)
            .with_new_window_req_handler(newwin_handler)
            .with_drag_drop_handler(drag_drop_handler);

        if let Some(bg) = background_color {
            builder = builder.with_background_color(bg);
        }
        if let Some(ref ua) = user_agent {
            builder = builder.with_user_agent(ua);
        }
        if let Some(ref script) = initialization_script {
            builder = builder.with_initialization_script(script);
        }
        if let Some(u) = url {
            builder = builder.with_url(u);
        }
        if let Some(h) = html {
            builder = builder.with_html(h);
        }

        #[cfg(target_os = "macos")]
        {
            builder = builder.with_accept_first_mouse(true);
        }

        #[cfg(all(unix, not(target_os = "macos")))]
        {
            if let Err(e) = gtk::init() {
                if !gtk::is_initialized() {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "GTK init failed: {e}. Is $DISPLAY set?"
                    )));
                }
            }
        }

        let webview = builder
            .build_as_child(&window_handle)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        #[cfg(all(unix, not(target_os = "macos")))]
        {
            for _ in 0..64 {
                if !gtk::main_iteration_do(false) {
                    break;
                }
            }
        }

        #[allow(clippy::arc_with_non_send_sync)]
        let inner = Arc::new(Mutex::new(Some(webview)));

        #[cfg(target_os = "macos")]
        let web_wants_keyboard = Arc::new(AtomicBool::new(false));
        #[cfg(target_os = "macos")]
        let mac_tk_unfocus = Arc::new(AtomicBool::new(false));
        #[cfg(target_os = "macos")]
        let wakeup_write_fd = Arc::new(AtomicI32::new(-1));

        #[cfg(target_os = "macos")]
        let _focus_sync = {
            let guard = macos_focus::install_focus_sync(
                inner.clone(),
                parent_ns_view,
                web_wants_keyboard.clone(),
                mac_tk_unfocus.clone(),
                wakeup_write_fd.clone(),
            )
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
            Mutex::new(Some(guard))
        };

        Ok(Self {
            owner_thread,
            inner,
            page_load_pending,
            ipc_pending,
            title_pending,
            drag_drop_pending,
            #[cfg(target_os = "macos")]
            _focus_sync,
            #[cfg(target_os = "macos")]
            web_wants_keyboard,
            #[cfg(target_os = "macos")]
            mac_tk_unfocus,
            #[cfg(target_os = "macos")]
            wakeup_write_fd,
            ipc_cb,
            nav_cb,
            title_cb,
            newwin_cb,
            drag_drop_cb,
        })
    }

    fn load_url(&self, url: &str) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.load_url(url)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn load_html(&self, html: &str) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.load_html(html)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn reload(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.reload()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn eval_js(&self, script: &str) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.evaluate_script(script)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn eval_js_with_callback(&self, script: &str, callback: Py<PyAny>) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.evaluate_script_with_callback(script, move |result: String| {
                Python::attach(|py| {
                    if let Err(err) = callback.call1(py, (result,)) {
                        report_py_error(py, err);
                    }
                });
            })
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn set_ipc_handler(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .ipc_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_ipc_handler(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .ipc_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        Ok(())
    }

    fn set_on_navigation(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .nav_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_on_navigation(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .nav_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        Ok(())
    }

    fn drain_page_load_events(&self) -> PyResult<Vec<(PageLoadEvent, String)>> {
        self.require_owner_thread()?;
        Ok(self
            .page_load_pending
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default())
    }

    fn drain_ipc_messages(&self) -> PyResult<Vec<String>> {
        self.require_owner_thread()?;
        let messages = self
            .ipc_pending
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default();
        if let Ok(guard) = self.ipc_cb.lock() {
            if let Some(ref func) = *guard {
                Python::attach(|py| {
                    for msg in &messages {
                        if let Err(err) = func.call1(py, (msg.as_str(),)) {
                            report_py_error(py, err);
                        }
                    }
                });
            }
        }
        Ok(messages)
    }

    fn drain_title_events(&self) -> PyResult<Vec<String>> {
        self.require_owner_thread()?;
        let titles = self
            .title_pending
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default();
        if let Ok(guard) = self.title_cb.lock() {
            if let Some(ref func) = *guard {
                Python::attach(|py| {
                    for title in &titles {
                        if let Err(err) = func.call1(py, (title.as_str(),)) {
                            report_py_error(py, err);
                        }
                    }
                });
            }
        }
        Ok(titles)
    }

    fn drain_drag_drop_events(
        &self,
    ) -> PyResult<Vec<(DragDropEvent, Vec<String>, (i32, i32))>> {
        self.require_owner_thread()?;
        let events = self
            .drag_drop_pending
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default();
        if let Ok(guard) = self.drag_drop_cb.lock() {
            if let Some(ref func) = *guard {
                Python::attach(|py| {
                    for (evt_type, paths, pos) in &events {
                        if let Err(err) = func.call1(py, (*evt_type, paths.clone(), *pos)) {
                            report_py_error(py, err);
                        }
                    }
                });
            }
        }
        Ok(events)
    }

    fn _enqueue_ipc_message(&self, message: String) -> PyResult<()> {
        self.require_owner_thread()?;
        if message.len() > MAX_IPC_MESSAGE_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "IPC message exceeds {} byte limit",
                MAX_IPC_MESSAGE_BYTES
            )));
        }
        push_bounded(&self.ipc_pending, message, MAX_IPC_PENDING, "IPC");
        Ok(())
    }

    fn _enqueue_title_event(&self, title: String) -> PyResult<()> {
        self.require_owner_thread()?;
        push_bounded(&self.title_pending, title, MAX_TITLE_PENDING, "title-changed");
        Ok(())
    }

    fn _enqueue_drag_drop_event(
        &self,
        event: DragDropEvent,
        paths: Vec<String>,
        position: (i32, i32),
    ) -> PyResult<()> {
        self.require_owner_thread()?;
        push_bounded(
            &self.drag_drop_pending,
            (event, paths, position),
            MAX_DRAG_DROP_PENDING,
            "drag-drop",
        );
        Ok(())
    }

    fn set_on_title_changed(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .title_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_on_title_changed(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .title_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        Ok(())
    }

    fn set_on_new_window(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .newwin_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_on_new_window(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .newwin_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        Ok(())
    }

    fn set_drag_drop_handler(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .drag_drop_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_drag_drop_handler(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .drag_drop_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        Ok(())
    }

    fn set_bounds(&self, x: f64, y: f64, width: f64, height: f64) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.set_bounds(make_rect(x, y, width, height))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn set_visible(&self, visible: bool) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.set_visible(visible)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn set_background_color(&self, r: u8, g: u8, b: u8, a: u8) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.set_background_color((r, g, b, a))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn set_mac_web_input_active(&self, active: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        self.web_wants_keyboard.store(active, Ordering::SeqCst);
        #[cfg(not(target_os = "macos"))]
        let _ = (self, active);
        Ok(())
    }

    fn set_mac_wakeup_write_fd(&self, fd: i32) -> PyResult<()> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        self.wakeup_write_fd.store(fd, Ordering::SeqCst);
        #[cfg(not(target_os = "macos"))]
        let _ = (self, fd);
        Ok(())
    }

    fn take_mac_tk_unfocus(&self) -> PyResult<bool> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            Ok(self.mac_tk_unfocus.swap(false, Ordering::SeqCst))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = self;
            Ok(false)
        }
    }

    /// Whether Rust has requested a Tcl unfocus drain (``mac_tk_unfocus`` flag).
    fn mac_tk_unfocus_pending(&self) -> PyResult<bool> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            Ok(self.mac_tk_unfocus.load(Ordering::SeqCst))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = self;
            Ok(false)
        }
    }

    /// Set coordination flags as the NSEvent web-click path does (tests / debugging).
    fn mac_request_tk_unfocus(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            self.web_wants_keyboard.store(true, Ordering::SeqCst);
            self.mac_tk_unfocus.store(true, Ordering::SeqCst);
            macos_focus::notify_wakeup(&self.wakeup_write_fd);
        }
        #[cfg(not(target_os = "macos"))]
        let _ = self;
        Ok(())
    }

    /// Whether this webview currently owns macOS keyboard routing (``web_wants``).
    fn mac_web_input_active(&self) -> PyResult<bool> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            Ok(self.web_wants_keyboard.load(Ordering::SeqCst))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = self;
            Ok(false)
        }
    }

    /// Hit-test in wry top-left coordinates (same space as ``set_bounds``).
    fn mac_hit_test_wry_point(&self, x: f64, y: f64) -> PyResult<bool> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            Ok(macos_focus::hit_test_wry_point(&self.inner, x, y))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = (self, x, y);
            Ok(false)
        }
    }

    fn focus(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.focus()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn focus_parent(&self) -> PyResult<()> {
        #[cfg(target_os = "macos")]
        self.web_wants_keyboard.store(false, Ordering::SeqCst);
        with_webview(self, |wv| {
            wv.focus_parent()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn open_devtools(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.open_devtools();
            Ok(())
        })
    }

    fn close_devtools(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.close_devtools();
            Ok(())
        })
    }

    fn is_devtools_open(&self) -> PyResult<bool> {
        with_webview(self, |wv| Ok(wv.is_devtools_open()))
    }

    fn url(&self) -> PyResult<String> {
        with_webview(self, |wv| {
            wv.url()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    /// Release the native webview and tear down platform resources.
    fn destroy(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            self.web_wants_keyboard.store(false, Ordering::SeqCst);
            self.mac_tk_unfocus.store(false, Ordering::SeqCst);
            self.wakeup_write_fd.store(-1, Ordering::SeqCst);
            if let Ok(mut guard) = self._focus_sync.lock() {
                *guard = None;
            }
        }

        if let Ok(mut ipc) = self.ipc_cb.lock() {
            *ipc = None;
        }
        if let Ok(mut nav) = self.nav_cb.lock() {
            *nav = None;
        }
        if let Ok(mut title) = self.title_cb.lock() {
            *title = None;
        }
        if let Ok(mut newwin) = self.newwin_cb.lock() {
            *newwin = None;
        }
        if let Ok(mut drag) = self.drag_drop_cb.lock() {
            *drag = None;
        }
        if let Ok(mut pending) = self.ipc_pending.lock() {
            pending.clear();
        }
        if let Ok(mut pending) = self.title_pending.lock() {
            pending.clear();
        }
        if let Ok(mut pending) = self.drag_drop_pending.lock() {
            pending.clear();
        }
        if let Ok(mut pending) = self.page_load_pending.lock() {
            pending.clear();
        }

        let mut guard = self
            .inner
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("webview lock poisoned"))?;
        if let Some(wv) = guard.take() {
            let _ = wv.set_visible(false);
        }
        Ok(())
    }
}

fn with_webview<F, T>(this: &WebView, f: F) -> PyResult<T>
where
    F: FnOnce(&wry::WebView) -> PyResult<T>,
{
    this.require_owner_thread()?;
    let guard = this
        .inner
        .lock()
        .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("webview lock poisoned"))?;
    match guard.as_ref() {
        Some(wv) => f(wv),
        None => Err(pyo3::exceptions::PyRuntimeError::new_err(
            "webview already destroyed",
        )),
    }
}

#[pyclass(eq, eq_int, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum NewWindowResponse {
    Allow,
    Deny,
}

#[pyclass(eq, eq_int, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum DragDropEvent {
    Enter,
    Over,
    Drop,
    Leave,
    Unknown,
}

#[pyfunction]
fn pump_events() {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Bound work per Tk tick — WebKitGTK can enqueue continuously and
        // an unbounded drain would hang nested inside Tcl's update().
        for _ in 0..32 {
            if !gtk::main_iteration_do(false) {
                break;
            }
        }
    }
}

#[pyfunction]
fn ensure_gtk_init() {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = gtk::init();
    }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<WebView>()?;
    m.add_class::<PageLoadEvent>()?;
    m.add_class::<NewWindowResponse>()?;
    m.add_class::<DragDropEvent>()?;
    m.add_function(wrap_pyfunction!(pump_events, m)?)?;
    m.add_function(wrap_pyfunction!(ensure_gtk_init, m)?)?;
    Ok(())
}
