//! macOS: route keyboard focus between WKWebView and Tk at the NSEvent layer.
//!
//! wry ``set_bounds`` uses top-left logical coordinates; AppKit mouse points in
//! the embed parent may use bottom-left unless the view is flipped.  Hit
//! testing must convert before comparing to ``WebView::bounds()``.
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
use objc2_foundation::{NSNotification, NSNotificationCenter, NSOperationQueue, NSPoint};
use wry::dpi::{LogicalPosition, LogicalSize};
use wry::WebViewExtMacOS;

pub struct FocusSyncGuard {
    click_monitor: Retained<AnyObject>,
    key_observer: Retained<AnyObject>,
}

impl Drop for FocusSyncGuard {
    fn drop(&mut self) {
        unsafe {
            NSEvent::removeMonitor(&self.click_monitor);
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

            let parent = unsafe { parent_ns_view.as_ref() };
            let parent_point = parent.convertPoint_fromView(event_ref.locationInWindow(), None);
            let wry_point = parent_point_to_wry(parent, parent_point);

            if let Ok(guard) = inner.lock() {
                if let Some(ref wv) = *guard {
                    if point_in_wry_bounds(wv, wry_point) {
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
                    let Some(wry_point) = current_mouse_wry_point(parent_ns_view) else {
                        return;
                    };
                    if point_in_wry_bounds(wv, wry_point) {
                        focus_webview(wv, "focus on window key");
                        unfocus.store(true, Ordering::SeqCst);
                        notify_wakeup(&wakeup);
                    } else {
                        release_web_focus_locked(wv, &web_wants);
                    }
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

fn current_mouse_wry_point(parent_ns_view: NonNull<NSView>) -> Option<NSPoint> {
    let _mtm = MainThreadMarker::new()?;
    unsafe {
        let parent = parent_ns_view.as_ref();
        let window = parent.window()?;
        let screen = NSEvent::mouseLocation();
        let window_point = window.convertPointFromScreen(screen);
        let parent_point = parent.convertPoint_fromView(window_point, None);
        Some(parent_point_to_wry(parent, parent_point))
    }
}

/// Convert a point in the embed parent's AppKit space to wry top-left space.
fn parent_point_to_wry(parent: &NSView, point: NSPoint) -> NSPoint {
    if parent.isFlipped() {
        point
    } else {
        let height = parent.frame().size.height;
        NSPoint::new(point.x, height - point.y)
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
