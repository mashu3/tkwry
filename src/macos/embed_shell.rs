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

    pub fn set_visible(&self, wv: &wry::WebView, visible: bool) {
        self.ensure_attached(wv);
        self.container.setHidden(!visible);
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
