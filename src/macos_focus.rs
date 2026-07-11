//! macOS: route keyboard focus between WKWebView and Tk at the NSEvent layer.
//!
//! Hit testing uses AppKit view conversion (window point → WKWebView local
//! space) so embed-parent flip, Retina scale, and non-standard view hierarchies
//! stay aligned with wry ``set_bounds``.
//!
//! Never call Python from AppKit callbacks or ``performBlock`` — deadlock with
//! Tk.  Instead write one byte to a pipe and set a flag; Python drains on the
//! Tk thread via a lightweight ``after`` pump (WKWebView breaks Tcl
//! ``fileevent`` on the same root).

use std::cell::RefCell;
use std::collections::HashMap;
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::MainThreadMarker;
use objc2_app_kit::{
    NSEvent, NSEventMask, NSView, NSWindow, NSWindowDidBecomeKeyNotification,
};
use objc2_foundation::{NSNotification, NSNotificationCenter, NSOperationQueue, NSPoint};
use wry::dpi::{LogicalPosition, LogicalSize};
use wry::WebViewExtMacOS;

const TAB_KEY_CODE: u16 = 48;
const ESCAPE_KEY_CODE: u16 = 53;

struct FocusEntry {
    id: u64,
    inner: Arc<Mutex<Option<wry::WebView>>>,
    web_wants_keyboard: Arc<AtomicBool>,
    mac_tk_unfocus: Arc<AtomicBool>,
    parent_ns_view: NonNull<NSView>,
}

struct FocusMonitors {
    click_monitor: Retained<AnyObject>,
    keydown_monitor: Retained<AnyObject>,
    keyup_monitor: Retained<AnyObject>,
    flags_monitor: Retained<AnyObject>,
    key_observer: Retained<AnyObject>,
}

struct WindowFocusCoordinator {
    window: Retained<NSWindow>,
    entries: Vec<FocusEntry>,
    wakeup_write_fd: Arc<AtomicI32>,
    monitors: Option<FocusMonitors>,
}

static NEXT_ENTRY_ID: AtomicU64 = AtomicU64::new(1);

thread_local! {
    static COORDINATORS: RefCell<HashMap<isize, WindowFocusCoordinator>> =
        RefCell::new(HashMap::new());
}

fn window_key(window: &NSWindow) -> isize {
    window as *const NSWindow as isize
}

pub struct FocusSyncGuard {
    window_key: isize,
    entry_id: u64,
}

impl Drop for FocusSyncGuard {
    fn drop(&mut self) {
        COORDINATORS.with(|map| {
            let mut map = map.borrow_mut();
            let Some(coord) = map.get_mut(&self.window_key) else {
                return;
            };
            coord.entries.retain(|entry| entry.id != self.entry_id);
            if coord.entries.is_empty() {
                coord.remove_monitors();
                map.remove(&self.window_key);
            }
        });
    }
}

/// Wake the Tk main loop (pipe byte + flag; drained by Python ``after`` pump).
pub fn notify_wakeup(fd: &AtomicI32) {
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

pub fn install_focus_sync(
    inner: Arc<Mutex<Option<wry::WebView>>>,
    parent_ns_view: NonNull<NSView>,
    web_wants_keyboard: Arc<AtomicBool>,
    mac_tk_unfocus: Arc<AtomicBool>,
    wakeup_write_fd: Arc<AtomicI32>,
) -> Result<FocusSyncGuard, String> {
    let _mtm = MainThreadMarker::new().ok_or("macOS focus sync requires the main thread")?;

    let ns_window = unsafe { parent_ns_view.as_ref() }
        .window()
        .ok_or("macOS focus sync requires an NSWindow")?;

    let entry_id = NEXT_ENTRY_ID.fetch_add(1, Ordering::Relaxed);
    let entry = FocusEntry {
        id: entry_id,
        inner,
        web_wants_keyboard,
        mac_tk_unfocus,
        parent_ns_view,
    };

    let key = window_key(&ns_window);
    COORDINATORS.with(|map| {
        let mut map = map.borrow_mut();
        let coord = map.entry(key).or_insert_with(|| WindowFocusCoordinator {
            window: ns_window.clone(),
            entries: Vec::new(),
            wakeup_write_fd: wakeup_write_fd.clone(),
            monitors: None,
        });
        coord.entries.push(entry);
        if coord.monitors.is_none() {
            coord.install_monitors()?;
        }
        Ok(FocusSyncGuard {
            window_key: key,
            entry_id,
        })
    })
}

impl WindowFocusCoordinator {
    fn install_monitors(&mut self) -> Result<(), String> {
        let window = self.window.clone();
        let wakeup = self.wakeup_write_fd.clone();

        let click_block = {
            let window = window.clone();
            let wakeup = wakeup.clone();
            RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
                let event_ref = unsafe { event.as_ref() };
                let window_point = event_ref.locationInWindow();
                with_coordinator(&window, |coord| {
                    handle_click(&window, &coord.entries, window_point, &wakeup);
                });
                event.as_ptr()
            })
        };

        let click_mask =
            NSEventMask::LeftMouseDown | NSEventMask::RightMouseDown | NSEventMask::OtherMouseDown;
        let click_monitor = unsafe {
            NSEvent::addLocalMonitorForEventsMatchingMask_handler(click_mask, &click_block)
        }
        .ok_or("failed to install NSEvent local monitor")?;

        let keydown_block = {
            let window = window.clone();
            let wakeup = wakeup.clone();
            RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
                let event_ref = unsafe { event.as_ref() };
                with_coordinator(&window, |coord| {
                    handle_keydown(&window, &coord.entries, event_ref, &wakeup);
                });
                event.as_ptr()
            })
        };

        let keydown_mask = NSEventMask::KeyDown;
        let keydown_monitor = unsafe {
            NSEvent::addLocalMonitorForEventsMatchingMask_handler(keydown_mask, &keydown_block)
        }
        .ok_or("failed to install NSEvent keydown monitor")?;

        let keyup_block = {
            let window = window.clone();
            let wakeup = wakeup.clone();
            RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
                with_coordinator(&window, |coord| {
                    handle_keyup_or_flags(&window, &coord.entries, &wakeup, "focus on keyup");
                });
                event.as_ptr()
            })
        };

        let keyup_mask = NSEventMask::KeyUp;
        let keyup_monitor = unsafe {
            NSEvent::addLocalMonitorForEventsMatchingMask_handler(keyup_mask, &keyup_block)
        }
        .ok_or("failed to install NSEvent keyup monitor")?;

        let flags_block = {
            let window = window.clone();
            let wakeup = wakeup.clone();
            RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
                let event_ref = unsafe { event.as_ref() };
                if event_ref.keyCode() == TAB_KEY_CODE {
                    return event.as_ptr();
                }
                with_coordinator(&window, |coord| {
                    handle_keyup_or_flags(
                        &window,
                        &coord.entries,
                        &wakeup,
                        "focus on flags changed",
                    );
                });
                event.as_ptr()
            })
        };

        let flags_mask = NSEventMask::FlagsChanged;
        let flags_monitor = unsafe {
            NSEvent::addLocalMonitorForEventsMatchingMask_handler(flags_mask, &flags_block)
        }
        .ok_or("failed to install NSEvent flags monitor")?;

        let key_block = {
            let window = window.clone();
            let wakeup = wakeup.clone();
            RcBlock::new(move |_notification: NonNull<NSNotification>| {
                with_coordinator(&window, |coord| {
                    handle_window_became_key(&window, &coord.entries, &wakeup);
                });
            })
        };

        let key_observer = unsafe {
            NSNotificationCenter::defaultCenter().addObserverForName_object_queue_usingBlock(
                Some(NSWindowDidBecomeKeyNotification),
                Some(&window),
                Some(NSOperationQueue::mainQueue().as_ref()),
                &key_block,
            )
        };
        let key_observer = unsafe { Retained::cast_unchecked(key_observer) };

        self.monitors = Some(FocusMonitors {
            click_monitor,
            keydown_monitor,
            keyup_monitor,
            flags_monitor,
            key_observer,
        });
        Ok(())
    }

    fn remove_monitors(&mut self) {
        if let Some(monitors) = self.monitors.take() {
            unsafe {
                NSEvent::removeMonitor(&monitors.click_monitor);
                NSEvent::removeMonitor(&monitors.keydown_monitor);
                NSEvent::removeMonitor(&monitors.keyup_monitor);
                NSEvent::removeMonitor(&monitors.flags_monitor);
                NSNotificationCenter::defaultCenter().removeObserver(&monitors.key_observer);
            }
        }
    }
}

fn with_coordinator(window: &NSWindow, f: impl FnOnce(&WindowFocusCoordinator)) {
    COORDINATORS.with(|map| {
        let map = map.borrow();
        let key = window_key(window);
        if let Some(coord) = map.get(&key) {
            f(coord);
        }
    });
}

fn handle_click(
    window: &NSWindow,
    entries: &[FocusEntry],
    window_point: NSPoint,
    wakeup: &AtomicI32,
) {
    if let Some(idx) = topmost_entry_index(window, entries, window_point) {
        activate_entry(entries, idx, wakeup);
    } else {
        release_all_web_focus(entries);
    }
}

fn handle_keydown(
    window: &NSWindow,
    entries: &[FocusEntry],
    event: &NSEvent,
    wakeup: &AtomicI32,
) {
    let Some(active_idx) = active_entry_index(entries) else {
        return;
    };
    let entry = &entries[active_idx];
    if !entry.web_wants_keyboard.load(Ordering::SeqCst) {
        return;
    }

    let key_code = event.keyCode();
    if key_code == TAB_KEY_CODE || key_code == ESCAPE_KEY_CODE {
        if let Ok(guard) = entry.inner.lock() {
            if let Some(ref wv) = *guard {
                release_web_focus_locked(wv, &entry.web_wants_keyboard);
            }
        }
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
        return;
    }

    if let Ok(guard) = entry.inner.lock() {
        if let Some(ref wv) = *guard {
            focus_webview(wv, "focus on keydown");
        }
    }

    let Some(window_point) = current_window_point(window) else {
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
        return;
    };
    if topmost_entry_index(window, entries, window_point) != Some(active_idx) {
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
    }
}

fn handle_keyup_or_flags(
    window: &NSWindow,
    entries: &[FocusEntry],
    wakeup: &AtomicI32,
    label: &str,
) {
    let Some(active_idx) = active_entry_index(entries) else {
        return;
    };
    let entry = &entries[active_idx];
    if !entry.web_wants_keyboard.load(Ordering::SeqCst) {
        return;
    }

    if let Ok(guard) = entry.inner.lock() {
        if let Some(ref wv) = *guard {
            focus_webview(wv, label);
        }
    }

    let Some(window_point) = current_window_point(window) else {
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
        return;
    };
    if topmost_entry_index(window, entries, window_point) != Some(active_idx) {
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
    }
}

fn handle_window_became_key(window: &NSWindow, entries: &[FocusEntry], wakeup: &AtomicI32) {
    let Some(active_idx) = active_entry_index(entries) else {
        return;
    };
    let entry = &entries[active_idx];
    if !entry.web_wants_keyboard.load(Ordering::SeqCst) {
        return;
    }

    let Some(window_point) = current_window_point(window) else {
        if let Ok(guard) = entry.inner.lock() {
            if let Some(ref wv) = *guard {
                release_web_focus_locked(wv, &entry.web_wants_keyboard);
            }
        }
        entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup);
        return;
    };

    match topmost_entry_index(window, entries, window_point) {
        Some(idx) if idx == active_idx => {
            if let Ok(guard) = entry.inner.lock() {
                if let Some(ref wv) = *guard {
                    focus_webview(wv, "focus on window key");
                }
            }
            entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
            notify_wakeup(wakeup);
        }
        Some(idx) => activate_entry(entries, idx, wakeup),
        None => {
            if let Ok(guard) = entry.inner.lock() {
                if let Some(ref wv) = *guard {
                    release_web_focus_locked(wv, &entry.web_wants_keyboard);
                }
            }
            entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
            notify_wakeup(wakeup);
        }
    }
}

fn activate_entry(entries: &[FocusEntry], idx: usize, wakeup: &AtomicI32) {
    for (i, entry) in entries.iter().enumerate() {
        if i == idx {
            if let Ok(guard) = entry.inner.lock() {
                if let Some(ref wv) = *guard {
                    entry.web_wants_keyboard.store(true, Ordering::SeqCst);
                    entry.mac_tk_unfocus.store(true, Ordering::SeqCst);
                    focus_webview(wv, "focus");
                }
            }
        } else if entry.web_wants_keyboard.swap(false, Ordering::SeqCst) {
            if let Ok(guard) = entry.inner.lock() {
                if let Some(ref wv) = *guard {
                    focus_webview_parent(wv, "focus_parent");
                }
            }
        }
    }
    notify_wakeup(wakeup);
}

fn release_all_web_focus(entries: &[FocusEntry]) {
    for entry in entries {
        if !entry.web_wants_keyboard.load(Ordering::SeqCst) {
            continue;
        }
        if let Ok(guard) = entry.inner.lock() {
            if let Some(ref wv) = *guard {
                release_web_focus_locked(wv, &entry.web_wants_keyboard);
            }
        }
    }
}

fn active_entry_index(entries: &[FocusEntry]) -> Option<usize> {
    entries
        .iter()
        .position(|entry| entry.web_wants_keyboard.load(Ordering::SeqCst))
}

/// Pick the frontmost registered WebView at *window_point* using AppKit hit testing.
fn topmost_entry_index(
    window: &NSWindow,
    entries: &[FocusEntry],
    window_point: NSPoint,
) -> Option<usize> {
    let content = window.contentView()?;
    let point_in_content = content.convertPoint_fromView(window_point, None);
    let hit = content.hitTest(point_in_content)?;
    entry_index_for_hit_view(entries, hit)
}

fn entry_index_for_hit_view(entries: &[FocusEntry], hit: Retained<NSView>) -> Option<usize> {
    let mut current = Some(hit);
    while let Some(view) = current {
        if let Some(idx) = entry_index_matching_view(entries, view.as_ref()) {
            return Some(idx);
        }
        current = unsafe { view.superview() };
    }
    None
}

fn entry_index_matching_view(entries: &[FocusEntry], view: &NSView) -> Option<usize> {
    entries
        .iter()
        .enumerate()
        .find_map(|(idx, entry)| view_belongs_to_entry(view, entry).then_some(idx))
}

fn view_belongs_to_entry(view: &NSView, entry: &FocusEntry) -> bool {
    unsafe {
        if std::ptr::eq(view, entry.parent_ns_view.as_ref()) {
            return true;
        }
    }
    let Ok(guard) = entry.inner.lock() else {
        return false;
    };
    let Some(ref wv) = *guard else {
        return false;
    };
    let wk = wv.webview();
    std::ptr::eq(view, Retained::as_ptr(&wk).cast::<NSView>())
}

/// Hit-test using wry top-left coordinates (same space as ``set_bounds``).
pub fn hit_test_wry_point(inner: &Arc<Mutex<Option<wry::WebView>>>, x: f64, y: f64) -> bool {
    let Ok(guard) = inner.lock() else {
        return false;
    };
    let Some(ref wv) = *guard else {
        return false;
    };
    point_in_wry_bounds(wv, NSPoint::new(x, y))
}

fn focus_webview(wv: &wry::WebView, label: &str) {
    if let Err(err) = wv.focus() {
        eprintln!("tkwry: macOS {label} failed: {err}");
    }
}

fn focus_webview_parent(wv: &wry::WebView, label: &str) {
    if let Err(err) = wv.focus_parent() {
        eprintln!("tkwry: macOS {label} failed: {err}");
    }
}

/// Return keyboard from the webview to Tk while holding the webview lock.
fn release_web_focus_locked(wv: &wry::WebView, web_wants: &Arc<AtomicBool>) {
    web_wants.store(false, Ordering::SeqCst);
    focus_webview_parent(wv, "focus_parent");
}

fn logical_bounds(wv: &wry::WebView, bounds: wry::Rect) -> (f64, f64, f64, f64) {
    let scale = wv.ns_window().backingScaleFactor();
    let LogicalPosition { x, y } = bounds.position.to_logical(scale);
    let LogicalSize { width, height } = bounds.size.to_logical(scale);
    (x, y, width, height)
}

fn wry_point_in_logical_rect(point: NSPoint, x: f64, y: f64, width: f64, height: f64) -> bool {
    point.x >= x && point.x < x + width && point.y >= y && point.y < y + height
}

fn point_in_wry_bounds(wv: &wry::WebView, wry_point: NSPoint) -> bool {
    let bounds = match wv.bounds() {
        Ok(bounds) => bounds,
        Err(err) => {
            eprintln!("tkwry: macOS bounds query failed: {err}");
            return false;
        }
    };
    let (x, y, width, height) = logical_bounds(wv, bounds);
    wry_point_in_logical_rect(wry_point, x, y, width, height)
}

fn current_window_point(window: &NSWindow) -> Option<NSPoint> {
    let _mtm = MainThreadMarker::new()?;
    let screen = NSEvent::mouseLocation();
    Some(window.convertPointFromScreen(screen))
}

#[cfg(test)]
mod tests {
    use super::*;
    use objc2_foundation::NSRect;

    fn point_in_ns_rect(point: NSPoint, rect: NSRect) -> bool {
        point.x >= rect.origin.x
            && point.y >= rect.origin.y
            && point.x < rect.origin.x + rect.size.width
            && point.y < rect.origin.y + rect.size.height
    }

    #[test]
    fn wry_point_in_logical_rect_uses_top_left_space() {
        assert!(wry_point_in_logical_rect(
            NSPoint::new(10.0, 20.0),
            0.0,
            0.0,
            100.0,
            50.0
        ));
        assert!(!wry_point_in_logical_rect(
            NSPoint::new(100.0, 20.0),
            0.0,
            0.0,
            100.0,
            50.0
        ));
    }

    #[test]
    fn point_in_ns_rect_uses_appkit_bounds() {
        let rect = NSRect::new(
            NSPoint::new(5.0, 10.0),
            objc2_foundation::NSSize::new(20.0, 30.0),
        );
        assert!(point_in_ns_rect(NSPoint::new(5.0, 10.0), rect));
        assert!(point_in_ns_rect(NSPoint::new(24.9, 39.9), rect));
        assert!(!point_in_ns_rect(NSPoint::new(25.0, 10.0), rect));
        assert!(!point_in_ns_rect(NSPoint::new(5.0, 40.0), rect));
    }

    #[test]
    fn logical_bounds_normalizes_physical_values() {
        use wry::dpi::{PhysicalPosition, PhysicalSize, Position, Size};

        let bounds = wry::Rect {
            position: Position::Physical(PhysicalPosition::new(20, 40)),
            size: Size::Physical(PhysicalSize::new(200, 100)),
        };
        let scale = 2.0;
        let LogicalPosition { x, y } = bounds.position.to_logical(scale);
        let LogicalSize { width, height } = bounds.size.to_logical(scale);
        assert_eq!(x, 10.0);
        assert_eq!(y, 20.0);
        assert_eq!(width, 100.0);
        assert_eq!(height, 50.0);
        assert!(wry_point_in_logical_rect(
            NSPoint::new(50.0, 25.0),
            x,
            y,
            width,
            height
        ));
    }
}
