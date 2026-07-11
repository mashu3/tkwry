//! macOS: disable automatic window tabbing for Tk-hosted WebViews.

use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, Ordering};

use objc2::MainThreadMarker;
use objc2_app_kit::{NSView, NSWindow, NSWindowTabbingMode};

static AUTOMATIC_WINDOW_TABBING_DISABLED: AtomicBool = AtomicBool::new(false);

const MAIN_THREAD_ERROR: &str = "macOS window tabbing requires the main thread";

/// Opt out of macOS automatic window tabs process-wide.
///
/// Must run before the first ``Tk()`` call; otherwise AppKit may reserve a tab
/// bar in the titlebar chrome (double title strip) for the process.
pub fn disable_automatic_window_tabbing() -> Result<(), String> {
    let mtm = MainThreadMarker::new().ok_or(MAIN_THREAD_ERROR)?;

    if AUTOMATIC_WINDOW_TABBING_DISABLED.swap(true, Ordering::SeqCst) {
        return Ok(());
    }

    NSWindow::setAllowsAutomaticWindowTabbing(false, mtm);
    Ok(())
}

/// Mark the host window as non-tabbed.
pub fn disable_window_tabbing(parent_ns_view: NonNull<NSView>) -> Result<(), String> {
    let _mtm = MainThreadMarker::new().ok_or(MAIN_THREAD_ERROR)?;

    disable_automatic_window_tabbing()?;

    let parent = unsafe { parent_ns_view.as_ref() };
    let Some(window) = parent.window() else {
        return Err("macOS window tabbing requires an NSWindow parent".into());
    };
    window.setTabbingMode(NSWindowTabbingMode::Disallowed);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disable_automatic_window_tabbing_requires_main_thread() {
        let result = std::thread::spawn(disable_automatic_window_tabbing).join();
        assert!(result.unwrap().is_err());
    }
}
