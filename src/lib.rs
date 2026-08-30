//! wry bindings for embedding a WebView into a Tkinter host window.

mod app_protocol;
mod cookie_api;
#[cfg(target_os = "macos")]
mod macos;
mod session;

use pyo3::prelude::*;
use std::cell::Cell;
use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
#[cfg(target_os = "macos")]
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

fn make_rect(x: f64, y: f64, width: f64, height: f64) -> wry::Rect {
    // Tk `winfo_*` on Windows matches the Win32 child coordinate space used by
    // the embed HWND. When the process is DPI-aware those values are physical
    // pixels; wry `Logical` would scale them again and mis-size the WebView.
    // macOS/Linux keep logical coordinates (existing embed math).
    #[cfg(target_os = "windows")]
    {
        wry::Rect {
            position: wry::dpi::Position::Physical(wry::dpi::PhysicalPosition::new(
                x.round() as i32,
                y.round() as i32,
            )),
            size: wry::dpi::Size::Physical(wry::dpi::PhysicalSize::new(
                width.max(1.0).round() as u32,
                height.max(1.0).round() as u32,
            )),
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        wry::Rect {
            position: wry::dpi::Position::Logical(wry::dpi::LogicalPosition::new(x, y)),
            size: wry::dpi::Size::Logical(wry::dpi::LogicalSize::new(width, height)),
        }
    }
}

/// Inverse of [`make_rect`]: values in the same space Tk passes to ``set_bounds``.
fn rect_to_tuple(rect: wry::Rect) -> (f64, f64, f64, f64) {
    use wry::dpi::{Position, Size};
    #[cfg(target_os = "windows")]
    {
        let x = match rect.position {
            Position::Physical(p) => p.x as f64,
            Position::Logical(p) => p.x,
        };
        let y = match rect.position {
            Position::Physical(p) => p.y as f64,
            Position::Logical(p) => p.y,
        };
        let width = match rect.size {
            Size::Physical(s) => s.width as f64,
            Size::Logical(s) => s.width,
        };
        let height = match rect.size {
            Size::Physical(s) => s.height as f64,
            Size::Logical(s) => s.height,
        };
        (x, y, width, height)
    }
    #[cfg(not(target_os = "windows"))]
    {
        let x = match rect.position {
            Position::Logical(p) => p.x,
            Position::Physical(p) => p.x as f64,
        };
        let y = match rect.position {
            Position::Logical(p) => p.y,
            Position::Physical(p) => p.y as f64,
        };
        let width = match rect.size {
            Size::Logical(s) => s.width,
            Size::Physical(s) => s.width as f64,
        };
        let height = match rect.size {
            Size::Logical(s) => s.height,
            Size::Physical(s) => s.height as f64,
        };
        (x, y, width, height)
    }
}

/// Maximum number of buffered async events per channel. When the Tk thread falls
/// behind, queues are compacted where possible before the oldest event is dropped.
const MAX_PAGE_LOAD_PENDING: usize = 2048;
const MAX_IPC_PENDING: usize = 2048;
const MAX_RPC_PENDING: usize = 2048;
const MAX_TITLE_PENDING: usize = 2048;
const MAX_DRAG_DROP_PENDING: usize = 2048;
const MAX_DOWNLOAD_COMPLETE_PENDING: usize = 2048;
const MAX_EVAL_PENDING: usize = 2048;
const MAX_SYNC_HOOK_PENDING: usize = 256;

/// Default sync-hook result when no Python handler is registered.
const NAV_SYNC_DEFAULT_MISSING: bool = true;

/// Drag-drop events without a position from the platform (e.g. Leave).
const DRAG_DROP_NO_POSITION: (i32, i32) = (-1, -1);

/// Maximum IPC / RPC message size (10 MiB). Oversized IPC is dropped; oversized
/// RPC tries to settle with ``RpcMessageTooLarge`` when the request id can be
/// recovered. Keep in sync with ``tkwry.ipc.MAX_RPC_MESSAGE_BYTES``.
const MAX_IPC_MESSAGE_BYTES: usize = 10 * 1024 * 1024;
const MAX_RPC_MESSAGE_BYTES: usize = MAX_IPC_MESSAGE_BYTES;
const RPC_ID_SCAN_BYTES: usize = 8192;

/// Navigation/new-window hooks block the WebKit thread until the Tk thread drains
/// them; cap wait time so a stuck handler cannot freeze the page indefinitely.
const SYNC_HOOK_TIMEOUT: Duration = Duration::from_secs(30);
/// After the Python handler starts, cap execution so a non-returning callback
/// cannot block the WebKit thread forever.
const SYNC_HOOK_HANDLER_TIMEOUT: Duration = Duration::from_secs(30);
/// Hard cap on total wait from enqueue (pre-start + handler combined).
const SYNC_HOOK_MAX_WAIT: Duration =
    Duration::from_secs(SYNC_HOOK_TIMEOUT.as_secs() + SYNC_HOOK_HANDLER_TIMEOUT.as_secs());
const SYNC_HOOK_POLL_INTERVAL: Duration = Duration::from_millis(50);

/// ``eval_js_with_callback`` registrations older than this are pruned on drain.
const EVAL_CALLBACK_TIMEOUT: Duration = Duration::from_secs(30);

/// Print a Python exception (with traceback) to stderr from a Rust callback.
fn report_py_error(py: Python<'_>, err: PyErr) {
    err.print(py);
}

fn queue_lock_poisoned() -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err("event queue lock poisoned")
}

fn make_room_in_queue<T>(
    queue: &mut VecDeque<T>,
    max: usize,
    dropped: &AtomicU64,
    label: &str,
    mut compact: impl FnMut(&mut VecDeque<T>) -> bool,
) {
    while queue.len() >= max {
        let mut compacted = false;
        while compact(queue) {
            compacted = true;
            if queue.len() < max {
                return;
            }
        }
        if compacted {
            continue;
        }
        dropped.fetch_add(1, Ordering::SeqCst);
        queue.pop_front();
        eprintln!("tkwry: dropping oldest {label} event (pending queue full at {max} events)");
        break;
    }
}

#[allow(clippy::too_many_arguments)]
fn push_if_listening<T>(
    listening: &AtomicBool,
    pending: &Arc<Mutex<VecDeque<T>>>,
    dropped: &AtomicU64,
    item: T,
    max: usize,
    label: &str,
    wakeup: Option<&Arc<AtomicI32>>,
    mut compact: impl FnMut(&mut VecDeque<T>) -> bool,
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
    make_room_in_queue(&mut queue, max, dropped, label, &mut compact);
    queue.push_back(item);
    if let Some(fd) = wakeup {
        notify_wakeup(fd);
    }
    Ok(())
}

type EvalResultPending = Arc<Mutex<VecDeque<(u64, Option<String>)>>>;

fn push_eval_result(pending: &EvalResultPending, dropped: &AtomicU64, token: u64, result: String) {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: eval result dropped (event queue lock poisoned)");
            return;
        }
    };
    while queue.len() >= MAX_EVAL_PENDING {
        dropped.fetch_add(1, Ordering::SeqCst);
        let (evicted_token, evicted_result) = queue.pop_front().expect("queue len checked");
        eprintln!(
            "tkwry: dropping oldest eval result (pending queue full at {MAX_EVAL_PENDING} events)"
        );
        if evicted_result.is_some() {
            queue.push_back((evicted_token, None));
        }
    }
    queue.push_back((token, Some(result)));
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
    started: AtomicBool,
    handler_started_at: Mutex<Option<Instant>>,
}

impl<T> SyncHookSlot<T> {
    fn new() -> Self {
        Self {
            result: Mutex::new(None),
            cvar: Condvar::new(),
            cancelled: AtomicBool::new(false),
            started: AtomicBool::new(false),
            handler_started_at: Mutex::new(None),
        }
    }
}

fn mark_sync_hook_started<T>(slot: &SyncHookSlot<T>) {
    slot.started.store(true, Ordering::SeqCst);
    if let Ok(mut started_at) = slot.handler_started_at.lock() {
        *started_at = Some(Instant::now());
    }
}

fn wait_sync_hook<T>(
    slot: &SyncHookSlot<T>,
    timeout: Duration,
    handler_timeout: Duration,
    label: &str,
    default: T,
    wakeup: Option<&Arc<AtomicI32>>,
) -> T {
    let mut guard = match slot.result.lock() {
        Ok(guard) => guard,
        Err(_) => {
            eprintln!("tkwry: {label} dropped (sync hook lock poisoned)");
            return default;
        }
    };
    let enqueued_at = Instant::now();
    let absolute_deadline = enqueued_at + SYNC_HOOK_MAX_WAIT;
    let deadline = enqueued_at + timeout;
    while guard.is_none() {
        if slot.cancelled.load(Ordering::SeqCst) {
            return default;
        }
        if !slot.started.load(Ordering::SeqCst) {
            let remaining = deadline
                .min(absolute_deadline)
                .saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                slot.cancelled.store(true, Ordering::SeqCst);
                eprintln!("tkwry: {label} timed out after {}s", timeout.as_secs());
                return default;
            }
            if let Some(fd) = wakeup {
                notify_wakeup(fd);
            }
            let wait_for = remaining.min(SYNC_HOOK_POLL_INTERVAL);
            let (next, _) = match slot.cvar.wait_timeout(guard, wait_for) {
                Ok(pair) => pair,
                Err(_) => {
                    eprintln!("tkwry: {label} dropped (sync hook lock poisoned)");
                    return default;
                }
            };
            guard = next;
        } else {
            let handler_deadline = slot
                .handler_started_at
                .lock()
                .ok()
                .and_then(|started_at| *started_at)
                .map(|started_at| started_at + handler_timeout)
                .unwrap_or_else(|| Instant::now() + handler_timeout);
            let remaining = handler_deadline
                .min(absolute_deadline)
                .saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                slot.cancelled.store(true, Ordering::SeqCst);
                if Instant::now() >= absolute_deadline {
                    eprintln!(
                        "tkwry: {label} timed out after {}s total",
                        SYNC_HOOK_MAX_WAIT.as_secs()
                    );
                } else {
                    eprintln!(
                        "tkwry: {label} handler timed out after {}s",
                        handler_timeout.as_secs()
                    );
                }
                return default;
            }
            let wait_for = remaining.min(SYNC_HOOK_POLL_INTERVAL);
            let (next, _) = match slot.cvar.wait_timeout(guard, wait_for) {
                Ok(pair) => pair,
                Err(_) => {
                    eprintln!("tkwry: {label} dropped (sync hook lock poisoned)");
                    return default;
                }
            };
            guard = next;
        }
    }
    guard.take().unwrap_or(default)
}

fn resolve_sync_hook<T>(slot: &SyncHookSlot<T>, value: T) {
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
    queue.retain(|(existing_url, old_slot)| {
        if existing_url == &url {
            old_slot.cancelled.store(true, Ordering::SeqCst);
            resolve_sync_hook(old_slot, false);
            false
        } else {
            true
        }
    });
    if queue.len() >= MAX_SYNC_HOOK_PENDING {
        eprintln!("tkwry: rejecting navigation sync hook (queue full at {MAX_SYNC_HOOK_PENDING})");
        return false;
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
    queue.retain(|(existing_url, old_slot)| {
        if existing_url == &url {
            old_slot.cancelled.store(true, Ordering::SeqCst);
            resolve_sync_hook(old_slot, NewWindowResponse::Deny);
            false
        } else {
            true
        }
    });
    if queue.len() >= MAX_SYNC_HOOK_PENDING {
        eprintln!("tkwry: rejecting new-window sync hook (queue full at {MAX_SYNC_HOOK_PENDING})");
        return false;
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
        mark_sync_hook_started(&slot);
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
        mark_sync_hook_started(&slot);
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

fn abort_download_sync_hooks(pending: &DownloadSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: download sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (_, _, slot) in requests {
        slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&slot, download_deny());
    }
}

fn enqueue_download_sync_hook(
    pending: &DownloadSyncPending,
    url: String,
    dest: String,
    slot: Arc<SyncHookSlot<DownloadStartResult>>,
) -> bool {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: download hook dropped (queue lock poisoned)");
            return false;
        }
    };
    if queue.len() >= MAX_SYNC_HOOK_PENDING {
        eprintln!("tkwry: rejecting download sync hook (queue full at {MAX_SYNC_HOOK_PENDING})");
        return false;
    }
    queue.push((url, dest, slot));
    true
}

fn extract_download_start_result(
    result: &Bound<'_, PyAny>,
    context: &str,
) -> Option<DownloadStartResult> {
    match result.extract::<(bool, Option<String>)>() {
        Ok((allow, dest)) => Some(DownloadStartResult { allow, dest }),
        Err(err) => {
            eprintln!("tkwry: {context}: callback must return (bool, str | None) ({err})");
            None
        }
    }
}

fn drain_download_sync_hooks(download_cb: &PyCallback, pending: &DownloadSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: download sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (url, dest, slot) in requests {
        if slot.cancelled.load(Ordering::SeqCst) {
            resolve_sync_hook(&slot, download_deny());
            continue;
        }
        mark_sync_hook_started(&slot);
        let decision = Python::attach(|py| {
            if let Some(func) = clone_py_callback(py, download_cb) {
                match func.call1(py, (url.as_str(), dest.as_str())) {
                    Ok(result) => extract_download_start_result(result.bind(py), "on_download")
                        .unwrap_or_else(download_deny),
                    Err(err) => {
                        report_py_error(py, err);
                        download_deny()
                    }
                }
            } else {
                download_allow_suggested()
            }
        });
        resolve_sync_hook(&slot, decision);
    }
}

fn push_download_complete_event(
    listening: &AtomicBool,
    pending: &DownloadCompletePending,
    dropped: &AtomicU64,
    item: DownloadCompletePendingItem,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    push_if_listening(
        listening,
        pending,
        dropped,
        item,
        MAX_DOWNLOAD_COMPLETE_PENDING,
        "download-complete",
        wakeup,
        |_| false,
    )
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
    pending: &Arc<Mutex<VecDeque<T>>>,
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

fn drain_queue<T>(pending: &Arc<Mutex<VecDeque<T>>>) -> PyResult<Vec<T>> {
    pending
        .lock()
        .map(|mut queue| queue.drain(..).collect())
        .map_err(|_| queue_lock_poisoned())
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

fn extract_permission_response(
    result: &Bound<'_, PyAny>,
    context: &str,
) -> Option<PermissionResponse> {
    match result.extract::<PermissionResponse>() {
        Ok(value) => Some(value),
        Err(err) => {
            eprintln!("tkwry: {context}: callback must return PermissionResponse ({err})");
            None
        }
    }
}

fn permission_kind_from_wry(kind: wry::PermissionKind) -> PermissionKind {
    match kind {
        wry::PermissionKind::Microphone => PermissionKind::Microphone,
        wry::PermissionKind::Camera => PermissionKind::Camera,
        wry::PermissionKind::Geolocation => PermissionKind::Geolocation,
        wry::PermissionKind::Notifications => PermissionKind::Notifications,
        wry::PermissionKind::ClipboardRead => PermissionKind::ClipboardRead,
        wry::PermissionKind::DisplayCapture => PermissionKind::DisplayCapture,
        wry::PermissionKind::Midi => PermissionKind::Midi,
        wry::PermissionKind::Sensors => PermissionKind::Sensors,
        wry::PermissionKind::MediaKeySystemAccess => PermissionKind::MediaKeySystemAccess,
        wry::PermissionKind::LocalFonts => PermissionKind::LocalFonts,
        wry::PermissionKind::WindowManagement => PermissionKind::WindowManagement,
        wry::PermissionKind::PointerLock => PermissionKind::PointerLock,
        wry::PermissionKind::AutomaticDownloads => PermissionKind::AutomaticDownloads,
        wry::PermissionKind::FileSystemAccess => PermissionKind::FileSystemAccess,
        wry::PermissionKind::Autoplay => PermissionKind::Autoplay,
        _ => PermissionKind::Other,
    }
}

fn permission_response_to_wry(resp: PermissionResponse) -> wry::PermissionResponse {
    match resp {
        PermissionResponse::Allow => wry::PermissionResponse::Allow,
        PermissionResponse::Deny => wry::PermissionResponse::Deny,
        PermissionResponse::Default => wry::PermissionResponse::Default,
    }
}

fn abort_permission_sync_hooks(pending: &PermissionSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: permission sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (_, slot) in requests {
        slot.cancelled.store(true, Ordering::SeqCst);
        resolve_sync_hook(&slot, PermissionResponse::Deny);
    }
}

fn enqueue_permission_sync_hook(
    pending: &PermissionSyncPending,
    kind: PermissionKind,
    slot: Arc<SyncHookSlot<PermissionResponse>>,
) -> bool {
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: permission hook dropped (queue lock poisoned)");
            return false;
        }
    };
    // Coalesce duplicate kinds: cancel the older pending request.
    queue.retain(|(existing_kind, old_slot)| {
        if *existing_kind == kind {
            old_slot.cancelled.store(true, Ordering::SeqCst);
            resolve_sync_hook(old_slot, PermissionResponse::Deny);
            false
        } else {
            true
        }
    });
    if queue.len() >= MAX_SYNC_HOOK_PENDING {
        eprintln!("tkwry: rejecting permission sync hook (queue full at {MAX_SYNC_HOOK_PENDING})");
        return false;
    }
    queue.push((kind, slot));
    true
}

fn drain_permission_sync_hooks(permission_cb: &PyCallback, pending: &PermissionSyncPending) {
    let requests = match pending.lock() {
        Ok(mut queue) => std::mem::take(&mut *queue),
        Err(_) => {
            eprintln!("tkwry: permission sync hook queue dropped (lock poisoned)");
            return;
        }
    };
    for (kind, slot) in requests {
        if slot.cancelled.load(Ordering::SeqCst) {
            resolve_sync_hook(&slot, PermissionResponse::Deny);
            continue;
        }
        mark_sync_hook_started(&slot);
        let resp = Python::attach(|py| {
            if let Some(func) = clone_py_callback(py, permission_cb) {
                match func.call1(py, (kind,)) {
                    Ok(result) => {
                        extract_permission_response(result.bind(py), "permission_handler")
                            .unwrap_or(PermissionResponse::Deny)
                    }
                    Err(err) => {
                        report_py_error(py, err);
                        PermissionResponse::Deny
                    }
                }
            } else {
                PermissionResponse::Default
            }
        });
        resolve_sync_hook(&slot, resp);
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

#[pyclass(eq, eq_int, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum NewWindowResponse {
    Allow,
    Deny,
}

/// Permission kinds requested by the page (wry ``PermissionKind``).
///
/// Engine coverage varies; see docs. ``Other`` covers unrecognized kinds.
#[pyclass(eq, eq_int, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum PermissionKind {
    Microphone,
    Camera,
    Geolocation,
    Notifications,
    ClipboardRead,
    DisplayCapture,
    Midi,
    Sensors,
    MediaKeySystemAccess,
    LocalFonts,
    WindowManagement,
    PointerLock,
    AutomaticDownloads,
    FileSystemAccess,
    Autoplay,
    Other,
}

/// Response for ``permission_handler`` (wry ``PermissionResponse``).
#[pyclass(eq, eq_int, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Default)]
enum PermissionResponse {
    Allow,
    Deny,
    #[default]
    Default,
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

type PyCallback = Arc<Mutex<Option<Py<PyAny>>>>;
type PageLoadPending = Arc<Mutex<VecDeque<(PageLoadEvent, String)>>>;
type IpcEnvelope = (String, String); // (source_url, body)
type IpcPending = Arc<Mutex<VecDeque<IpcEnvelope>>>;
type TitlePending = Arc<Mutex<VecDeque<String>>>;
type DragDropPendingItem = (DragDropEvent, Vec<String>, (i32, i32));
type DragDropPending = Arc<Mutex<VecDeque<DragDropPendingItem>>>;
type EvalCallbackEntry = (Py<PyAny>, Instant);
type EvalCallbackMap = Arc<Mutex<HashMap<u64, EvalCallbackEntry>>>;
type DrainedEvalCallback = (u64, Py<PyAny>, Option<String>);
type NavSyncPending = Arc<Mutex<Vec<(String, Arc<SyncHookSlot<bool>>)>>>;
type NewWinSyncPending = Arc<Mutex<Vec<(String, Arc<SyncHookSlot<NewWindowResponse>>)>>>;
type PermissionSyncPending =
    Arc<Mutex<Vec<(PermissionKind, Arc<SyncHookSlot<PermissionResponse>>)>>>;
type DownloadSyncPending =
    Arc<Mutex<Vec<(String, String, Arc<SyncHookSlot<DownloadStartResult>>)>>>;
type DownloadCompletePendingItem = (String, Option<String>, bool);
type DownloadCompletePending = Arc<Mutex<VecDeque<DownloadCompletePendingItem>>>;

#[derive(Clone)]
struct DownloadStartResult {
    allow: bool,
    dest: Option<String>,
}

fn download_deny() -> DownloadStartResult {
    DownloadStartResult {
        allow: false,
        dest: None,
    }
}

fn download_allow_suggested() -> DownloadStartResult {
    DownloadStartResult {
        allow: true,
        dest: None,
    }
}

fn try_compact_title_queue(queue: &mut VecDeque<String>) -> bool {
    for index in 0..queue.len().saturating_sub(1) {
        if queue[index] == queue[index + 1] {
            queue.remove(index);
            return true;
        }
    }
    false
}

fn try_compact_ipc_queue(queue: &mut VecDeque<IpcEnvelope>) -> bool {
    for index in 0..queue.len().saturating_sub(1) {
        if queue[index] == queue[index + 1] {
            queue.remove(index);
            return true;
        }
    }
    false
}

/// Schemes that must never navigate, even without a Python ``on_navigation``.
///
/// ``data:`` is intentionally *not* listed: WebView2 implements ``html=`` /
/// ``load_html`` via ``NavigateToString``, which surfaces as a ``data:``
/// navigation. Blocking it leaves the view on the initial ``about:blank``
/// (created before init/IPC scripts) so ``window.ipc`` / ``window.tkwry``
/// never appear. ``app=`` / ``untrusted=True`` still reject ``data:`` in
/// Python navigation policy.
fn is_dangerous_nav_url(url: &str) -> bool {
    let trimmed = url.trim();
    let Some((scheme, _)) = trimmed.split_once(':') else {
        return false;
    };
    matches!(
        scheme.to_ascii_lowercase().as_str(),
        "javascript" | "vbscript" | "blob" | "mailto"
    )
}

/// True when *body* looks like a tkwry RPC envelope (``{"__tkwry":"rpc",...}``).
///
/// Used only to pick the dedicated RPC queue so IPC overflow cannot drop
/// in-flight ``window.tkwry.call`` requests. Python still parses the envelope.
fn is_rpc_envelope(body: &str) -> bool {
    let s = body.trim_start();
    if !s.starts_with('{') {
        return false;
    }
    let Some(key_at) = s.find("\"__tkwry\"") else {
        return false;
    };
    let after_key = &s[key_at + "\"__tkwry\"".len()..];
    let after_colon = after_key.trim_start();
    let Some(rest) = after_colon.strip_prefix(':') else {
        return false;
    };
    rest.trim_start().starts_with("\"rpc\"")
}

fn json_escape_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", u32::from(c))),
            c => out.push(c),
        }
    }
    out
}

fn scan_prefix(body: &str, max_bytes: usize) -> &str {
    if body.len() <= max_bytes {
        return body;
    }
    let mut end = max_bytes;
    while end > 0 && !body.is_char_boundary(end) {
        end -= 1;
    }
    &body[..end]
}

/// Scan the start of an RPC envelope for ``"id": "..."`` without parsing JSON.
fn extract_rpc_request_id(body: &str) -> Option<&str> {
    let head = scan_prefix(body, RPC_ID_SCAN_BYTES);
    let mut rest = head;
    while let Some(at) = rest.find("\"id\"") {
        let after_key = rest[at + 4..].trim_start();
        let Some(after_colon) = after_key.strip_prefix(':').map(str::trim_start) else {
            rest = &rest[at + 4..];
            continue;
        };
        let Some(after_quote) = after_colon.strip_prefix('"') else {
            rest = &rest[at + 4..];
            continue;
        };
        let end = after_quote.find('"')?;
        let id = &after_quote[..end];
        if !id.is_empty() {
            return Some(id);
        }
        rest = &rest[at + 4..];
    }
    None
}

fn rpc_reject_envelope(id: &str, type_name: &str, message: &str) -> String {
    format!(
        r#"{{"__tkwry":"rpc","id":"{}","__tkwry_reject":"{}","message":"{}"}}"#,
        json_escape_string(id),
        json_escape_string(type_name),
        json_escape_string(message),
    )
}

/// Apply per-message size limits, then queue IPC/RPC bodies.
///
/// Oversized RPC is replaced with a small ``RpcMessageTooLarge`` envelope when
/// the request id can be recovered so the JS Promise can reject.
#[allow(clippy::too_many_arguments)]
fn enqueue_window_ipc_body(
    listening: &AtomicBool,
    ipc_pending: &IpcPending,
    ipc_dropped: &AtomicU64,
    rpc_pending: &IpcPending,
    rpc_dropped: &AtomicU64,
    body: String,
    source_url: String,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    let rpc = is_rpc_envelope(&body);
    let limit = if rpc {
        MAX_RPC_MESSAGE_BYTES
    } else {
        MAX_IPC_MESSAGE_BYTES
    };
    if body.len() > limit {
        if rpc {
            if let Some(id) = extract_rpc_request_id(&body) {
                let envelope = rpc_reject_envelope(
                    id,
                    "RpcMessageTooLarge",
                    &format!(
                        "RPC message exceeds {limit} byte limit ({} bytes)",
                        body.len()
                    ),
                );
                return push_window_ipc_body(
                    listening,
                    ipc_pending,
                    ipc_dropped,
                    rpc_pending,
                    rpc_dropped,
                    envelope,
                    source_url,
                    wakeup,
                );
            }
        }
        let dropped = if rpc { rpc_dropped } else { ipc_dropped };
        let label = if rpc { "RPC" } else { "IPC" };
        dropped.fetch_add(1, Ordering::SeqCst);
        eprintln!(
            "tkwry: {label} message dropped ({} bytes exceeds {limit} byte limit)",
            body.len()
        );
        return Ok(());
    }
    push_window_ipc_body(
        listening,
        ipc_pending,
        ipc_dropped,
        rpc_pending,
        rpc_dropped,
        body,
        source_url,
        wakeup,
    )
}

#[allow(clippy::too_many_arguments)]
fn push_window_ipc_body(
    listening: &AtomicBool,
    ipc_pending: &IpcPending,
    ipc_dropped: &AtomicU64,
    rpc_pending: &IpcPending,
    rpc_dropped: &AtomicU64,
    body: String,
    source_url: String,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    let envelope = (source_url, body);
    if is_rpc_envelope(&envelope.1) {
        push_if_listening(
            listening,
            rpc_pending,
            rpc_dropped,
            envelope,
            MAX_RPC_PENDING,
            "RPC",
            wakeup,
            |_: &mut VecDeque<IpcEnvelope>| false,
        )
    } else {
        push_if_listening(
            listening,
            ipc_pending,
            ipc_dropped,
            envelope,
            MAX_IPC_PENDING,
            "IPC",
            wakeup,
            try_compact_ipc_queue,
        )
    }
}

fn try_compact_page_load_queue(queue: &mut VecDeque<(PageLoadEvent, String)>) -> bool {
    if queue.len() >= 2
        && matches!(queue[0], (PageLoadEvent::Started, _))
        && matches!(queue[1], (PageLoadEvent::Started, _))
    {
        queue.pop_front();
        return true;
    }
    if !queue.is_empty() && matches!(queue[0], (PageLoadEvent::Finished, _)) {
        queue.pop_front();
        return true;
    }
    for index in 0..queue.len().saturating_sub(1) {
        if matches!(queue[index], (PageLoadEvent::Started, _))
            && matches!(queue[index + 1], (PageLoadEvent::Finished, _))
            && queue[index].1 == queue[index + 1].1
        {
            queue.remove(index);
            return true;
        }
    }
    false
}

fn try_compact_drag_drop_queue(queue: &mut VecDeque<DragDropPendingItem>) -> bool {
    for index in 0..queue.len().saturating_sub(1) {
        if matches!(queue[index].0, DragDropEvent::Over)
            && matches!(queue[index + 1].0, DragDropEvent::Over)
            && queue[index].1 == queue[index + 1].1
        {
            queue.remove(index);
            return true;
        }
    }
    false
}

fn push_title_event(
    listening: &AtomicBool,
    pending: &TitlePending,
    dropped: &AtomicU64,
    item: String,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    if !listening.load(Ordering::SeqCst) {
        dropped.fetch_add(1, Ordering::SeqCst);
        return Ok(());
    }
    let mut queue = match pending.lock() {
        Ok(queue) => queue,
        Err(_) => {
            eprintln!("tkwry: title-changed event dropped (event queue lock poisoned)");
            return Err(());
        }
    };
    if !listening.load(Ordering::SeqCst) {
        dropped.fetch_add(1, Ordering::SeqCst);
        return Ok(());
    }
    if queue.back() == Some(&item) {
        if let Some(fd) = wakeup {
            notify_wakeup(fd);
        }
        return Ok(());
    }
    make_room_in_queue(
        &mut queue,
        MAX_TITLE_PENDING,
        dropped,
        "title-changed",
        try_compact_title_queue,
    );
    queue.push_back(item);
    if let Some(fd) = wakeup {
        notify_wakeup(fd);
    }
    Ok(())
}

fn push_page_load_event(
    listening: &AtomicBool,
    pending: &PageLoadPending,
    dropped: &AtomicU64,
    item: (PageLoadEvent, String),
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    push_if_listening(
        listening,
        pending,
        dropped,
        item,
        MAX_PAGE_LOAD_PENDING,
        "page-load",
        wakeup,
        try_compact_page_load_queue,
    )
}

fn push_drag_drop_event(
    listening: &AtomicBool,
    pending: &DragDropPending,
    dropped: &AtomicU64,
    item: DragDropPendingItem,
    wakeup: Option<&Arc<AtomicI32>>,
) -> Result<(), ()> {
    push_if_listening(
        listening,
        pending,
        dropped,
        item,
        MAX_DRAG_DROP_PENDING,
        "drag-drop",
        wakeup,
        try_compact_drag_drop_queue,
    )
}

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
    rpc_pending: IpcPending,
    title_pending: TitlePending,
    drag_drop_pending: DragDropPending,
    download_complete_pending: DownloadCompletePending,
    eval_callbacks: EvalCallbackMap,
    eval_result_pending: EvalResultPending,
    eval_next_token: AtomicU64,
    /// When false, async event sources skip queueing (handler cleared).
    page_load_listening: Arc<AtomicBool>,
    ipc_listening: Arc<AtomicBool>,
    title_listening: Arc<AtomicBool>,
    drag_drop_listening: Arc<AtomicBool>,
    download_complete_listening: Arc<AtomicBool>,
    ipc_overflow_dropped: Arc<AtomicU64>,
    rpc_overflow_dropped: Arc<AtomicU64>,
    page_load_overflow_dropped: Arc<AtomicU64>,
    title_overflow_dropped: Arc<AtomicU64>,
    drag_drop_overflow_dropped: Arc<AtomicU64>,
    download_complete_overflow_dropped: Arc<AtomicU64>,
    eval_overflow_dropped: Arc<AtomicU64>,
    nav_sync_pending: NavSyncPending,
    newwin_sync_pending: NewWinSyncPending,
    permission_sync_pending: PermissionSyncPending,
    download_sync_pending: DownloadSyncPending,
    /// Pipe write fd registered by Python to wake the Tk event loop.
    wakeup_write_fd: Arc<AtomicI32>,
    nav_cb: PyCallback,
    newwin_cb: PyCallback,
    permission_cb: PyCallback,
    download_cb: PyCallback,
    #[cfg(target_os = "macos")]
    mac: macos::MacPlatformState,
    /// Nested wry calls (e.g. sync navigation hooks during ``load_url``).
    wry_call_depth: Cell<u32>,
    /// ``destroy()`` requested while a nested wry call is active.
    destroy_pending: Cell<bool>,
    /// Keeps the shared ``WebContext`` alive (custom protocol / profile).
    #[allow(dead_code)]
    session: Option<Arc<Mutex<session::WebSessionState>>>,
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
        if let Ok(mut permission) = self.permission_cb.lock() {
            *permission = None;
        }
        if let Ok(mut download) = self.download_cb.lock() {
            *download = None;
        }
        if let Ok(mut eval_callbacks) = self.eval_callbacks.lock() {
            eval_callbacks.clear();
        }
        if let Ok(mut eval_results) = self.eval_result_pending.lock() {
            eval_results.clear();
        }
        abort_nav_sync_hooks(&self.nav_sync_pending);
        abort_newwin_sync_hooks(&self.newwin_sync_pending);
        abort_permission_sync_hooks(&self.permission_sync_pending);
        abort_download_sync_hooks(&self.download_sync_pending);
        // Destroy teardown: log poison instead of failing destroy.
        for result in [
            set_listening_and_clear_queue(
                &self.page_load_listening,
                &self.page_load_pending,
                false,
            ),
            set_listening_and_clear_queue(&self.ipc_listening, &self.ipc_pending, false),
            set_listening_and_clear_queue(&self.ipc_listening, &self.rpc_pending, false),
            set_listening_and_clear_queue(&self.title_listening, &self.title_pending, false),
            set_listening_and_clear_queue(
                &self.drag_drop_listening,
                &self.drag_drop_pending,
                false,
            ),
            set_listening_and_clear_queue(
                &self.download_complete_listening,
                &self.download_complete_pending,
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
        self.mac.teardown();
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
        app_root = None,
        spa_fallback = false,
        app_cache_control = None,
        app_csp = None,
        app_coop = false,
        app_corp = false,
        visible = true,
        devtools = false,
        clipboard = false,
        javascript_enabled = true,
        autoplay = true,
        hotkeys_zoom = false,
        back_forward_gestures = false,
        default_context_menus = true,
        focused = true,
        background_color = None,
        user_agent = None,
        initialization_script = None,
        on_navigation = None,
        on_new_window = None,
        on_permission = None,
        page_load_listening = false,
        ipc_listening = false,
        title_listening = false,
        drag_drop_listening = false,
        on_download_started = None,
        download_complete_listening = false,
        with_ipc = true,
        session = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        parent: isize,
        owner_thread: Option<u64>,
        width: u32,
        height: u32,
        url: Option<String>,
        html: Option<String>,
        app_root: Option<String>,
        spa_fallback: bool,
        app_cache_control: Option<String>,
        app_csp: Option<String>,
        app_coop: bool,
        app_corp: bool,
        visible: bool,
        devtools: bool,
        clipboard: bool,
        javascript_enabled: bool,
        autoplay: bool,
        hotkeys_zoom: bool,
        back_forward_gestures: bool,
        default_context_menus: bool,
        focused: bool,
        background_color: Option<(u8, u8, u8, u8)>,
        user_agent: Option<String>,
        initialization_script: Option<String>,
        on_navigation: Option<Py<PyAny>>,
        on_new_window: Option<Py<PyAny>>,
        on_permission: Option<Py<PyAny>>,
        page_load_listening: bool,
        ipc_listening: bool,
        title_listening: bool,
        drag_drop_listening: bool,
        on_download_started: Option<Py<PyAny>>,
        download_complete_listening: bool,
        with_ipc: bool,
        session: Option<Bound<'_, session::WebSession>>,
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
            macos::disable_window_tabbing(parent_ns_view)
                .map_err(|err| {
                    eprintln!("tkwry: disable_window_tabbing failed at create (will retry): {err}");
                })
                .ok();
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
        let permission_cb: PyCallback = Arc::new(Mutex::new(on_permission));
        let download_cb: PyCallback = Arc::new(Mutex::new(on_download_started));
        let page_load_pending: PageLoadPending = Arc::new(Mutex::new(VecDeque::new()));
        let ipc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let rpc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let title_pending: TitlePending = Arc::new(Mutex::new(VecDeque::new()));
        let drag_drop_pending: DragDropPending = Arc::new(Mutex::new(VecDeque::new()));
        let download_complete_pending: DownloadCompletePending =
            Arc::new(Mutex::new(VecDeque::new()));
        let eval_callbacks: EvalCallbackMap = Arc::new(Mutex::new(HashMap::new()));
        let eval_result_pending: EvalResultPending = Arc::new(Mutex::new(VecDeque::new()));
        // Async queues start disabled unless Python requests them at create.
        let page_load_listening = Arc::new(AtomicBool::new(page_load_listening));
        let ipc_listening = Arc::new(AtomicBool::new(ipc_listening));
        let title_listening = Arc::new(AtomicBool::new(title_listening));
        let drag_drop_listening = Arc::new(AtomicBool::new(drag_drop_listening));
        let download_complete_listening = Arc::new(AtomicBool::new(download_complete_listening));
        let ipc_overflow_dropped = Arc::new(AtomicU64::new(0));
        let rpc_overflow_dropped = Arc::new(AtomicU64::new(0));
        let page_load_overflow_dropped = Arc::new(AtomicU64::new(0));
        let title_overflow_dropped = Arc::new(AtomicU64::new(0));
        let drag_drop_overflow_dropped = Arc::new(AtomicU64::new(0));
        let download_complete_overflow_dropped = Arc::new(AtomicU64::new(0));
        let eval_overflow_dropped = Arc::new(AtomicU64::new(0));
        let nav_sync_pending: NavSyncPending = Arc::new(Mutex::new(Vec::new()));
        let newwin_sync_pending: NewWinSyncPending = Arc::new(Mutex::new(Vec::new()));
        let permission_sync_pending: PermissionSyncPending = Arc::new(Mutex::new(Vec::new()));
        let download_sync_pending: DownloadSyncPending = Arc::new(Mutex::new(Vec::new()));
        let wakeup_write_fd = Arc::new(AtomicI32::new(-1));

        let nav_cb_clone = nav_cb.clone();
        let nav_sync_pending_clone = nav_sync_pending.clone();
        let wakeup_fd_clone = wakeup_write_fd.clone();
        let owner_thread_for_nav = owner_thread;
        let nav_handler = move |url: String| -> bool {
            if is_dangerous_nav_url(&url) {
                return false;
            }
            let slot = Arc::new(SyncHookSlot::new());
            if !enqueue_nav_sync_hook(&nav_sync_pending_clone, url, slot.clone()) {
                return false;
            }
            notify_wakeup(&wakeup_fd_clone);
            if Python::attach(|_py| python_thread_id().ok()) == Some(owner_thread_for_nav) {
                drain_nav_sync_hooks(&nav_cb_clone, &nav_sync_pending_clone);
            }
            wait_sync_hook(
                &slot,
                SYNC_HOOK_TIMEOUT,
                SYNC_HOOK_HANDLER_TIMEOUT,
                "on_navigation",
                false,
                Some(&wakeup_fd_clone),
            )
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
            let _ = push_page_load_event(
                &page_load_listening_clone,
                &page_load_pending_clone,
                &page_load_overflow_clone,
                (evt, url),
                Some(&wakeup_for_page_load),
            );
        };

        let title_pending_clone = title_pending.clone();
        let title_listening_clone = title_listening.clone();
        let title_overflow_clone = title_overflow_dropped.clone();
        let wakeup_for_title = wakeup_write_fd.clone();
        let title_handler = move |title: String| {
            let _ = push_title_event(
                &title_listening_clone,
                &title_pending_clone,
                &title_overflow_clone,
                title,
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
                    SYNC_HOOK_HANDLER_TIMEOUT,
                    "on_new_window",
                    NewWindowResponse::Deny,
                    Some(&wakeup_fd_for_newwin),
                );
                match resp {
                    NewWindowResponse::Deny => wry::NewWindowResponse::Deny,
                    NewWindowResponse::Allow => wry::NewWindowResponse::Allow,
                }
            };

        let has_permission_handler = permission_cb
            .lock()
            .map(|guard| guard.is_some())
            .unwrap_or(false);

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
            let _ = push_drag_drop_event(
                &drag_drop_listening_clone,
                &drag_drop_pending_clone,
                &drag_drop_overflow_clone,
                (evt_type, paths_str, pos),
                Some(&wakeup_for_drag_drop),
            );
            true
        };

        let download_cb_clone = download_cb.clone();
        let download_sync_pending_clone = download_sync_pending.clone();
        let wakeup_fd_for_download = wakeup_write_fd.clone();
        let owner_thread_for_download = owner_thread;
        let download_started_handler = move |url: String, dest: &mut PathBuf| -> bool {
            let has_cb = download_cb_clone
                .lock()
                .map(|guard| guard.is_some())
                .unwrap_or(false);
            if !has_cb {
                return true;
            }
            let suggested = dest.to_string_lossy().to_string();
            let slot = Arc::new(SyncHookSlot::new());
            if !enqueue_download_sync_hook(
                &download_sync_pending_clone,
                url,
                suggested,
                slot.clone(),
            ) {
                return false;
            }
            notify_wakeup(&wakeup_fd_for_download);
            if Python::attach(|_py| python_thread_id().ok()) == Some(owner_thread_for_download) {
                drain_download_sync_hooks(&download_cb_clone, &download_sync_pending_clone);
            }
            let decision = wait_sync_hook(
                &slot,
                SYNC_HOOK_TIMEOUT,
                SYNC_HOOK_HANDLER_TIMEOUT,
                "on_download",
                download_deny(),
                Some(&wakeup_fd_for_download),
            );
            if !decision.allow {
                return false;
            }
            if let Some(path) = decision.dest {
                let override_dest = PathBuf::from(&path);
                if !override_dest.is_absolute() {
                    eprintln!("tkwry: on_download dest must be an absolute path; denying");
                    return false;
                }
                *dest = override_dest;
            }
            true
        };

        let download_complete_pending_clone = download_complete_pending.clone();
        let download_complete_listening_clone = download_complete_listening.clone();
        let download_complete_overflow_clone = download_complete_overflow_dropped.clone();
        let wakeup_for_download_complete = wakeup_write_fd.clone();
        let download_completed_handler =
            move |url: String, path: Option<PathBuf>, success: bool| {
                let dest = path.map(|p| p.to_string_lossy().to_string());
                let _ = push_download_complete_event(
                    &download_complete_listening_clone,
                    &download_complete_pending_clone,
                    &download_complete_overflow_clone,
                    (url, dest, success),
                    Some(&wakeup_for_download_complete),
                );
            };

        let session_state = session.as_ref().map(|s| s.borrow().state_arc());
        let mut session_guard = match session_state.as_ref() {
            Some(arc) => Some(arc.lock().map_err(|_| {
                pyo3::exceptions::PyRuntimeError::new_err("WebSession lock poisoned")
            })?),
            None => None,
        };

        let app_root_path = match app_root {
            Some(root) => {
                let root_path = PathBuf::from(root);
                if !root_path.is_dir() {
                    return Err(pyo3::exceptions::PyValueError::new_err(format!(
                        "app_root is not a directory: {}",
                        root_path.display()
                    )));
                }
                Some(root_path)
            }
            None => None,
        };

        let register_app = match (&app_root_path, session_guard.as_mut()) {
            (Some(root), Some(guard)) if !guard.ephemeral => match &guard.registered_app_root {
                Some(existing) if existing != root => {
                    return Err(pyo3::exceptions::PyValueError::new_err(format!(
                        "WebSession already has app root {}; cannot use {}. \
WebViews that share a session must use the same app= root \
(Linux registers tkwry:// once per WebContext)",
                        existing.display(),
                        root.display()
                    )));
                }
                Some(_) => {
                    // Linux registers once on the shared context; Windows/macOS
                    // attach the scheme per WebView.
                    cfg!(any(target_os = "windows", target_os = "macos"))
                }
                None => {
                    guard.registered_app_root = Some(root.clone());
                    true
                }
            },
            (Some(_), _) => true,
            (None, _) => false,
        };

        let ephemeral = session_guard.as_ref().map(|g| g.ephemeral).unwrap_or(false);
        #[cfg(target_os = "macos")]
        let data_store_id = session_guard.as_ref().and_then(|g| g.data_store_id);

        let mut builder = match session_guard.as_mut() {
            Some(guard) => wry::WebViewBuilder::new_with_web_context(&mut guard.context),
            None => wry::WebViewBuilder::new(),
        };

        builder = builder
            .with_bounds(make_rect(0.0, 0.0, width as f64, height as f64))
            .with_visible(visible)
            .with_devtools(devtools)
            .with_clipboard(clipboard)
            .with_focused(focused)
            .with_navigation_handler(nav_handler)
            .with_on_page_load_handler(pageload_handler)
            .with_document_title_changed_handler(title_handler)
            .with_new_window_req_handler(newwin_handler)
            .with_drag_drop_handler(drag_drop_handler)
            .with_download_started_handler(download_started_handler)
            .with_download_completed_handler(download_completed_handler);
        if !javascript_enabled {
            builder = builder.with_javascript_disabled();
        }
        builder = builder.with_autoplay(autoplay);
        builder = builder.with_hotkeys_zoom(hotkeys_zoom);
        builder = builder.with_back_forward_navigation_gestures(back_forward_gestures);
        #[cfg(target_os = "windows")]
        {
            use wry::WebViewBuilderExtWindows;
            builder = builder.with_default_context_menus(default_context_menus);
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = default_context_menus;
        }
        if has_permission_handler {
            let permission_cb_clone = permission_cb.clone();
            let permission_sync_pending_clone = permission_sync_pending.clone();
            let wakeup_fd_for_permission = wakeup_write_fd.clone();
            let owner_thread_for_permission = owner_thread;
            builder = builder.with_permission_handler(move |kind: wry::PermissionKind| {
                let kind = permission_kind_from_wry(kind);
                let slot = Arc::new(SyncHookSlot::new());
                if !enqueue_permission_sync_hook(&permission_sync_pending_clone, kind, slot.clone())
                {
                    return wry::PermissionResponse::Deny;
                }
                notify_wakeup(&wakeup_fd_for_permission);
                if Python::attach(|_py| python_thread_id().ok())
                    == Some(owner_thread_for_permission)
                {
                    drain_permission_sync_hooks(
                        &permission_cb_clone,
                        &permission_sync_pending_clone,
                    );
                }
                let resp = wait_sync_hook(
                    &slot,
                    SYNC_HOOK_TIMEOUT,
                    SYNC_HOOK_HANDLER_TIMEOUT,
                    "permission_handler",
                    PermissionResponse::Deny,
                    Some(&wakeup_fd_for_permission),
                );
                permission_response_to_wry(resp)
            });
        }
        if with_ipc {
            let ipc_pending_for_handler = ipc_pending.clone();
            let rpc_pending_for_handler = rpc_pending.clone();
            let ipc_listening_for_handler = ipc_listening.clone();
            let ipc_overflow_for_handler = ipc_overflow_dropped.clone();
            let rpc_overflow_for_handler = rpc_overflow_dropped.clone();
            let wakeup_for_handler = wakeup_write_fd.clone();
            builder = builder.with_ipc_handler(move |req: wry::http::Request<String>| {
                let source_url = req.uri().to_string();
                let body = req.body().clone();
                let _ = enqueue_window_ipc_body(
                    &ipc_listening_for_handler,
                    &ipc_pending_for_handler,
                    &ipc_overflow_for_handler,
                    &rpc_pending_for_handler,
                    &rpc_overflow_for_handler,
                    body,
                    source_url,
                    Some(&wakeup_for_handler),
                );
            });
        }

        if ephemeral {
            // Private browsing. On Linux this uses a per-view ephemeral
            // context (shared WebContext is ignored for storage).
            builder = builder.with_incognito(true);
        }

        #[cfg(target_os = "macos")]
        if let Some(id) = data_store_id {
            use wry::WebViewBuilderExtDarwin;
            builder = builder.with_data_store_identifier(id);
        }

        if register_app {
            let root_for_protocol = {
                let root = app_root_path.expect("register_app implies app_root");
                root.canonicalize().unwrap_or(root)
            };
            let serve_options = app_protocol::AppServeOptions {
                spa_fallback,
                cache_control: app_cache_control,
                csp: app_csp,
                coop: app_coop,
                corp: app_corp,
            };
            builder = builder.with_custom_protocol("tkwry".into(), move |_id, request| {
                app_protocol::serve_app_request(&root_for_protocol, request, &serve_options)
            });
            #[cfg(target_os = "windows")]
            {
                use wry::WebViewBuilderExtWindows;
                // Match macOS/Linux ``tkwry://`` origins more closely for CORS.
                builder = builder.with_https_scheme(true);
            }
        }

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

        // Drop the session lock before storing Arc on Self.
        drop(session_guard);
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
        let mac = macos::MacPlatformState::install(
            inner.clone(),
            parent_ns_view,
            wakeup_write_fd.clone(),
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;

        Ok(Self {
            owner_thread,
            inner,
            page_load_pending,
            ipc_pending,
            rpc_pending,
            title_pending,
            drag_drop_pending,
            download_complete_pending,
            eval_callbacks,
            eval_result_pending,
            eval_next_token: AtomicU64::new(1),
            page_load_listening,
            ipc_listening,
            title_listening,
            drag_drop_listening,
            download_complete_listening,
            ipc_overflow_dropped,
            rpc_overflow_dropped,
            page_load_overflow_dropped,
            title_overflow_dropped,
            drag_drop_overflow_dropped,
            download_complete_overflow_dropped,
            eval_overflow_dropped,
            nav_sync_pending,
            newwin_sync_pending,
            permission_sync_pending,
            download_sync_pending,
            wakeup_write_fd,
            #[cfg(target_os = "macos")]
            mac,
            nav_cb,
            newwin_cb,
            permission_cb,
            download_cb,
            wry_call_depth: Cell::new(0),
            destroy_pending: Cell::new(false),
            session: session_state,
        })
    }

    fn load_url(&self, url: &str) -> PyResult<()> {
        let url = app_protocol::navigate_url(url);
        with_webview(self, |wv| {
            wv.load_url(url.as_ref())
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    /// Navigate with extra request headers (this navigation only).
    ///
    /// Header **values** must not appear in error messages (callers validate too).
    fn load_url_with_headers(&self, url: &str, headers: Vec<(String, String)>) -> PyResult<()> {
        use wry::http::{HeaderMap, HeaderName, HeaderValue};

        let mut map = HeaderMap::new();
        for (name, value) in headers {
            let header_name = HeaderName::try_from(name.as_str())
                .map_err(|_| pyo3::exceptions::PyValueError::new_err("invalid HTTP header name"))?;
            let header_value = HeaderValue::try_from(value.as_str()).map_err(|_| {
                pyo3::exceptions::PyValueError::new_err("invalid HTTP header value")
            })?;
            map.append(header_name, header_value);
        }
        let url = app_protocol::navigate_url(url);
        with_webview(self, |wv| {
            wv.load_url_with_headers(url.as_ref(), map)
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

    fn go_back(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.go_back()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn go_forward(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.go_forward()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn can_go_back(&self) -> PyResult<bool> {
        with_webview(self, |wv| {
            wv.can_go_back()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn can_go_forward(&self) -> PyResult<bool> {
        with_webview(self, |wv| {
            wv.can_go_forward()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn print(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.print()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    /// macOS-only margins for the system print dialog (wry ``WebViewExtMacOS``).
    ///
    /// Still fire-and-forget — no PDF / success / cancel result. On Windows and
    /// Linux raises ``OSError``.
    #[pyo3(signature = (*, top = 0.0, right = 0.0, bottom = 0.0, left = 0.0))]
    fn print_with_options(&self, top: f32, right: f32, bottom: f32, left: f32) -> PyResult<()> {
        with_webview(self, |wv| {
            #[cfg(target_os = "macos")]
            {
                use wry::{PrintMargin, PrintOptions, WebViewExtMacOS};
                wv.print_with_options(&PrintOptions {
                    margins: PrintMargin {
                        top,
                        right,
                        bottom,
                        left,
                    },
                })
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = (wv, top, right, bottom, left);
                Err(pyo3::exceptions::PyOSError::new_err(
                    "print_with_options is only available on macOS \
                     (wry WebViewExtMacOS); use print() on Windows / Linux",
                ))
            }
        })
    }

    /// Page zoom factor (`1.0` = 100%). Wraps wry `WebView::zoom`.
    ///
    /// Engine range is platform-defined (e.g. WebView2 typically `0.25`–`5.0`);
    /// tkwry does not clamp.
    fn set_zoom(&self, scale: f64) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.zoom(scale)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn cookies(&self) -> PyResult<Vec<cookie_api::Cookie>> {
        with_webview(self, |wv| {
            wv.cookies()
                .map(|list| list.iter().map(cookie_api::Cookie::from_wry).collect())
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn cookies_for_url(&self, url: &str) -> PyResult<Vec<cookie_api::Cookie>> {
        with_webview(self, |wv| {
            wv.cookies_for_url(url)
                .map(|list| list.iter().map(cookie_api::Cookie::from_wry).collect())
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn set_cookie(&self, cookie: cookie_api::Cookie) -> PyResult<()> {
        let wry_cookie = cookie.to_wry()?;
        with_webview(self, |wv| {
            wv.set_cookie(&wry_cookie)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn delete_cookie(&self, cookie: cookie_api::Cookie) -> PyResult<()> {
        let wry_cookie = cookie.to_wry()?;
        with_webview(self, |wv| {
            wv.delete_cookie(&wry_cookie)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn clear_all_browsing_data(&self) -> PyResult<()> {
        with_webview(self, |wv| {
            wv.clear_all_browsing_data()
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
        drain_permission_sync_hooks(&self.permission_cb, &self.permission_sync_pending);
        drain_download_sync_hooks(&self.download_cb, &self.download_sync_pending);
        Ok(())
    }

    fn take_queue_drop_counts(&self) -> PyResult<(u64, u64, u64, u64, u64, u64)> {
        self.require_owner_thread()?;
        Ok((
            self.ipc_overflow_dropped.swap(0, Ordering::SeqCst),
            self.page_load_overflow_dropped.swap(0, Ordering::SeqCst),
            self.title_overflow_dropped.swap(0, Ordering::SeqCst),
            self.drag_drop_overflow_dropped.swap(0, Ordering::SeqCst),
            self.eval_overflow_dropped.swap(0, Ordering::SeqCst),
            self.rpc_overflow_dropped.swap(0, Ordering::SeqCst),
        ))
    }

    /// Same counters as ``take_queue_drop_counts`` plus download-complete overflow.
    ///
    /// Returns
    /// ``(ipc, page_load, title, drag_drop, eval, rpc, download_complete)``.
    fn take_queue_drop_stats(&self) -> PyResult<(u64, u64, u64, u64, u64, u64, u64)> {
        self.require_owner_thread()?;
        Ok((
            self.ipc_overflow_dropped.swap(0, Ordering::SeqCst),
            self.page_load_overflow_dropped.swap(0, Ordering::SeqCst),
            self.title_overflow_dropped.swap(0, Ordering::SeqCst),
            self.drag_drop_overflow_dropped.swap(0, Ordering::SeqCst),
            self.eval_overflow_dropped.swap(0, Ordering::SeqCst),
            self.rpc_overflow_dropped.swap(0, Ordering::SeqCst),
            self.download_complete_overflow_dropped
                .swap(0, Ordering::SeqCst),
        ))
    }

    fn set_ipc_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(&self.ipc_listening, &self.ipc_pending, enabled)?;
        set_listening_and_clear_queue(&self.ipc_listening, &self.rpc_pending, enabled)
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

    fn drain_ipc_messages(&self) -> PyResult<Vec<(String, String)>> {
        self.require_owner_thread()?;
        drain_queue(&self.ipc_pending)
    }

    fn drain_rpc_messages(&self) -> PyResult<Vec<(String, String)>> {
        self.require_owner_thread()?;
        drain_queue(&self.rpc_pending)
    }

    fn drain_title_events(&self) -> PyResult<Vec<String>> {
        self.require_owner_thread()?;
        drain_queue(&self.title_pending)
    }

    fn drain_drag_drop_events(&self) -> PyResult<Vec<DragDropPendingItem>> {
        self.require_owner_thread()?;
        drain_queue(&self.drag_drop_pending)
    }

    fn set_on_download_started(&self, handler: Py<PyAny>) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .download_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = Some(handler);
        Ok(())
    }

    fn clear_on_download_started(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        let mut guard = self
            .download_cb
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("callback lock poisoned"))?;
        *guard = None;
        abort_download_sync_hooks(&self.download_sync_pending);
        Ok(())
    }

    fn set_download_complete_listening(&self, enabled: bool) -> PyResult<()> {
        self.require_owner_thread()?;
        set_listening_and_clear_queue(
            &self.download_complete_listening,
            &self.download_complete_pending,
            enabled,
        )
    }

    fn drain_download_complete_events(&self) -> PyResult<Vec<DownloadCompletePendingItem>> {
        self.require_owner_thread()?;
        drain_queue(&self.download_complete_pending)
    }

    fn _enqueue_download_complete_event(
        &self,
        url: String,
        dest: Option<String>,
        success: bool,
    ) -> PyResult<()> {
        self.require_owner_thread()?;
        push_download_complete_event(
            &self.download_complete_listening,
            &self.download_complete_pending,
            &self.download_complete_overflow_dropped,
            (url, dest, success),
            None,
        )
        .map_err(|()| queue_lock_poisoned())
    }

    #[pyo3(signature = (message, source_url=None))]
    fn _enqueue_ipc_message(&self, message: String, source_url: Option<String>) -> PyResult<()> {
        self.require_owner_thread()?;
        let source_url = source_url.unwrap_or_default();
        if is_rpc_envelope(&message) && message.len() > MAX_RPC_MESSAGE_BYTES {
            if let Some(id) = extract_rpc_request_id(&message) {
                let envelope = rpc_reject_envelope(
                    id,
                    "RpcMessageTooLarge",
                    &format!(
                        "RPC message exceeds {} byte limit ({} bytes)",
                        MAX_RPC_MESSAGE_BYTES,
                        message.len()
                    ),
                );
                return push_window_ipc_body(
                    &self.ipc_listening,
                    &self.ipc_pending,
                    &self.ipc_overflow_dropped,
                    &self.rpc_pending,
                    &self.rpc_overflow_dropped,
                    envelope,
                    source_url,
                    None,
                )
                .map_err(|()| queue_lock_poisoned());
            }
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "RPC message exceeds {} byte limit",
                MAX_RPC_MESSAGE_BYTES
            )));
        }
        if message.len() > MAX_IPC_MESSAGE_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "IPC message exceeds {} byte limit",
                MAX_IPC_MESSAGE_BYTES
            )));
        }
        push_window_ipc_body(
            &self.ipc_listening,
            &self.ipc_pending,
            &self.ipc_overflow_dropped,
            &self.rpc_pending,
            &self.rpc_overflow_dropped,
            message,
            source_url,
            None,
        )
        .map_err(|()| queue_lock_poisoned())
    }

    fn _enqueue_title_event(&self, title: String) -> PyResult<()> {
        self.require_owner_thread()?;
        push_title_event(
            &self.title_listening,
            &self.title_pending,
            &self.title_overflow_dropped,
            title,
            None,
        )
        .map_err(|()| queue_lock_poisoned())
    }

    fn _enqueue_drag_drop_event(
        &self,
        event: DragDropEvent,
        paths: Vec<String>,
        position: (i32, i32),
    ) -> PyResult<()> {
        self.require_owner_thread()?;
        push_drag_drop_event(
            &self.drag_drop_listening,
            &self.drag_drop_pending,
            &self.drag_drop_overflow_dropped,
            (event, paths, position),
            None,
        )
        .map_err(|()| queue_lock_poisoned())
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

    fn bounds(&self) -> PyResult<(f64, f64, f64, f64)> {
        with_webview(self, |wv| {
            let rect = wv
                .bounds()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            Ok(rect_to_tuple(rect))
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
        self.mac.set_web_input_active(active);
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
            Ok(self.mac.take_tk_unfocus())
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
            Ok(self.mac.tk_unfocus_pending())
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
        self.mac.request_tk_unfocus(&self.wakeup_write_fd);
        #[cfg(not(target_os = "macos"))]
        let _ = self;
        Ok(())
    }

    /// Whether this webview currently owns macOS keyboard routing (``web_wants``).
    fn mac_web_input_active(&self) -> PyResult<bool> {
        self.require_owner_thread()?;
        #[cfg(target_os = "macos")]
        {
            Ok(self.mac.web_input_active())
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
            Ok(macos::hit_test_wry_point(&self.inner, x, y))
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
        self.mac.release_web_input();
        with_webview(self, |wv| {
            wv.focus_parent()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })
    }

    fn open_devtools(&self) -> PyResult<()> {
        // DevTools open/close can run a nested AppKit/WebKit turn that re-enters
        // tkwry queues. Holding `inner` across that nests into deadlock (seen on
        // macOS after prior WebView create/destroy in the same process).
        with_webview_reentrant(self, |wv| {
            wv.open_devtools();
            Ok(())
        })
    }

    fn close_devtools(&self) -> PyResult<()> {
        with_webview_reentrant(self, |wv| {
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
            with_webview(self, |wv| match macos::read_document_url(wv) {
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

    /// Force release even when nested wry calls left ``destroy_pending`` set.
    fn force_destroy(&self) -> PyResult<()> {
        self.require_owner_thread()?;
        self.clear_callbacks_and_queues();
        self.destroy_pending.set(false);
        self.wry_call_depth.set(0);
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
    // Resolve sync hooks queued before this nested wry call so WebKit callbacks
    // are not rejected while the Tk thread is inside load_url / eval_js.
    drain_nav_sync_hooks(&this.nav_cb, &this.nav_sync_pending);
    drain_newwin_sync_hooks(&this.newwin_cb, &this.newwin_sync_pending);
    drain_permission_sync_hooks(&this.permission_cb, &this.permission_sync_pending);
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

/// Like [`with_webview`], but temporarily takes the native view out of `inner`.
///
/// Use for wry APIs that may run a nested platform event turn (DevTools open /
/// close). Holding `inner` across that turn deadlocks when nested work tries to
/// touch the same WebView.
fn with_webview_reentrant<F, T>(this: &WebView, f: F) -> PyResult<T>
where
    F: FnOnce(&wry::WebView) -> PyResult<T>,
{
    this.require_owner_thread()?;
    this.enter_wry_call();
    drain_nav_sync_hooks(&this.nav_cb, &this.nav_sync_pending);
    drain_newwin_sync_hooks(&this.newwin_cb, &this.newwin_sync_pending);
    drain_permission_sync_hooks(&this.permission_cb, &this.permission_sync_pending);

    let taken = {
        let mut guard = this
            .inner
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("webview lock poisoned"))?;
        guard.take()
    };

    let result = match taken.as_ref() {
        Some(wv) => f(wv),
        None => Err(pyo3::exceptions::PyRuntimeError::new_err(
            "webview already destroyed",
        )),
    };

    {
        let mut guard = this
            .inner
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("webview lock poisoned"))?;
        // Put the view back unless destroy already cleared/replaced it.
        if guard.is_none() {
            *guard = taken;
        }
    }
    this.leave_wry_call()?;
    result
}

#[pyfunction]
#[pyo3(signature = (max_iterations=None))]
fn pump_events(max_iterations: Option<usize>) -> bool {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        const DEFAULT_ITERATIONS: usize = 128;
        const MAX_ITERATIONS: usize = 512;
        // Tests / pumps may run before any WebView attaches GtkPump.
        let _ = gtk::init();
        // Bound work per Tk tick — WebKitGTK can enqueue continuously and
        // an unbounded drain would hang nested inside Tcl's update().
        let limit = max_iterations
            .unwrap_or(DEFAULT_ITERATIONS)
            .clamp(1, MAX_ITERATIONS);
        for _ in 0..limit {
            if !gtk::main_iteration_do(false) {
                return gtk::events_pending();
            }
        }
        gtk::events_pending()
    }
    #[cfg(not(all(unix, not(target_os = "macos"))))]
    {
        let _ = max_iterations;
        false
    }
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
        macos::disable_process_automatic_window_tabbing()
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
        macos::disable_window_tabbing(parent_ns_view)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    }
    Ok(())
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<WebView>()?;
    m.add_class::<session::WebSession>()?;
    m.add_class::<cookie_api::Cookie>()?;
    m.add_class::<PageLoadEvent>()?;
    m.add_class::<NewWindowResponse>()?;
    m.add_class::<PermissionKind>()?;
    m.add_class::<PermissionResponse>()?;
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
    fn push_eval_result_evicts_oldest_and_delivers_new_result_when_pending_full() {
        let pending = Arc::new(Mutex::new(VecDeque::new()));
        let dropped = AtomicU64::new(0);
        for token in 0..MAX_EVAL_PENDING as u64 {
            push_eval_result(&pending, &dropped, token, format!("r{token}"));
        }
        push_eval_result(&pending, &dropped, 9999, "new".into());
        assert!(dropped.load(Ordering::SeqCst) >= 1);
        let queue = pending.lock().unwrap();
        assert_eq!(queue.len(), MAX_EVAL_PENDING);
        assert_eq!(queue.back(), Some(&(9999, Some("new".to_string()))));
    }

    #[test]
    fn push_if_listening_drops_oldest_when_full() {
        let listening = AtomicBool::new(true);
        let pending: Arc<Mutex<VecDeque<i32>>> = Arc::new(Mutex::new(VecDeque::new()));
        let dropped = AtomicU64::new(0);
        for value in 0..=4_i32 {
            assert!(push_if_listening(
                &listening,
                &pending,
                &dropped,
                value,
                4,
                "test",
                None,
                |_: &mut VecDeque<i32>| false,
            )
            .is_ok());
        }
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert_eq!(*pending.lock().unwrap(), VecDeque::from([1, 2, 3, 4]));
    }

    #[test]
    fn push_if_listening_counts_drop_when_not_listening() {
        let listening = AtomicBool::new(false);
        let pending: Arc<Mutex<VecDeque<i32>>> = Arc::new(Mutex::new(VecDeque::new()));
        let dropped = AtomicU64::new(0);
        assert!(push_if_listening(
            &listening,
            &pending,
            &dropped,
            1,
            4,
            "test",
            None,
            |_: &mut VecDeque<i32>| false,
        )
        .is_ok());
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert!(pending.lock().unwrap().is_empty());
    }

    #[test]
    fn try_compact_title_queue_removes_adjacent_duplicates() {
        let mut queue = VecDeque::from(["a".into(), "a".into(), "b".into()]);
        assert!(try_compact_title_queue(&mut queue));
        assert_eq!(queue, VecDeque::from(["a".to_string(), "b".to_string()]));
    }

    #[test]
    fn is_rpc_envelope_accepts_compact_and_spaced_json() {
        assert!(is_rpc_envelope(
            r#"{"__tkwry":"rpc","id":"r1","method":"ping","params":[]}"#
        ));
        assert!(is_rpc_envelope(
            r#"{ "__tkwry": "rpc", "id": "r1", "method": "ping" }"#
        ));
        assert!(!is_rpc_envelope(r#"{"action":"increment"}"#));
        assert!(!is_rpc_envelope("not-json"));
        assert!(!is_rpc_envelope(r#"{"__tkwry":"event"}"#));
    }

    #[test]
    fn extract_rpc_request_id_from_envelope() {
        assert_eq!(
            extract_rpc_request_id(r#"{"__tkwry":"rpc","id":"r42","method":"x"}"#),
            Some("r42")
        );
        assert_eq!(
            extract_rpc_request_id(r#"{ "id" : "spaced" , "__tkwry":"rpc"}"#),
            Some("spaced")
        );
        assert_eq!(extract_rpc_request_id(r#"{"action":"x"}"#), None);
        assert_eq!(extract_rpc_request_id("not-json"), None);
    }

    #[test]
    fn oversized_rpc_enqueues_reject_envelope() {
        let listening = AtomicBool::new(true);
        let ipc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let rpc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let ipc_dropped = AtomicU64::new(0);
        let rpc_dropped = AtomicU64::new(0);
        let padding = "x".repeat(MAX_RPC_MESSAGE_BYTES + 8);
        let body = format!(r#"{{"__tkwry":"rpc","id":"r9","method":"x","params":["{padding}"]}}"#);
        assert!(enqueue_window_ipc_body(
            &listening,
            &ipc_pending,
            &ipc_dropped,
            &rpc_pending,
            &rpc_dropped,
            body,
            "https://example.com/".into(),
            None,
        )
        .is_ok());
        assert_eq!(rpc_dropped.load(Ordering::SeqCst), 0);
        let queued = rpc_pending.lock().unwrap();
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].0, "https://example.com/");
        assert!(queued[0].1.contains("RpcMessageTooLarge"));
        assert!(queued[0].1.contains(r#""id":"r9""#));
        assert!(queued[0].1.len() < 4096);
    }

    #[test]
    fn rpc_queue_survives_ipc_overflow() {
        let listening = AtomicBool::new(true);
        let ipc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let rpc_pending: IpcPending = Arc::new(Mutex::new(VecDeque::new()));
        let ipc_dropped = AtomicU64::new(0);
        let rpc_dropped = AtomicU64::new(0);
        for i in 0..MAX_IPC_PENDING {
            assert!(push_window_ipc_body(
                &listening,
                &ipc_pending,
                &ipc_dropped,
                &rpc_pending,
                &rpc_dropped,
                format!("ipc-{i}"),
                String::new(),
                None,
            )
            .is_ok());
        }
        assert!(push_window_ipc_body(
            &listening,
            &ipc_pending,
            &ipc_dropped,
            &rpc_pending,
            &rpc_dropped,
            "ipc-overflow".into(),
            String::new(),
            None,
        )
        .is_ok());
        assert!(ipc_dropped.load(Ordering::SeqCst) >= 1);
        assert_eq!(ipc_pending.lock().unwrap().len(), MAX_IPC_PENDING);

        let rpc_msg = r#"{"__tkwry":"rpc","id":"r1","method":"ping","params":[]}"#;
        assert!(push_window_ipc_body(
            &listening,
            &ipc_pending,
            &ipc_dropped,
            &rpc_pending,
            &rpc_dropped,
            rpc_msg.into(),
            "tkwry://localhost/".into(),
            None,
        )
        .is_ok());
        assert_eq!(rpc_dropped.load(Ordering::SeqCst), 0);
        assert_eq!(rpc_pending.lock().unwrap().len(), 1);
        assert_eq!(
            rpc_pending.lock().unwrap()[0],
            ("tkwry://localhost/".into(), rpc_msg.into())
        );
    }

    #[test]
    fn dangerous_nav_schemes_are_rejected() {
        assert!(is_dangerous_nav_url("javascript:alert(1)"));
        assert!(is_dangerous_nav_url("vbscript:msgbox(1)"));
        assert!(is_dangerous_nav_url("blob:https://example.com/uuid"));
        assert!(is_dangerous_nav_url("mailto:user@example.com"));
        // WebView2 NavigateToString (html=) reports data:; do not block it here.
        assert!(!is_dangerous_nav_url("DATA:text/html,hi"));
        assert!(!is_dangerous_nav_url("https://example.com/"));
        assert!(!is_dangerous_nav_url("tkwry://localhost/index.html"));
        assert!(!is_dangerous_nav_url("file:///tmp/index.html"));
        assert!(!is_dangerous_nav_url("about:blank"));
    }

    #[test]
    fn try_compact_page_load_queue_drops_stale_started() {
        let mut queue = VecDeque::from([
            (PageLoadEvent::Started, "https://old.example/".into()),
            (PageLoadEvent::Started, "https://new.example/".into()),
        ]);
        assert!(try_compact_page_load_queue(&mut queue));
        assert_eq!(queue.len(), 1);
        assert_eq!(queue[0].1, "https://new.example/");
    }

    #[test]
    fn try_compact_page_load_queue_drops_orphan_finished() {
        let mut queue = VecDeque::from([(PageLoadEvent::Finished, "https://old.example/".into())]);
        assert!(try_compact_page_load_queue(&mut queue));
        assert!(queue.is_empty());
    }

    #[test]
    fn wait_sync_hook_does_not_timeout_after_handler_starts() {
        let slot = Arc::new(SyncHookSlot::new());
        let slot_clone = slot.clone();
        let worker = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            mark_sync_hook_started(&slot_clone);
            std::thread::sleep(Duration::from_millis(150));
            resolve_sync_hook(&slot_clone, true);
        });
        let result = wait_sync_hook(
            &slot,
            Duration::from_millis(80),
            Duration::from_secs(1),
            "test",
            false,
            None,
        );
        worker.join().unwrap();
        assert!(result);
    }

    #[test]
    fn wait_sync_hook_enforces_absolute_deadline_after_handler_starts() {
        let slot = Arc::new(SyncHookSlot::new());
        mark_sync_hook_started(&slot);
        if let Ok(mut started_at) = slot.handler_started_at.lock() {
            *started_at = Some(Instant::now() - SYNC_HOOK_MAX_WAIT);
        }
        let result = wait_sync_hook(
            &slot,
            Duration::from_secs(30),
            Duration::from_secs(30),
            "test",
            false,
            None,
        );
        assert!(!result);
        assert!(slot.cancelled.load(Ordering::SeqCst));
    }

    #[test]
    fn wait_sync_hook_times_out_when_handler_never_returns() {
        let slot = Arc::new(SyncHookSlot::new());
        mark_sync_hook_started(&slot);
        let result = wait_sync_hook(
            &slot,
            Duration::from_millis(50),
            Duration::from_millis(50),
            "test",
            false,
            None,
        );
        assert!(!result);
        assert!(slot.cancelled.load(Ordering::SeqCst));
    }

    #[test]
    fn prune_stale_eval_callbacks_removes_old_entries() {
        Python::initialize();
        let mut callbacks = HashMap::new();
        let dropped = AtomicU64::new(0);
        Python::attach(|py| {
            let cb = py.None();
            callbacks.insert(
                1,
                (
                    cb,
                    Instant::now() - EVAL_CALLBACK_TIMEOUT - Duration::from_secs(1),
                ),
            );
            callbacks.insert(2, (py.None(), Instant::now()));
        });
        prune_stale_eval_callbacks(&mut callbacks, &dropped);
        assert_eq!(dropped.load(Ordering::SeqCst), 1);
        assert_eq!(callbacks.len(), 1);
        assert!(callbacks.contains_key(&2));
    }
}
