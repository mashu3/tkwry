//! Single entry for Linux GTK initialization.
//!
//! WebKitGTK / wry require ``gtk::init`` before ``WebContext`` or
//! ``build_as_child``. Call sites must go through [`ensure_gtk_initialized`]
//! instead of calling ``gtk::init`` directly.

/// Ensure GTK is initialized (idempotent).
///
/// Returns ``Err`` only when init fails and GTK is still not initialized
/// (typically a missing ``$DISPLAY``). Already-initialized processes treat a
/// late ``gtk::init`` error as success.
pub(crate) fn ensure_gtk_initialized() -> Result<(), String> {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Err(e) = gtk::init() {
            if !gtk::is_initialized() {
                return Err(format!("GTK init failed: {e}. Is $DISPLAY set?"));
            }
        }
    }
    Ok(())
}
