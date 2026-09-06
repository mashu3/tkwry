//! Per-WebView clip containers for shared Tk ``NSView`` hosts.
//!
//! Tk child frames do not receive their own ``NSView``; wry child webviews are
//! siblings on the toplevel content view. WebKit DevTools can expand a bare
//! ``WKWebView`` to the parent view's geometry. Wrapping each webview in a
//! fixed-size container keeps the inspector inside the Tk frame bounds.

use std::cell::Cell;
use std::ptr::NonNull;

use objc2::rc::Retained;
use objc2::MainThreadMarker;
use objc2_app_kit::{NSAutoresizingMaskOptions, NSView, NSWindowOrderingMode};
use objc2_foundation::{NSPoint, NSRect, NSSize};
use wry::WebViewExtMacOS;

fn embed_origin(view: &NSView, x: i32, y: i32, height: f64) -> NSPoint {
    if view.isFlipped() {
        NSPoint::new(x as f64, y as f64)
    } else {
        let frame = view.frame();
        NSPoint::new(x as f64, frame.size.height - y as f64 - height)
    }
}

fn enable_container_clipping(container: &NSView) {
    // Layer-backed clipping is what platforms.md describes as masksToBounds.
    // clipsToBounds covers newer AppKit; both stay in sync with the docs.
    container.setWantsLayer(true);
    container.setClipsToBounds(true);
    if let Some(layer) = container.layer() {
        layer.setMasksToBounds(true);
    }
}

pub struct MacClipHost {
    parent: Retained<NSView>,
    container: Retained<NSView>,
    attached: Cell<bool>,
}

impl MacClipHost {
    pub fn new(parent: NonNull<NSView>) -> Result<Self, String> {
        let _mtm = MainThreadMarker::new().ok_or("macOS clip host requires the main thread")?;
        let parent = unsafe {
            Retained::retain(parent.as_ptr()).ok_or("failed to retain embed parent NSView")?
        };
        let container = NSView::new(_mtm);
        container.setAutoresizingMask(NSAutoresizingMaskOptions::ViewNotSizable);
        enable_container_clipping(&container);
        Ok(Self {
            parent,
            container,
            attached: Cell::new(false),
        })
    }

    fn ensure_attached(&self, wv: &wry::WebView) {
        if self.attached.get() {
            return;
        }
        let wk = wv.webview();
        wk.removeFromSuperview();
        self.container.addSubview(&wk);
        self.parent.addSubview(&self.container);
        wk.setAutoresizingMask(NSAutoresizingMaskOptions::ViewNotSizable);
        self.attached.set(true);
    }

    pub fn set_bounds(
        &self,
        wv: &wry::WebView,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
    ) -> Result<(), String> {
        self.ensure_attached(wv);
        let width = width.max(1.0);
        let height = height.max(1.0);
        let x_i = x.round() as i32;
        let y_i = y.round() as i32;

        let container_frame = NSRect::new(
            embed_origin(&self.parent, x_i, y_i, height),
            NSSize::new(width, height),
        );
        self.container.setFrame(container_frame);

        let wk = wv.webview();
        wk.setFrame(NSRect::new(
            NSPoint::new(0.0, 0.0),
            NSSize::new(width, height),
        ));
        Ok(())
    }

    pub fn set_visible(&self, wv: &wry::WebView, visible: bool) -> Result<(), String> {
        self.ensure_attached(wv);
        self.container.setHidden(!visible);
        // Create-time ``visible=False`` hides the WKWebView itself; unhiding
        // only the clip container would leave a blank frame after Map.
        wv.set_visible(visible)
            .map_err(|e| format!("macOS clip set_visible failed: {e}"))
    }

    pub fn raise_to_front(&self) {
        self.parent.addSubview_positioned_relativeTo(
            &self.container,
            NSWindowOrderingMode::Above,
            None,
        );
    }

    pub fn teardown(&self) {
        self.container.removeFromSuperview();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clip_container_enables_masks_to_bounds() {
        let Some(mtm) = MainThreadMarker::new() else {
            // Default ``cargo test`` workers are not the AppKit main thread.
            eprintln!("skipping clip masksToBounds assert (not on AppKit main thread)");
            return;
        };
        let parent = NSView::new(mtm);
        let parent_ptr =
            NonNull::new(Retained::as_ptr(&parent) as *mut NSView).expect("parent view");
        let host = MacClipHost::new(parent_ptr).expect("clip host");
        assert!(host.container.wantsLayer());
        assert!(host.container.clipsToBounds());
        let layer = host.container.layer().expect("backing layer");
        assert!(layer.masksToBounds());
    }

    #[test]
    fn clip_host_new_requires_main_thread() {
        let err = std::thread::spawn(|| {
            // Dangling NonNull only exercises the main-thread gate before retain.
            MacClipHost::new(NonNull::<NSView>::dangling()).err()
        })
        .join()
        .expect("thread")
        .expect("expected Err off the main thread");
        assert!(err.contains("main thread"));
    }
}
