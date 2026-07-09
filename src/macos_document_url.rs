//! macOS: read the document URL without wry's panicking ``url()`` wrapper.
//!
//! wry 0.55's ``url_from_webview`` uses ``Option::unwrap()`` when WebKit has no
//! ``NSURL`` (inline HTML). objc2-web-kit exposes ``URL()`` as ``Option``, so we
//! can distinguish "no document URL" from real WebKit/wry failures.

use std::ffi::c_char;
use std::ptr::NonNull;

use objc2::rc::Retained;
use objc2::ClassType;
use objc2::MainThreadMarker;
use objc2_app_kit::NSView;
use objc2_foundation::{NSObjectProtocol, NSString};
use objc2_web_kit::WKWebView;
use wry::dpi::{LogicalPosition, LogicalSize, Position, Size};

pub fn read_document_url(
    wv: &wry::WebView,
    parent_ns_view: NonNull<NSView>,
) -> Result<Option<String>, String> {
    let _mtm = MainThreadMarker::new().ok_or("document URL requires the main thread")?;
    let bounds = wv.bounds().map_err(|err| err.to_string())?;
    let parent = unsafe { parent_ns_view.as_ref() };
    let wk = find_wkwebview(parent, &bounds)?;
    let url_obj = unsafe { wk.URL() };
    let Some(url_obj) = url_obj else {
        return Ok(None);
    };
    nsurl_to_string(&url_obj).map(Some)
}

fn find_wkwebview(parent: &NSView, bounds: &wry::Rect) -> Result<Retained<WKWebView>, String> {
    let mut matches = Vec::new();
    collect_wkwebviews(parent, &mut matches);
    match matches.len() {
        0 => Err("WKWebView not found under embed parent".into()),
        1 => Ok(matches.pop().expect("length checked")),
        _ => matches
            .into_iter()
            .find(|wk| frame_matches_bounds(wk.frame(), bounds))
            .ok_or_else(|| "multiple WKWebViews under embed parent; none matched bounds".into()),
    }
}

fn collect_wkwebviews(view: &NSView, out: &mut Vec<Retained<WKWebView>>) {
    for subview in view.subviews().iter() {
        if subview.isKindOfClass(WKWebView::class()) {
            let wk = unsafe { Retained::cast_unchecked::<WKWebView>(subview.clone()) };
            out.push(wk);
        }
        collect_wkwebviews(&subview, out);
    }
}

fn frame_matches_bounds(frame: objc2_foundation::NSRect, bounds: &wry::Rect) -> bool {
    let (x, y) = match bounds.position {
        Position::Logical(LogicalPosition { x, y }) => (x, y),
        Position::Physical(p) => (p.x as f64, p.y as f64),
    };
    let (width, height) = match bounds.size {
        Size::Logical(LogicalSize { width, height }) => (width, height),
        Size::Physical(s) => (s.width as f64, s.height as f64),
    };

    const EPS: f64 = 0.5;
    (frame.origin.x - x).abs() < EPS
        && (frame.origin.y - y).abs() < EPS
        && (frame.size.width - width).abs() < EPS
        && (frame.size.height - height).abs() < EPS
}

fn nsurl_to_string(url: &objc2_foundation::NSURL) -> Result<String, String> {
    let absolute_url = url.absoluteString().ok_or("NSURL.absoluteString is nil")?;
    nsstring_to_string(&absolute_url)
}

fn nsstring_to_string(value: &NSString) -> Result<String, String> {
    let bytes = {
        let bytes: *const c_char = value.UTF8String();
        bytes as *const u8
    };
    // NSUTF8StringEncoding == 4
    let len = value.lengthOfBytesUsingEncoding(4);
    let bytes = unsafe { std::slice::from_raw_parts(bytes, len) };
    std::str::from_utf8(bytes)
        .map(Into::into)
        .map_err(|err| err.to_string())
}
