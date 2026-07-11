//! macOS: disable automatic window tabbing for Tk-hosted WebViews.

use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, Ordering};

use objc2::MainThreadMarker;
use objc2_app_kit::{NSApplication, NSView, NSWindow, NSWindowTabbingMode};

static AUTOMATIC_WINDOW_TABBING_DISABLED: AtomicBool = AtomicBool::new(false);

/// Opt out of macOS automatic window tabs process-wide.
///
/// Must run before the first ``Tk()`` call; otherwise AppKit may reserve a tab
/// bar in the titlebar chrome (double title strip) for the process.
pub fn disable_automatic_window_tabbing() {
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };

    if AUTOMATIC_WINDOW_TABBING_DISABLED.swap(true, Ordering::SeqCst) {
        return;
    }

    NSWindow::setAllowsAutomaticWindowTabbing(false, mtm);
}

/// Mark the host window and any existing AppKit windows as non-tabbed.
pub fn disable_window_tabbing(parent_ns_view: NonNull<NSView>) {
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };

    disable_automatic_window_tabbing();

    let app = NSApplication::sharedApplication(mtm);
    for window in app.windows().iter() {
        window.setTabbingMode(NSWindowTabbingMode::Disallowed);
    }

    let parent = unsafe { parent_ns_view.as_ref() };
    if let Some(window) = parent.window() {
        window.setTabbingMode(NSWindowTabbingMode::Disallowed);
    }
}
