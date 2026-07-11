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

use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::{Arc, Mutex};

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::MainThreadMarker;
use objc2_app_kit::{NSEvent, NSEventMask, NSView, NSWindowDidBecomeKeyNotification};
use objc2_foundation::{NSNotification, NSNotificationCenter, NSOperationQueue, NSPoint, NSRect};
use wry::dpi::{LogicalPosition, LogicalSize};
use wry::WebViewExtMacOS;

pub struct FocusSyncGuard {
    click_monitor: Retained<AnyObject>,
    keydown_monitor: Retained<AnyObject>,
    flags_monitor: Retained<AnyObject>,
    key_observer: Retained<AnyObject>,
}

impl Drop for FocusSyncGuard {
    fn drop(&mut self) {
        unsafe {
            NSEvent::removeMonitor(&self.click_monitor);
            NSEvent::removeMonitor(&self.keydown_monitor);
            NSEvent::removeMonitor(&self.flags_monitor);
            NSNotificationCenter::defaultCenter().removeObserver(&self.key_observer);
        }
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

    let click_block = {
        let inner = inner.clone();
        let web_wants = web_wants_keyboard.clone();
        let unfocus = mac_tk_unfocus.clone();
        let wakeup = wakeup_write_fd.clone();
        RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
            let event_ref = unsafe { event.as_ref() };
            let window_point = event_ref.locationInWindow();

            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    if window_point_hits_webview(wv, window_point) {
                        web_wants.store(true, Ordering::SeqCst);
                        unfocus.store(true, Ordering::SeqCst);
                        focus_webview(wv, "focus");
                        notify_wakeup(&wakeup);
                    } else if web_wants.load(Ordering::SeqCst) {
                        release_web_focus_locked(wv, &web_wants);
                    }
                } else if web_wants.load(Ordering::SeqCst) {
                    web_wants.store(false, Ordering::SeqCst);
                }
            }

            event.as_ptr()
        })
    };

    let click_mask =
        NSEventMask::LeftMouseDown | NSEventMask::RightMouseDown | NSEventMask::OtherMouseDown;

    let click_monitor =
        unsafe { NSEvent::addLocalMonitorForEventsMatchingMask_handler(click_mask, &click_block) }
            .ok_or("failed to install NSEvent local monitor")?;

    let keydown_block = {
        let inner = inner.clone();
        let web_wants = web_wants_keyboard.clone();
        let unfocus = mac_tk_unfocus.clone();
        let wakeup = wakeup_write_fd.clone();
        RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
            let event_ref = unsafe { event.as_ref() };
            if !web_wants.load(Ordering::SeqCst) {
                return event.as_ptr();
            }
            // Tab and other navigation keys may not reach Tcl bind_all (VoiceOver, etc.).
            const TAB_KEY_CODE: u16 = 48;
            const ESCAPE_KEY_CODE: u16 = 53;
            let key_code = event_ref.keyCode();
            if key_code == TAB_KEY_CODE || key_code == ESCAPE_KEY_CODE {
                if let Ok(guard) = inner.lock() {
                    if let Some(ref wv) = *guard {
                        release_web_focus_locked(wv, &web_wants);
                    }
                }
                unfocus.store(true, Ordering::SeqCst);
                notify_wakeup(&wakeup);
                return event.as_ptr();
            }
            // Re-assert WKWebView first responder for IME / VoiceOver / synthetic keys.
            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    focus_webview(wv, "focus on keydown");
                }
            }
            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    let Some(window_point) = current_window_point(parent_ns_view) else {
                        unfocus.store(true, Ordering::SeqCst);
                        notify_wakeup(&wakeup);
                        return event.as_ptr();
                    };
                    if !window_point_hits_webview(wv, window_point) {
                        unfocus.store(true, Ordering::SeqCst);
                        notify_wakeup(&wakeup);
                    }
                }
            }
            event.as_ptr()
        })
    };

    let keydown_mask = NSEventMask::KeyDown;
    let keydown_monitor = unsafe {
        NSEvent::addLocalMonitorForEventsMatchingMask_handler(keydown_mask, &keydown_block)
    }
    .ok_or("failed to install NSEvent keydown monitor")?;

    let flags_block = {
        let inner = inner.clone();
        let web_wants = web_wants_keyboard.clone();
        let unfocus = mac_tk_unfocus.clone();
        let wakeup = wakeup_write_fd.clone();
        RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
            if !web_wants.load(Ordering::SeqCst) {
                return event.as_ptr();
            }
            let event_ref = unsafe { event.as_ref() };
            const TAB_KEY_CODE: u16 = 48;
            if event_ref.keyCode() == TAB_KEY_CODE {
                return event.as_ptr();
            }
            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    focus_webview(wv, "focus on flags changed");
                    let Some(window_point) = current_window_point(parent_ns_view) else {
                        unfocus.store(true, Ordering::SeqCst);
                        notify_wakeup(&wakeup);
                        return event.as_ptr();
                    };
                    if !window_point_hits_webview(wv, window_point) {
                        unfocus.store(true, Ordering::SeqCst);
                        notify_wakeup(&wakeup);
                    }
                }
            }
            event.as_ptr()
        })
    };

    let flags_mask = NSEventMask::FlagsChanged;
    let flags_monitor = unsafe {
        NSEvent::addLocalMonitorForEventsMatchingMask_handler(flags_mask, &flags_block)
    }
    .ok_or("failed to install NSEvent flags monitor")?;

    let key_block = {
        let inner = inner.clone();
        let web_wants = web_wants_keyboard.clone();
        let unfocus = mac_tk_unfocus.clone();
        let wakeup = wakeup_write_fd.clone();
        RcBlock::new(move |_notification: NonNull<NSNotification>| {
            if !web_wants.load(Ordering::SeqCst) {
                return;
            }
            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    // Keyboard-only window activation (Alt+Tab, etc.): restore web
                    // input without requiring a mouse hit-test.
                    focus_webview(wv, "focus on window key");
                    unfocus.store(true, Ordering::SeqCst);
                    notify_wakeup(&wakeup);
                } else {
                    web_wants.store(false, Ordering::SeqCst);
                }
            }
        })
    };

    let key_observer = unsafe {
        NSNotificationCenter::defaultCenter().addObserverForName_object_queue_usingBlock(
            Some(NSWindowDidBecomeKeyNotification),
            Some(&ns_window),
            Some(NSOperationQueue::mainQueue().as_ref()),
            &key_block,
        )
    };
    let key_observer = unsafe { Retained::cast_unchecked(key_observer) };

    Ok(FocusSyncGuard {
        click_monitor,
        keydown_monitor,
        flags_monitor,
        key_observer,
    })
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

/// Whether *window_point* lies inside the WKWebView using AppKit conversion.
fn window_point_hits_webview(wv: &wry::WebView, window_point: NSPoint) -> bool {
    let wk = wv.webview();
    let local = wk.convertPoint_fromView(window_point, None);
    point_in_ns_rect(local, wk.bounds())
}

fn point_in_ns_rect(point: NSPoint, rect: NSRect) -> bool {
    point.x >= rect.origin.x
        && point.y >= rect.origin.y
        && point.x < rect.origin.x + rect.size.width
        && point.y < rect.origin.y + rect.size.height
}

fn current_window_point(parent_ns_view: NonNull<NSView>) -> Option<NSPoint> {
    let _mtm = MainThreadMarker::new()?;
    unsafe {
        let parent = parent_ns_view.as_ref();
        let window = parent.window()?;
        let screen = NSEvent::mouseLocation();
        Some(window.convertPointFromScreen(screen))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
