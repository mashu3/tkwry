//! Shared wry [`WebContext`] exposed as ``WebSession``.

use std::cell::RefCell;
#[cfg(any(target_os = "macos", test))]
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

/// Stable 16-byte id for macOS ``WKWebsiteDataStore`` (path-derived).
#[cfg(any(target_os = "macos", test))]
pub(crate) fn data_store_id_for_path(path: &Path) -> [u8; 16] {
    let mut out = [0u8; 16];
    let mut h1 = std::collections::hash_map::DefaultHasher::new();
    path.hash(&mut h1);
    0x9e37_79b9_7f4a_7c15u64.hash(&mut h1);
    out[..8].copy_from_slice(&h1.finish().to_le_bytes());
    let mut h2 = std::collections::hash_map::DefaultHasher::new();
    path.hash(&mut h2);
    0x243f_6a88_85a3_08d3u64.hash(&mut h2);
    out[8..].copy_from_slice(&h2.finish().to_le_bytes());
    // RFC 4122 variant / version bits (version 5-ish marker).
    out[6] = (out[6] & 0x0f) | 0x50;
    out[8] = (out[8] & 0x3f) | 0x80;
    out
}

pub(crate) struct WebSessionMeta {
    pub(crate) ephemeral: bool,
    pub(crate) data_directory: Option<PathBuf>,
    /// Linux registers ``tkwry`` once per context; remember the app root.
    pub(crate) registered_app_root: Option<PathBuf>,
    /// Serve options committed with the first successful ``app=`` create.
    pub(crate) registered_app_serve_options: Option<crate::app_protocol::AppServeOptions>,
    #[cfg(target_os = "macos")]
    pub(crate) data_store_id: Option<[u8; 16]>,
}

impl WebSessionMeta {
    fn new(data_directory: Option<PathBuf>, ephemeral: bool) -> PyResult<Self> {
        if ephemeral && data_directory.is_some() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "WebSession: pass data_directory= or ephemeral=True, not both",
            ));
        }
        if let Some(ref dir) = data_directory {
            std::fs::create_dir_all(dir).map_err(|e| {
                pyo3::exceptions::PyOSError::new_err(format!(
                    "WebSession: cannot create data_directory {}: {e}",
                    dir.display()
                ))
            })?;
        }

        #[cfg(target_os = "macos")]
        let data_store_id = data_directory
            .as_ref()
            .filter(|_| !ephemeral)
            .map(|p| data_store_id_for_path(p));

        Ok(Self {
            ephemeral,
            data_directory,
            registered_app_root: None,
            registered_app_serve_options: None,
            #[cfg(target_os = "macos")]
            data_store_id,
        })
    }
}

/// Keeps shared ``WebContext`` alive for attached WebViews.
#[derive(Clone)]
pub(crate) struct SessionRefs {
    pub(crate) meta: Arc<Mutex<WebSessionMeta>>,
    pub(crate) context: Arc<RefCell<wry::WebContext>>,
    pub(crate) create_lock: Arc<Mutex<()>>,
}

impl SessionRefs {
    pub(crate) fn lock_meta(&self) -> PyResult<std::sync::MutexGuard<'_, WebSessionMeta>> {
        self.meta
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("WebSession lock poisoned"))
    }
}

/// Whether ``with_custom_protocol`` must be attached on this builder.
///
/// On Linux the scheme is registered once per shared ``WebContext`` after a
/// successful create. ``registered_app_root`` is committed only after
/// ``build_as_child`` succeeds so a failed first attempt can retry.
pub(crate) fn should_attach_app_protocol(
    registered_app_root: Option<&PathBuf>,
    app_root: &PathBuf,
) -> bool {
    match registered_app_root {
        None => true,
        Some(existing) if existing == app_root => {
            cfg!(any(target_os = "windows", target_os = "macos"))
        }
        Some(_) => false,
    }
}

pub(crate) fn commit_registered_app_root(state: &mut WebSessionMeta, app_root: &Path) {
    if state.ephemeral || state.registered_app_root.is_some() {
        return;
    }
    state.registered_app_root = Some(app_root.to_path_buf());
}

pub(crate) fn commit_registered_app_serve_options(
    state: &mut WebSessionMeta,
    options: crate::app_protocol::AppServeOptions,
) {
    if state.ephemeral || state.registered_app_serve_options.is_some() {
        return;
    }
    state.registered_app_serve_options = Some(options);
}

/// Shared browser profile for one or more :class:`~tkwry.WebView` instances.
///
/// Maps to wry's ``WebContext`` (cookies / cache / localStorage where the
/// platform supports it). Keep the session alive while any WebView uses it.
#[pyclass(name = "WebSession", unsendable)]
pub struct WebSession {
    /// Session metadata (app root registration, ephemeral flag, …).
    pub(crate) meta: Arc<Mutex<WebSessionMeta>>,
    /// Shared wry context; borrowed mutably during ``build_as_child`` on the Tk thread.
    #[allow(clippy::arc_with_non_send_sync)]
    pub(crate) context: Arc<RefCell<wry::WebContext>>,
    /// Serializes native create on this session so ``WebContext`` is not double-borrowed.
    pub(crate) create_lock: Arc<Mutex<()>>,
}

#[pymethods]
impl WebSession {
    #[new]
    #[pyo3(signature = (*, data_directory = None, ephemeral = false))]
    fn new(data_directory: Option<String>, ephemeral: bool) -> PyResult<Self> {
        // WebContext construction uses WebKitGTK APIs that require gtk::init.
        #[cfg(all(unix, not(target_os = "macos")))]
        {
            if let Err(e) = gtk::init() {
                if !gtk::is_initialized() {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "GTK init failed: {e}. Is $DISPLAY set?"
                    )));
                }
            }
        }
        let data_directory = data_directory.map(PathBuf::from);
        let meta = WebSessionMeta::new(data_directory.clone(), ephemeral)?;
        // wry's ephemeral constructor is crate-private; use a normal context and
        // ``with_incognito(true)`` on each WebView (see WebView::new).
        let context = wry::WebContext::new(data_directory);
        Ok(Self {
            meta: Arc::new(Mutex::new(meta)),
            #[allow(clippy::arc_with_non_send_sync)]
            context: Arc::new(RefCell::new(context)),
            create_lock: Arc::new(Mutex::new(())),
        })
    }

    /// Absolute data directory, or ``None`` for the platform default / ephemeral.
    #[getter]
    fn data_directory(&self) -> PyResult<Option<String>> {
        let guard = self
            .meta
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("WebSession lock poisoned"))?;
        Ok(guard
            .data_directory
            .as_ref()
            .map(|p| p.to_string_lossy().into_owned()))
    }

    /// Whether this session is private / non-persistent.
    #[getter]
    fn ephemeral(&self) -> PyResult<bool> {
        let guard = self
            .meta
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("WebSession lock poisoned"))?;
        Ok(guard.ephemeral)
    }
}

impl WebSession {
    pub(crate) fn session_refs(&self) -> SessionRefs {
        SessionRefs {
            meta: self.meta.clone(),
            context: self.context.clone(),
            create_lock: self.create_lock.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_store_id_is_stable_for_same_path() {
        let a = data_store_id_for_path(Path::new("/tmp/tkwry-profile"));
        let b = data_store_id_for_path(Path::new("/tmp/tkwry-profile"));
        let c = data_store_id_for_path(Path::new("/tmp/other"));
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    #[test]
    fn failed_create_retry_still_attaches_app_protocol_on_linux() {
        let root = PathBuf::from("/tmp/tkwry-app");
        assert!(should_attach_app_protocol(None, &root));
        assert!(should_attach_app_protocol(None, &root));
        let mut state = WebSessionMeta {
            ephemeral: false,
            data_directory: None,
            registered_app_root: None,
            registered_app_serve_options: None,
            #[cfg(target_os = "macos")]
            data_store_id: None,
        };
        commit_registered_app_root(&mut state, &root);
        assert_eq!(state.registered_app_root.as_deref(), Some(root.as_path()));
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        assert!(!should_attach_app_protocol(
            state.registered_app_root.as_ref(),
            &root
        ));
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        assert!(should_attach_app_protocol(
            state.registered_app_root.as_ref(),
            &root
        ));
    }

    #[test]
    fn app_serve_options_must_match_on_shared_session() {
        use crate::app_protocol::{validate_app_serve_options, AppServeOptions};

        let first = AppServeOptions {
            spa_fallback: true,
            csp: Some("default-src 'self'".into()),
            ..AppServeOptions::default()
        };
        let same = first.clone();
        let different = AppServeOptions {
            coop: true,
            ..first.clone()
        };
        assert!(validate_app_serve_options(None, &first).is_ok());
        assert!(validate_app_serve_options(Some(&first), &same).is_ok());
        assert!(validate_app_serve_options(Some(&first), &different).is_err());
    }
}
