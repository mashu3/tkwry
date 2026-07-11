//! macOS-specific WebView integration (focus, document URL, window tabbing).

mod document_url;
mod focus;
mod window;

pub use document_url::read_document_url;
pub use focus::{hit_test_wry_point, install_focus_sync, notify_wakeup, FocusSyncGuard};
pub use window::{disable_process_automatic_window_tabbing, disable_window_tabbing};

use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::{Arc, Mutex};

use objc2_app_kit::NSView;

/// Per-WebView macOS state (focus sync, keyboard routing flags).
pub struct MacPlatformState {
    focus_sync: Mutex<Option<FocusSyncGuard>>,
    web_wants_keyboard: Arc<AtomicBool>,
    mac_tk_unfocus: Arc<AtomicBool>,
}

impl MacPlatformState {
    pub fn install(
        inner: Arc<Mutex<Option<wry::WebView>>>,
        parent_ns_view: NonNull<NSView>,
        wakeup_write_fd: Arc<AtomicI32>,
    ) -> Result<Self, String> {
        let web_wants_keyboard = Arc::new(AtomicBool::new(false));
        let mac_tk_unfocus = Arc::new(AtomicBool::new(false));
        let guard = install_focus_sync(
            inner,
            parent_ns_view,
            web_wants_keyboard.clone(),
            mac_tk_unfocus.clone(),
            wakeup_write_fd,
        )?;
        Ok(Self {
            focus_sync: Mutex::new(Some(guard)),
            web_wants_keyboard,
            mac_tk_unfocus,
        })
    }

    pub fn teardown(&self) {
        self.web_wants_keyboard.store(false, Ordering::SeqCst);
        self.mac_tk_unfocus.store(false, Ordering::SeqCst);
        if let Ok(mut guard) = self.focus_sync.lock() {
            *guard = None;
        }
    }

    pub fn set_web_input_active(&self, active: bool) {
        self.web_wants_keyboard.store(active, Ordering::SeqCst);
    }

    pub fn web_input_active(&self) -> bool {
        self.web_wants_keyboard.load(Ordering::SeqCst)
    }

    pub fn take_tk_unfocus(&self) -> bool {
        self.mac_tk_unfocus.swap(false, Ordering::SeqCst)
    }

    pub fn tk_unfocus_pending(&self) -> bool {
        self.mac_tk_unfocus.load(Ordering::SeqCst)
    }

    pub fn request_tk_unfocus(&self, wakeup_write_fd: &AtomicI32) {
        self.web_wants_keyboard.store(true, Ordering::SeqCst);
        self.mac_tk_unfocus.store(true, Ordering::SeqCst);
        notify_wakeup(wakeup_write_fd);
    }

    pub fn release_web_input(&self) {
        self.web_wants_keyboard.store(false, Ordering::SeqCst);
    }
}
