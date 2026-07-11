//! macOS: read the document URL without wry's panicking ``url()`` wrapper.
//!
//! wry 0.55's ``url_from_webview`` uses ``Option::unwrap()`` when WebKit has no
//! ``NSURL`` (inline HTML). objc2-web-kit exposes ``URL()`` as ``Option``, so we
//! can distinguish "no document URL" from real WebKit/wry failures.

use std::ffi::c_char;

use objc2::MainThreadMarker;
use objc2_foundation::{NSString, NSURL};
use wry::WebViewExtMacOS;

/// Foundation encoding constant for UTF-8 (`NSUTF8StringEncoding`).
const NS_UTF8_STRING_ENCODING: usize = 4;

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
    let ptr: *const c_char = value.UTF8String();
    if ptr.is_null() {
        return Err("NSString.UTF8String returned null".into());
    }
    let len = value.lengthOfBytesUsingEncoding(NS_UTF8_STRING_ENCODING);
    utf8_buffer_to_string(ptr.cast(), len)
}

/// Decode ``len`` UTF-8 bytes from a Foundation ``UTF8String`` buffer.
///
/// Uses Foundation's byte length rather than ``CStr`` so embedded NUL code
/// units do not truncate the read when they are present in the NSString.
fn utf8_buffer_to_string(ptr: *const u8, len: usize) -> Result<String, String> {
    if len == 0 {
        return Ok(String::new());
    }
    if ptr.is_null() {
        return Err("UTF-8 buffer pointer is null".into());
    }
    let bytes = unsafe { std::slice::from_raw_parts(ptr, len) };
    std::str::from_utf8(bytes)
        .map(Into::into)
        .map_err(|err| err.to_string())
}

#[cfg(test)]
mod tests {
    use std::ffi::CString;

    use super::*;

    #[test]
    fn utf8_buffer_to_string_rejects_null_pointer() {
        assert!(utf8_buffer_to_string(std::ptr::null(), 4).is_err());
    }

    #[test]
    fn utf8_buffer_to_string_empty_len_is_empty_string() {
        assert_eq!(utf8_buffer_to_string(b"x".as_ptr(), 0).unwrap(), "");
    }

    #[test]
    fn utf8_buffer_to_string_reads_embedded_nul_bytes() {
        let raw = b"before\0after";
        assert_eq!(
            utf8_buffer_to_string(raw.as_ptr(), raw.len()).unwrap(),
            "before\u{0}after"
        );
    }

    #[test]
    fn utf8_buffer_to_string_reads_c_string_without_trailing_nul() {
        let value = CString::new("https://example.com").unwrap();
        let bytes = value.as_bytes_with_nul();
        assert_eq!(
            utf8_buffer_to_string(bytes.as_ptr(), bytes.len() - 1).unwrap(),
            "https://example.com"
        );
    }
}
