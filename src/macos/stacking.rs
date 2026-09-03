//! macOS sibling WKWebView z-order inside a shared Tk NSView host.

use objc2_app_kit::NSWindowOrderingMode;
use wry::WebViewExtMacOS;

/// Raise *wv* above sibling subviews in the shared embed parent.
pub fn raise_webview(wv: &wry::WebView) -> Result<(), String> {
    let wk = wv.webview();
    let Some(superview) = (unsafe { wk.superview() }) else {
        return Ok(());
    };
    superview.addSubview_positioned_relativeTo(&wk, NSWindowOrderingMode::Above, None);
    Ok(())
}
