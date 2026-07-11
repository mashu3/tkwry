//! macOS: read the document URL without wry's panicking ``url()`` wrapper.
//!
//! wry 0.55's ``url_from_webview`` uses ``Option::unwrap()`` when WebKit has no
//! ``NSURL`` (inline HTML). objc2-web-kit exposes ``URL()`` as ``Option``, so we
//! can distinguish "no document URL" from real WebKit/wry failures.

use std::ffi::c_char;

use objc2::MainThreadMarker;
use objc2_foundation::{NSString, NSURL};
use wry::WebViewExtMacOS;

pub fn read_document_url(wv: &wry::WebView) -> Result<Option<String>, String> {
    let _mtm = MainThreadMarker::new().ok_or("document URL requires the main thread")?;
    let wk = wv.webview();
    let url_obj = unsafe { wk.URL() };
    let Some(url_obj) = url_obj else {
        return Ok(None);
    };
    nsurl_to_string(&url_obj).map(Some)
}

fn nsurl_to_string(url: &NSURL) -> Result<String, String> {
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
