//! Shared wry [`WebContext`] exposed as ``WebSession``.

use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

/// Stable 16-byte id for macOS ``WKWebsiteDataStore`` (path-derived).
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

pub(crate) struct WebSessionState {
    pub(crate) context: wry::WebContext,
    pub(crate) ephemeral: bool,
    pub(crate) data_directory: Option<PathBuf>,
    /// Linux registers ``tkwry`` once per context; remember the app root.
    pub(crate) registered_app_root: Option<PathBuf>,
    #[cfg(target_os = "macos")]
    pub(crate) data_store_id: Option<[u8; 16]>,
}

impl WebSessionState {
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

        // wry's ephemeral constructor is crate-private; use a normal context and
        // ``with_incognito(true)`` on each WebView (see WebView::new).
        let context = wry::WebContext::new(data_directory.clone());

        #[cfg(target_os = "macos")]
        let data_store_id = data_directory
            .as_ref()
            .filter(|_| !ephemeral)
            .map(|p| data_store_id_for_path(p));

        Ok(Self {
            context,
            ephemeral,
            data_directory,
            registered_app_root: None,
            #[cfg(target_os = "macos")]
            data_store_id,
        })
    }
}

/// Shared browser profile for one or more :class:`~tkwry.WebView` instances.
///
/// Maps to wry's ``WebContext`` (cookies / cache / localStorage where the
/// platform supports it). Keep the session alive while any WebView uses it.
#[pyclass(name = "WebSession", unsendable)]
pub struct WebSession {
    pub(crate) state: Arc<Mutex<WebSessionState>>,
}

#[pymethods]
impl WebSession {
    #[new]
    #[pyo3(signature = (*, data_directory = None, ephemeral = false))]
    fn new(data_directory: Option<String>, ephemeral: bool) -> PyResult<Self> {
        let data_directory = data_directory.map(PathBuf::from);
        let state = WebSessionState::new(data_directory, ephemeral)?;
        Ok(Self {
            state: Arc::new(Mutex::new(state)),
        })
    }

    /// Absolute data directory, or ``None`` for the platform default / ephemeral.
    #[getter]
    fn data_directory(&self) -> PyResult<Option<String>> {
        let guard = self
            .state
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
            .state
            .lock()
            .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("WebSession lock poisoned"))?;
        Ok(guard.ephemeral)
    }
}

impl WebSession {
    pub(crate) fn state_arc(&self) -> Arc<Mutex<WebSessionState>> {
        self.state.clone()
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
}
