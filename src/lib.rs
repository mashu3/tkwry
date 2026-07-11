//! wry bindings for embedding a WebView into a Tkinter host window.

#[cfg(target_os = "macos")]
mod macos_document_url;
#[cfg(target_os = "macos")]
mod macos_focus;
#[cfg(target_os = "macos")]
mod macos_window;

use pyo3::prelude::*;
use std::cell::Cell;
use std::collections::HashMap;
#[cfg(target_os = "macos")]
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

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
const MAX_EVAL_PENDING: usize = 256;
const MAX_SYNC_HOOK_PENDING: usize = 256;

/// Default sync-hook result when no Python handler is registered.
const NAV_SYNC_DEFAULT_MISSING: bool = true;

/// Drag-drop events without a position from the platform (e.g. Leave).
const DRAG_DROP_NO_POSITION: (i32, i32) = (-1, -1);

/// Maximum IPC message size (10 MiB). Messages exceeding this are dropped.
const MAX_IPC_MESSAGE_BYTES: usize = 10 * 1024 * 1024;

/// Navigation/new-window hooks block the WebKit thread until the Tk thread drains
/// them; cap wait time so a stuck handler cannot freeze the page indefinitely.
const SYNC_HOOK_TIMEOUT: Duration = Duration::from_secs(30);

/// ``eval_js_with_callback`` registrations older than this are pruned on drain.
const EVAL_CALLBACK_TIMEOUT: Duration = Duration::from_secs(30);

/// Print a Python exception (with traceback) to stderr from a Rust callback.
fn report_py_error(py: Python<'_>, err: PyErr) {
    err.print(py);
}

fn queue_lock_poisoned() -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err("event queue lock poisoned")
}

fn push_if_listening<T>(
    listening: &AtomicBool,
    pending: &Arc<Mutex<Vec<T>>>,
    dropped: &AtomicU64,
    item: T,
    max: usize,
    label: &str,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    // Fast path: avoid taking the queue lock when clearly not listening.
    if !listening.load(Ordering::SeqCst) {
        dropped.fetch_add(1, Ordering::SeqCst);
        return Ok(());
    }
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: {label} event dropped (event queue lock poisoned)");
            return Err(());
        }
    };
    // Re-check under the queue lock so disable+clear cannot interleave a push
    // (TOCTOU: load(true) → clear → push would otherwise resurrect stale events).
    if !listening.load(Ordering::SeqCst) {
        dropped.fetch_add(1, Ordering::SeqCst);
        return Ok(());
    }
    if queue.len() >= max {
        dropped.fetch_add(1, Ordering::SeqCst);
        queue.remove(0);
        eprintln!("tkwry: dropping oldest {label} event (pending queue full at {max} events)");
    }
    queue.push(item);
    if let Some(fd) = wakeup {
        notify_wakeup(fd);
    }
    Ok(())
}

type EvalResultPending = Arc<Mutex<Vec<(u64, Option<String>)>>>;

fn push_eval_result(pending: &EvalResultPending, dropped: &AtomicU64, token: u64, result: String) {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: eval result dropped (event queue lock poisoned)");
            return;
        }
    };
    if queue.len() >= MAX_EVAL_PENDING {
        dropped.fetch_add(1, Ordering::SeqCst);
        queue.remove(0);
        eprintln!(
            "tkwry: dropping oldest eval result (pending queue full at {MAX_EVAL_PENDING} events)"
        );
        // Queue a dropped sentinel so drain_eval_callbacks can pair with the
        // registered callback without conflating overflow with an empty JS value.
        queue.push((token, None));
        return;
    }
    queue.push((token, Some(result)));
}

/// Wake the Tk main loop (pipe byte; drained by Python ``after`` pump).
fn notify_wakeup(fd: &AtomicI32) {
    let fd = fd.load(Ordering::SeqCst);
    if fd < 0 {
        return;
    }
    let byte = 1u8;
    let wrote = unsafe { libc::write(fd, &byte as *const u8 as *const libc::c_void, 1) };
    if wrote < 0 {
        eprintln!(
            "tkwry: wakeup pipe write failed: {}",
            std::io::Error::last_os_error()
        );
    }
}

struct SyncHookSlot<T> {
    result: Mutex<Option<T>>,
    cvar: Condvar,
    cancelled: AtomicBool,
}

impl<T> SyncHookSlot<T> {
    fn new() -> Self {
        Self {
            result: Mutex::new(None),
            cvar: Condvar::new(),
            cancelled: AtomicBool::new(false),
        }
    }
}

fn wait_sync_hook<T: Copy>(
    slot: &SyncHookSlot<T>,
    timeout: Duration,
    label: &str,
    default: T,
) -> T {
    let mut guard = match slot.result.lock() {
        Ok(guard) => guard,
        Err(_) => {
            eprintln!("tkwry: {label} dropped (sync hook lock poisoned)");
            return default;
        }
    };
    let deadline = Instant::now() + timeout;
    while guard.is_none() {
        if slot.cancelled.load(Ordering::SeqCst) {
            return default;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            slot.cancelled.store(true, Ordering::SeqCst);
            eprintln!("tkwry: {label} timed out after {}s", timeout.as_secs());
            return default;
        }
        let (next, _) = match slot.cvar.wait_timeout(guard, remaining) {
            Ok(pair) => pair,
            Err(_) => {
                eprintln!("tkwry: {label} dropped (sync hook lock poisoned)");
                return default;
            }
        };
        guard = next;
    }
    guard.unwrap_or(default)
}

fn resolve_sync_hook<T: Copy>(slot: &SyncHookSlot<T>, value: T) {
    if let Ok(mut guard) = slot.result.lock() {
        *guard = Some(value);
        slot.cvar.notify_one();
    }
}

fn abort_nav_sync_hooks(pending: &NavSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: navigation sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (_, slot) in requests {
        slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&slot, false);
    }
}

fn abort_newwin_sync_hooks(pending: &NewWinSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: new-window sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (_, slot) in requests {
        slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&slot, NewWindowResponse::Deny);
    }
}

fn enqueue_nav_sync_hook(
    pending: &NavSyncPending,
    url: String,
    slot: Arc<SyncHookSlot<bool>>,
) -> bool {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: navigation hook dropped (queue lock poisoned)");
            return false;
        }
    };
    while queue.len() >= MAX_SYNC_HOOK_PENDING {
        let (_, old_slot) = queue.remove(0);
        old_slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&old_slot, false);
        eprintln!(
            "tkwry: dropping oldest navigation sync hook (queue full at {MAX_SYNC_HOOK_PENDING})"
        );
    }
    queue.push((url, slot));
    true
}

fn enqueue_newwin_sync_hook(
    pending: &NewWinSyncPending,
    url: String,
    slot: Arc<SyncHookSlot<NewWindowResponse>>,
) -> bool {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: new-window hook dropped (queue lock poisoned)");
            return false;
        }
    };
    while queue.len() >= MAX_SYNC_HOOK_PENDING {
        let (_, old_slot) = queue.remove(0);
        old_slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&old_slot, NewWindowResponse::Deny);
        eprintln!(
            "tkwry: dropping oldest new-window sync hook (queue full at {MAX_SYNC_HOOK_PENDING})"
        );
    }
    queue.push((url, slot));
    true
}

fn drain_nav_sync_hooks(nav_cb: &PyCallback, pending: &NavSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: navigation sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (url, slot) in requests {
        if slot.cancelled.load(Ordering::SeqCst) {
            resolve_sync_hook(&slot, false);
            continue;
        }
        let allowed = Python::attach(|py| {
            if let Some(func) = clone_py_callback(py, nav_cb) {
                call_sync_bool_callback(py, &func, url.as_str(), "on_navigation", false)
            } else {
                NAV_SYNC_DEFAULT_MISSING
            }
        });
        resolve_sync_hook(&slot, allowed);
    }
}

fn drain_newwin_sync_hooks(newwin_cb: &PyCallback, pending: &NewWinSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: new-window sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (url, slot) in requests {
        if slot.cancelled.load(Ordering::SeqCst) {
            resolve_sync_hook(&slot, NewWindowResponse::Deny);
            continue;
        }
        let resp = Python::attach(|py| {
            if let Some(func) = clone_py_callback(py, newwin_cb) {
                match func.call1(py, (url.as_str(),)) {
                    Ok(result) => extract_new_window_response(result.bind(py), "on_new_window")
                        .unwrap_or(NewWindowResponse::Deny),
                    Err(err) => {
                        report_py_error(py, err);
                        NewWindowResponse::Deny
                    }
                }
            } else {
                NewWindowResponse::Allow
            }
        });
        resolve_sync_hook(&slot, resp);
    }
}

fn prune_stale_eval_callbacks(
    callbacks: &mut HashMap<u64, EvalCallbackEntry>,
    dropped: &AtomicU64,
) {
    let now = Instant::now();
    callbacks.retain(|_, (_, registered)| {
        if now.duration_since(*registered) > EVAL_CALLBACK_TIMEOUT {
            dropped.fetch_add(1, Ordering::SeqCst);
            false
        } else {
            true
        }
    });
}

fn set_listening_and_clear_queue<T>(
    listening: &AtomicBool,
    pending: &Arc<Mutex<Vec<T>>>,
    enabled: bool,
) -> PyResult<()> {
    // Hold the queue lock across store+clear so push_if_listening cannot insert
    // after clear while still observing a prior true load.
    let mut queue = pending.lock().map_err(|_| queue_lock_poisoned())?;
    listening.store(enabled, Ordering::SeqCst);
    if !enabled {
        queue.clear();
    }
    Ok(())
}

fn drain_queue<T>(pending: &Arc<Mutex<Vec<T>>>) -> PyResult<Vec<T>> {
    pending
        .lock()
        .map(|mut queue| std::mem::take(&mut *queue))
        .map_err(|_| queue_lock_poisoned())
}

fn push_listening_py<T>(
    listening: &AtomicBool,
    pending: &Arc<Mutex<Vec<T>>>,
    dropped: &AtomicU64,
    item: T,
    max: usize,
    label: &str,
) -> PyResult<()> {
    push_if_listening(listening, pending, dropped, item, max, label, None)
        .map_err(|()| queue_lock_poisoned())
}

fn alloc_eval_token(counter: &AtomicU64, callbacks: &mut HashMap<u64, EvalCallbackEntry>) -> u64 {
    loop {
        let token = counter.fetch_add(1, Ordering::SeqCst);
        if token == 0 {
            continue;
        }
        if callbacks.remove(&token).is_some() {
            eprintln!("tkwry: recycled eval token {token} after counter wrap");
        }
        return token;
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

fn normalize_document_url(url: Option<String>) -> Option<String> {
    url.filter(|url| !url.is_empty() && !url.eq_ignore_ascii_case("about:blank"))
}

fn call_sync_bool_callback(
    py: Python<'_>,
    func: &Py<PyAny>,
    url: &str,
    context: &str,
    default_on_error: bool,
) -> bool {
    match func.call1(py, (url,)) {
        Ok(result) => extract_py_bool(result.bind(py), context).unwrap_or(default_on_error),
        Err(err) => {
            report_py_error(py, err);
            default_on_error
        }
    }
}

fn clone_py_callback(py: Python<'_>, cb: &PyCallback) -> Option<Py<PyAny>> {
    cb.lock()
        .ok()
        .and_then(|guard| guard.as_ref().map(|func| func.clone_ref(py)))
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
type EvalCallbackEntry = (Py<PyAny>, Instant);
type EvalCallbackMap = Arc<Mutex<HashMap<u64, EvalCallbackEntry>>>;
type DrainedEvalCallback = (u64, Py<PyAny>, Option<String>);
type NavSyncPending = Arc<Mutex<Vec<(String, Arc<SyncHookSlot<bool>>)>>>;
type NewWinSyncPending = Arc<Mutex<Vec<(String, Arc<SyncHookSlot<NewWindowResponse>>)>>>;

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
    eval_callbacks: EvalCallbackMap,
    eval_result_pending: EvalResultPending,
    eval_next_token: AtomicU64,
    /// When false, async event sources skip queueing (handler cleared).
    page_load_listening: Arc<AtomicBool>,
    ipc_listening: Arc<AtomicBool>,
    title_listening: Arc<AtomicBool>,
    drag_drop_listening: Arc<AtomicBool>,
    ipc_overflow_dropped: Arc<AtomicU64>,
    page_load_overflow_dropped: Arc<AtomicU64>,
    title_overflow_dropped: Arc<AtomicU64>,
    drag_drop_overflow_dropped: Arc<AtomicU64>,
    eval_overflow_dropped: Arc<AtomicU64>,
    nav_sync_pending: NavSyncPending,
    newwin_sync_pending: NewWinSyncPending,
    /// Pipe write fd registered by Python to wake the Tk event loop.
    wakeup_write_fd: Arc<AtomicI32>,
    nav_cb: PyCallback,
    newwin_cb: PyCallback,
    #[cfg(target_os = "macos")]
    _focus_sync: Mutex<Option<macos_focus::FocusSyncGuard>>,
    #[cfg(target_os = "macos")]
    web_wants_keyboard: Arc<AtomicBool>,
    #[cfg(target_os = "macos")]
    mac_tk_unfocus: Arc<AtomicBool>,
    /// Nested wry calls (e.g. sync navigation hooks during ``load_url``).
    wry_call_depth: Cell<u32>,
    /// ``destroy()`` requested while a nested wry call is active.
    destroy_pending: Cell<bool>,
}

impl WebView {
    fn require_owner_thread(&self) -> PyResult<()> {
        let current = python_thread_id()?;
        if current != self.owner_thread {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(THREAD_ERROR));
        }
        Ok(())
    }

    fn enter_wry_call(&self) {
        self.wry_call_depth
            .set(self.wry_call_depth.get().saturating_add(1));
    }

    fn leave_wry_call(&self) -> PyResult<()> {
        let depth = self.wry_call_depth.get();
        debug_assert!(depth > 0);
        self.wry_call_depth.set(depth - 1);
        if depth == 1 && self.destroy_pending.get() {
            self.clear_callbacks_and_queues();
            self.destroy_inner()?;
            self.destroy_pending.set(false);
        }
        Ok(())
    }

    fn clear_callbacks_and_queues(&self) {
        if let Ok(mut nav) = self.nav_cb.lock() {
            *nav = None;
        }
        if let Ok(mut newwin) = self.newwin_cb.lock() {
            *newwin = None;
        }
        if let Ok(mut eval_callbacks) = self.eval_callbacks.lock() {
            eval_callbacks.clear();
        }
        if let Ok(mut eval_results) = self.eval_result_pending.lock() {
            eval_results.clear();
        }
        abort_nav_sync_hooks(&self.nav_sync_pending);
        abort_newwin_sync_hooks(&self.newwin_sync_pending);
        // Destroy teardown: log poison instead of failing destroy.
        for result in [
            set_listening_and_clear_queue(
                &self.page_load_listening,
                &self.page_load_pending,
                false,
            ),
            set_listening_and_clear_queue(&self.ipc_listening, &self.ipc_pending, false),
            set_listening_and_clear_queue(&self.title_listening, &self.title_pending, false),
            set_listening_and_clear_queue(
                &self.drag_drop_listening,
                &self.drag_drop_pending,
                false,
            ),
        ] {
            if let Err(err) = result {
                eprintln!("tkwry: {err}");
            }
        }
    }

    fn destroy_inner(&self) -> PyResult<()> {
        #[cfg(target_os = "macos")]
        {
            self.web_wants_keyboard.store(false, Ordering::SeqCst);
            self.mac_tk_unfocus.store(false, Ordering::SeqCst);
            if let Ok(mut guard) = self._focus_sync.lock() {
                *guard = None;
            }
        }
        self.wakeup_write_fd.store(-1, Ordering::SeqCst);

        let mut guard = self
            .inner
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("webview lock poisoned"))?;
        if let Some(wv) = guard.take() {
            if let Err(err) = wv.set_visible(false) {
                eprintln!("tkwry: set_visible(false) failed during destroy: {err}");
            }
            drop(wv);
        }
        Ok(())
    }

    fn native_is_alive(&self) -> bool {
        self.inner.lock().ok().is_some_and(|guard| guard.is_some())
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
        on_navigation = None,
        on_new_window = None,
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
        on_navigation: Option<Py<PyAny>>,
        on_new_window: Option<Py<PyAny>>,
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
            macos_window::disable_window_tabbing(parent_ns_view)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
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

        let nav_cb: PyCallback = Arc::new(Mutex::new(on_navigation));
        let newwin_cb: PyCallback = Arc::new(Mutex::new(on_new_window));
        let page_load_pending: PageLoadPending = Arc::new(Mutex::new(Vec::new()));
        let ipc_pending: IpcPending = Arc::new(Mutex::new(Vec::new()));
        let title_pending: TitlePending = Arc::new(Mutex::new(Vec::new()));
        let drag_drop_pending: DragDropPending = Arc::new(Mutex::new(Vec::new()));
        let eval_callbacks: EvalCallbackMap = Arc::new(Mutex::new(HashMap::new()));
        let eval_result_pending: EvalResultPending = Arc::new(Mutex::new(Vec::new()));
        // Async queues start disabled; Python enables them when a handler is set.
        let page_load_listening = Arc::new(AtomicBool::new(false));
        let ipc_listening = Arc::new(AtomicBool::new(false));
        let title_listening = Arc::new(AtomicBool::new(false));
        let drag_drop_listening = Arc::new(AtomicBool::new(false));
        let ipc_overflow_dropped = Arc::new(AtomicU64::new(0));
        let page_load_overflow_dropped = Arc::new(AtomicU64::new(0));
        let title_overflow_dropped = Arc::new(AtomicU64::new(0));
        let drag_drop_overflow_dropped = Arc::new(AtomicU64::new(0));
        let eval_overflow_dropped = Arc::new(AtomicU64::new(0));
        let nav_sync_pending: NavSyncPending = Arc::new(Mutex::new(Vec::new()));
        let newwin_sync_pending: NewWinSyncPending = Arc::new(Mutex::new(Vec::new()));
        let wakeup_write_fd = Arc::new(AtomicI32::new(-1));

        let ipc_pending_clone = ipc_pending.clone();
        let ipc_listening_clone = ipc_listening.clone();
        let ipc_overflow_clone = ipc_overflow_dropped.clone();
        let wakeup_for_ipc = wakeup_write_fd.clone();
        let ipc_handler_wry = move |req: wry::http::Request<String>| {
            let body = req.body().clone();
            if body.len() > MAX_IPC_MESSAGE_BYTES {
                ipc_overflow_clone.fetch_add(1, Ordering::SeqCst);
                eprintln!(
                    "tkwry: IPC message dropped ({} bytes exceeds {} byte limit)",
                    body.len(),
                    MAX_IPC_MESSAGE_BYTES
                );
                return;
            }
            let _ = push_if_listening(
                &ipc_listening_clone,
                &ipc_pending_clone,
                &ipc_overflow_clone,
                body,
                MAX_IPC_PENDING,
                "IPC",
                Some(&wakeup_for_ipc),
            );
        };

        let nav_cb_clone = nav_cb.clone();
        let nav_sync_pending_clone = nav_sync_pending.clone();
        let wakeup_fd_clone = wakeup_write_fd.clone();
        let owner_thread_for_nav = owner_thread;
        let nav_handler = move |url: String| -> bool {
            let slot = Arc::new(SyncHookSlot::new());
            if !enqueue_nav_sync_hook(&nav_sync_pending_clone, url, slot.clone()) {
                return false;
            }
            notify_wakeup(&wakeup_fd_clone);
            if Python::attach(|_py| python_thread_id().ok()) == Some(owner_thread_for_nav) {
                drain_nav_sync_hooks(&nav_cb_clone, &nav_sync_pending_clone);
            }
            wait_sync_hook(&slot, SYNC_HOOK_TIMEOUT, "on_navigation", false)
        };

        let page_load_pending_clone = page_load_pending.clone();
        let page_load_listening_clone = page_load_listening.clone();
        let page_load_overflow_clone = page_load_overflow_dropped.clone();
        let wakeup_for_page_load = wakeup_write_fd.clone();
        let pageload_handler = move |event: wry::PageLoadEvent, url: String| {
            let evt = match event {
                wry::PageLoadEvent::Started => PageLoadEvent::Started,
                wry::PageLoadEvent::Finished => PageLoadEvent::Finished,
            };
            let _ = push_if_listening(
                &page_load_listening_clone,
                &page_load_pending_clone,
                &page_load_overflow_clone,
                (evt, url),
                MAX_PAGE_LOAD_PENDING,
                "page-load",
                Some(&wakeup_for_page_load),
            );
        };

        let title_pending_clone = title_pending.clone();
        let title_listening_clone = title_listening.clone();
        let title_overflow_clone = title_overflow_dropped.clone();
        let wakeup_for_title = wakeup_write_fd.clone();
        let title_handler = move |title: String| {
            let _ = push_if_listening(
                &title_listening_clone,
                &title_pending_clone,
                &title_overflow_clone,
                title,
                MAX_TITLE_PENDING,
                "title-changed",
                Some(&wakeup_for_title),
            );
        };

        let newwin_cb_clone = newwin_cb.clone();
        let newwin_sync_pending_clone = newwin_sync_pending.clone();
        let wakeup_fd_for_newwin = wakeup_write_fd.clone();
        let owner_thread_for_newwin = owner_thread;
        let newwin_handler =
            move |url: String, _features: wry::NewWindowFeatures| -> wry::NewWindowResponse {
                let slot = Arc::new(SyncHookSlot::new());
                if !enqueue_newwin_sync_hook(&newwin_sync_pending_clone, url, slot.clone()) {
                    return wry::NewWindowResponse::Deny;
                }
                notify_wakeup(&wakeup_fd_for_newwin);
                if Python::attach(|_py| python_thread_id().ok()) == Some(owner_thread_for_newwin) {
                    drain_newwin_sync_hooks(&newwin_cb_clone, &newwin_sync_pending_clone);
                }
                let resp = wait_sync_hook(
                    &slot,
                    SYNC_HOOK_TIMEOUT,
                    "on_new_window",
                    NewWindowResponse::Deny,
                );
                match resp {
                    NewWindowResponse::Deny => wry::NewWindowResponse::Deny,
                    NewWindowResponse::Allow => wry::NewWindowResponse::Allow,
                }
            };

        let drag_drop_pending_clone = drag_drop_pending.clone();
        let drag_drop_listening_clone = drag_drop_listening.clone();
        let drag_drop_overflow_clone = drag_drop_overflow_dropped.clone();
        let wakeup_for_drag_drop = wakeup_write_fd.clone();
        // Always accept the OS drop. Python receives notify-only events on the
        // Tk thread, so a bool return from the handler cannot gate this path.
        let drag_drop_handler = move |event: wry::DragDropEvent| -> bool {
            let (evt_type, paths, position) = match &event {
                wry::DragDropEvent::Enter { paths, position } => {
                    (DragDropEvent::Enter, paths.clone(), *position)
                }
                wry::DragDropEvent::Over { position } => (DragDropEvent::Over, vec![], *position),
                wry::DragDropEvent::Drop { paths, position } => {
                    (DragDropEvent::Drop, paths.clone(), *position)
                }
                wry::DragDropEvent::Leave => (DragDropEvent::Leave, vec![], DRAG_DROP_NO_POSITION),
                _ => (DragDropEvent::Unknown, vec![], DRAG_DROP_NO_POSITION),
            };
            let paths_str: Vec<String> = paths
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect();
            let pos = (position.0, position.1);
            let _ = push_if_listening(
                &drag_drop_listening_clone,
                &drag_drop_pending_clone,
                &drag_drop_overflow_clone,
                (evt_type, paths_str, pos),
                MAX_DRAG_DROP_PENDING,
                "drag-drop",
                Some(&wakeup_for_drag_drop),
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
            eval_callbacks,
            eval_result_pending,
            eval_next_token: AtomicU64::new(1),
            page_load_listening,
            ipc_listening,
            title_listening,
            drag_drop_listening,
            ipc_overflow_dropped,
            page_load_overflow_dropped,
            title_overflow_dropped,
            drag_drop_overflow_dropped,
            eval_overflow_dropped,
            nav_sync_pending,
            newwin_sync_pending,
            wakeup_write_fd,
            #[cfg(target_os = "macos")]
            _focus_sync,
            #[cfg(target_os = "macos")]
            web_wants_keyboard,
            #[cfg(target_os = "macos")]
            mac_tk_unfocus,
            nav_cb,
            newwin_cb,
            wry_call_depth: Cell::new(0),
            destroy_pending: Cell::new(false),
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

    fn eval_js_with_callback(&self, script: &str, callback: Py<PyAny>) -> PyResult<u64> {
        self.require_owner_thread()?;
        let token = {
            let mut callbacks = self
                .eval_callbacks
                .lock()
                .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
            let token = alloc_eval_token(&self.eval_next_token, &mut callbacks);
            callbacks.insert(token, (callback, Instant::now()));
            token
        };
        let pending = self.eval_result_pending.clone();
        let dropped = self.eval_overflow_dropped.clone();
        let eval_result = with_webview(self, |wv| {
            wv.evaluate_script_with_callback(script, move |result: String| {
                push_eval_result(&pending, &dropped, token, result);
            })
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        });
        if eval_result.is_err() {
            if let Ok(mut callbacks) = self.eval_callbacks.lock() {
                callbacks.remove(&token);
            }
        }
        eval_result?;
        Ok(token)
    }

    fn drain_eval_callbacks(&self) -> PyResult<Vec<DrainedEvalCallback>> {
        self.require_owner_thread()?;
        let items = drain_queue(&self.eval_result_pending)?;
        let mut callbacks = self
            .eval_callbacks
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        prune_stale_eval_callbacks(&mut callbacks, &self.eval_overflow_dropped);
        let mut drained = Vec::with_capacity(items.len());
        for (token, result) in items {
            if let Some((callback, _)) = callbacks.remove(&token) {
                drained.push((token, callback, result));
            } else {
                self.eval_overflow_dropped.fetch_add(1, Ordering::SeqCst);
                eprintln!(
                    "tkwry: eval result dropped (callback expired or missing for token {token})"
                );
            }
        }
        prune_stale_eval_callbacks(&mut callbacks, &self.eval_overflow_dropped);
        Ok(drained)
    }

    fn drain_sync_hooks(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        drain_nav_sync_hooks(&self.nav_cb, &self.nav_sync_pending);
        drain_newwin_sync_hooks(&self.newwin_cb, &self.newwin_sync_pending);
        Ok(())
    }

    fn take_queue_drop_counts(&self) -> PyResult<(u64, u64, u64, u64, u64)> {
        self.require_owner_thread()?;
        Ok((
            self.ipc_overflow_dropped.swap(0, Ordering::SeqCst),
            self.page_load_overflow_dropped.swap(0, Ordering::SeqCst),
            self.title_overflow_dropped.swap(0, Ordering::SeqCst),
            self.drag_drop_overflow_dropped.swap(0, Ordering::SeqCst),
            self.eval_overflow_dropped.swap(0, Ordering::SeqCst),
        ))
    }

    fn set_ipc_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(&self.ipc_listening, &self.ipc_pending, enabled)
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
        abort_nav_sync_hooks(&self.nav_sync_pending);
        Ok(())
    }

    fn set_page_load_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(&self.page_load_listening, &self.page_load_pending, enabled)
    }

    fn drain_page_load_events(&self) -> PyResult<Vec<(PageLoadEvent, String)>> {
        self.require_owner_thread()?;
        drain_queue(&self.page_load_pending)
    }

    fn drain_ipc_messages(&self) -> PyResult<Vec<String>> {
        self.require_owner_thread()?;
        drain_queue(&self.ipc_pending)
    }

    fn drain_title_events(&self) -> PyResult<Vec<String>> {
        self.require_owner_thread()?;
        drain_queue(&self.title_pending)
    }

    fn drain_drag_drop_events(&self) -> PyResult<Vec<DragDropPendingItem>> {
        self.require_owner_thread()?;
        drain_queue(&self.drag_drop_pending)
    }

    fn _enqueue_ipc_message(&self, message: String) -> PyResult<()> {
        self.require_owner_thread()?;
        if message.len() > MAX_IPC_MESSAGE_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "IPC message exceeds {} byte limit",
                MAX_IPC_MESSAGE_BYTES
            )));
        }
        push_listening_py(
            &self.ipc_listening,
            &self.ipc_pending,
            &self.ipc_overflow_dropped,
            message,
            MAX_IPC_PENDING,
            "IPC",
        )
    }

    fn _enqueue_title_event(&self, title: String) -> PyResult<()> {
        self.require_owner_thread()?;
        push_listening_py(
            &self.title_listening,
            &self.title_pending,
            &self.title_overflow_dropped,
            title,
            MAX_TITLE_PENDING,
            "title-changed",
        )
    }

    fn _enqueue_drag_drop_event(
        &self,
        event: DragDropEvent,
        paths: Vec<String>,
        position: (i32, i32),
    ) -> PyResult<()> {
        self.require_owner_thread()?;
        push_listening_py(
            &self.drag_drop_listening,
            &self.drag_drop_pending,
            &self.drag_drop_overflow_dropped,
            (event, paths, position),
            MAX_DRAG_DROP_PENDING,
            "drag-drop",
        )
    }

    fn set_title_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(&self.title_listening, &self.title_pending, enabled)
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
        abort_newwin_sync_hooks(&self.newwin_sync_pending);
        Ok(())
    }

    fn set_drag_drop_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(&self.drag_drop_listening, &self.drag_drop_pending, enabled)
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
        self.wakeup_write_fd.store(fd, Ordering::SeqCst);
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

    fn url(&self) -> PyResult<Option<String>> {
        #[cfg(target_os = "macos")]
        {
            with_webview(self, |wv| match macos_document_url::read_document_url(wv) {
                Ok(url) => Ok(normalize_document_url(url)),
                Err(err) => Err(pyo3::exceptions::PyRuntimeError::new_err(err)),
            })
        }
        #[cfg(not(target_os = "macos"))]
        with_webview(self, |wv| match wv.url() {
            Ok(url) => Ok(normalize_document_url(Some(url))),
            Err(err) => Err(pyo3::exceptions::PyRuntimeError::new_err(err.to_string())),
        })
    }

    /// Release the native webview and tear down platform resources.
    fn destroy(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        if self.wry_call_depth.get() > 0 {
            self.clear_callbacks_and_queues();
            self.destroy_pending.set(true);
            return Ok(());
        }
        self.clear_callbacks_and_queues();
        self.destroy_inner()
    }

    /// ``True`` while the native webview has not been torn down yet.
    fn is_alive(&self) -> bool {
        self.native_is_alive()
    }
}

impl Drop for WebView {
    fn drop(&mut self) {
        self.clear_callbacks_and_queues();
        if let Err(err) = self.destroy_inner() {
            eprintln!("tkwry: WebView drop teardown failed: {err}");
        }
    }
}

fn with_webview<F, T>(this: &WebView, f: F) -> PyResult<T>
where
    F: FnOnce(&wry::WebView) -> PyResult<T>,
{
    this.require_owner_thread()?;
    this.enter_wry_call();
    let result = (|| -> PyResult<T> {
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
    })();
    this.leave_wry_call()?;
    result
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
#[pyo3(signature = (max_iterations=None))]
fn pump_events(max_iterations: Option<usize>) {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        const DEFAULT_ITERATIONS: usize = 128;
        const MAX_ITERATIONS: usize = 512;
        // Bound work per Tk tick — WebKitGTK can enqueue continuously and
        // an unbounded drain would hang nested inside Tcl's update().
        let limit = max_iterations
            .unwrap_or(DEFAULT_ITERATIONS)
            .clamp(1, MAX_ITERATIONS);
        for _ in 0..limit {
            if !gtk::main_iteration_do(false) {
                break;
            }
        }
    }
    #[cfg(not(all(unix, not(target_os = "macos"))))]
    let _ = max_iterations;
}

#[pyfunction]
fn ensure_gtk_init() {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = gtk::init();
    }
}

#[pyfunction]
fn disable_macos_automatic_window_tabbing() -> PyResult<()> {
    #[cfg(target_os = "macos")]
    {
        macos_window::disable_automatic_window_tabbing()
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    }
    Ok(())
}

#[pyfunction]
#[allow(unused_variables)]
fn disable_macos_window_tabbing(parent: usize) -> PyResult<()> {
    #[cfg(target_os = "macos")]
    {
        use objc2_app_kit::NSView;
        let ptr = parent as *mut NSView;
        let Some(parent_ns_view) = NonNull::new(ptr) else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "parent handle is null",
            ));
        };
        macos_window::disable_window_tabbing(parent_ns_view)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    }
    Ok(())
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<WebView>()?;
    m.add_class::<PageLoadEvent>()?;
    m.add_class::<NewWindowResponse>()?;
    m.add_class::<DragDropEvent>()?;
    m.add_function(wrap_pyfunction!(pump_events, m)?)?;
    m.add_function(wrap_pyfunction!(ensure_gtk_init, m)?)?;
    m.add_function(wrap_pyfunction!(disable_macos_automatic_window_tabbing, m)?)?;
    m.add_function(wrap_pyfunction!(disable_macos_window_tabbing, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_eval_result_queues_none_when_pending_full() {
        let pending = Arc::new(Mutex::new(Vec::new()));
        let dropped = AtomicU64::new(0);
        for token in 0..MAX_EVAL_PENDING as u64 {
            push_eval_result(&pending, &dropped, token, format!("r{token}"));
        }
        push_eval_result(&pending, &dropped, 999, "lost".into());
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        let queue = pending.lock().unwrap();
        assert_eq!(queue.len(), MAX_EVAL_PENDING);
        assert_eq!(queue.last(), Some(&(999, None)));
        assert_eq!(queue[MAX_EVAL_PENDING - 1], (999, None));
        assert_eq!(queue[0], (1, Some("r1".to_string())));
    }

    #[test]
    fn push_if_listening_drops_oldest_when_full() {
        let listening = AtomicBool::new(true);
        let pending: Arc<Mutex<Vec<i32>>> = Arc::new(Mutex::new(Vec::new()));
        let dropped = AtomicU64::new(0);
        for value in 0..=4_i32 {
            assert!(
                push_if_listening(&listening, &pending, &dropped, value, 4, "test", None).is_ok()
            );
        }
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert_eq!(*pending.lock().unwrap(), vec![1, 2, 3, 4]);
    }

    #[test]
    fn push_if_listening_counts_drop_when_not_listening() {
        let listening = AtomicBool::new(false);
        let pending: Arc<Mutex<Vec<i32>>> = Arc::new(Mutex::new(Vec::new()));
        let dropped = AtomicU64::new(0);
        assert!(push_if_listening(&listening, &pending, &dropped, 1, 4, "test", None).is_ok());
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert!(pending.lock().unwrap().is_empty());
    }

    #[test]
    fn prune_stale_eval_callbacks_removes_old_entries() {
        Python::initialize();
        let mut callbacks = HashMap::new();
        let dropped = AtomicU64::new(0);
        Python::attach(|py| {
            let cb = py.None().into();
            callbacks.insert(
                1,
                (
                    cb,
                    Instant::now() - EVAL_CALLBACK_TIMEOUT - Duration::from_secs(1),
                ),
            );
            callbacks.insert(2, (py.None().into(), Instant::now()));
        });
        prune_stale_eval_callbacks(&mut callbacks, &dropped);
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert_eq!(callbacks.len(), 1);
        assert!(callbacks.contains_key(&2));
    }
}
