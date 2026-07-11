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
use wry::dpi::{LogicalPosition, LogicalSize, Position, Size};

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
    let _ = unsafe { libc::write(fd, &byte as *const u8 as *const libc::c_void, 1) };
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

            let in_web = if let Ok(guard) = inner.lock() {
                guard
                    .as_ref()
                    .is_some_and(|wv| point_in_wry_bounds(wv, wry_point))
            } else {
                false
            };

            if in_web {
                web_wants.store(true, Ordering::SeqCst);
                unfocus.store(true, Ordering::SeqCst);
                if let Ok(guard) = inner.lock() {
                    if let Some(ref wv) = *guard {
                        let _ = wv.focus();
                    }
                }
                notify_wakeup(&wakeup);
            } else if web_wants.load(Ordering::SeqCst) {
                release_web_focus(&inner, &web_wants);
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
                    let _ = wv.focus();
                }
            }
            unfocus.store(true, Ordering::SeqCst);
            notify_wakeup(&wakeup);
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

/// Return keyboard from the webview to Tk **only when the webview owns it**.
fn release_web_focus(inner: &Arc<Mutex<Option<wry::WebView>>>, web_wants: &Arc<AtomicBool>) {
    web_wants.store(false, Ordering::SeqCst);
    if let Ok(guard) = inner.lock() {
        if let Some(ref wv) = *guard {
            let _ = wv.focus_parent();
        }
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

fn point_in_wry_bounds(wv: &wry::WebView, wry_point: NSPoint) -> bool {
    let bounds = match wv.bounds() {
        Ok(bounds) => bounds,
        Err(_) => return false,
    };

    let (x, y) = match bounds.position {
        Position::Logical(LogicalPosition { x, y }) => (x, y),
        Position::Physical(p) => (p.x as f64, p.y as f64),
    };
    let (width, height) = match bounds.size {
        Size::Logical(LogicalSize { width, height }) => (width, height),
        Size::Physical(s) => (s.width as f64, s.height as f64),
    };

    wry_point.x >= x && wry_point.x < x + width && wry_point.y >= y && wry_point.y < y + height
}
