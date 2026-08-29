//! Python ``Cookie`` type and conversion to/from wry's ``cookie::Cookie``.
//!
//! Never put cookie **values** in ``__repr__`` / logs / exceptions.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use wry::cookie::{self, Expiration, SameSite};

/// Cookie for :meth:`~tkwry.WebView.cookies` / ``set_cookie`` / ``delete_cookie``.
///
/// Mirrors wry's ``cookie::Cookie`` fields used on desktop. ``value`` is
/// readable via the attribute but omitted from ``repr``.
#[pyclass(frozen, from_py_object)]
#[derive(Clone)]
pub struct Cookie {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    value: String,
    #[pyo3(get)]
    domain: Option<String>,
    #[pyo3(get)]
    path: Option<String>,
    #[pyo3(get)]
    secure: Option<bool>,
    #[pyo3(get)]
    http_only: Option<bool>,
    /// ``"Strict"``, ``"Lax"``, or ``"None"`` when set.
    #[pyo3(get)]
    same_site: Option<String>,
    /// Max-Age in seconds when set.
    #[pyo3(get)]
    max_age: Option<f64>,
    /// Absolute expiry as Unix timestamp (UTC seconds) when set.
    #[pyo3(get)]
    expires: Option<f64>,
}

#[pymethods]
impl Cookie {
    #[new]
    #[pyo3(signature = (
        name,
        value,
        *,
        domain = None,
        path = None,
        secure = None,
        http_only = None,
        same_site = None,
        max_age = None,
        expires = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        name: String,
        value: String,
        domain: Option<String>,
        path: Option<String>,
        secure: Option<bool>,
        http_only: Option<bool>,
        same_site: Option<String>,
        max_age: Option<f64>,
        expires: Option<f64>,
    ) -> PyResult<Self> {
        if let Some(ref site) = same_site {
            parse_same_site(site)?;
        }
        if let Some(age) = max_age {
            if !age.is_finite() || age < 0.0 {
                return Err(PyValueError::new_err(
                    "Cookie.max_age must be a finite number >= 0",
                ));
            }
        }
        if let Some(ts) = expires {
            if !ts.is_finite() {
                return Err(PyValueError::new_err(
                    "Cookie.expires must be a finite Unix timestamp",
                ));
            }
        }
        Ok(Self {
            name,
            value,
            domain,
            path,
            secure,
            http_only,
            same_site,
            max_age,
            expires,
        })
    }

    fn __repr__(&self) -> String {
        // Intentionally omit ``value`` (never log cookie secrets).
        format!(
            "Cookie(name={:?}, domain={:?}, path={:?}, secure={:?}, http_only={:?}, same_site={:?}, max_age={:?}, expires={:?})",
            self.name,
            self.domain,
            self.path,
            self.secure,
            self.http_only,
            self.same_site,
            self.max_age,
            self.expires
        )
    }
}

impl Cookie {
    pub(crate) fn from_wry(c: &cookie::Cookie<'_>) -> Self {
        let same_site = c.same_site().map(|site| match site {
            SameSite::Strict => "Strict".to_string(),
            SameSite::Lax => "Lax".to_string(),
            SameSite::None => "None".to_string(),
        });
        let max_age = c.max_age().map(|d| d.as_seconds_f64());
        let expires = match c.expires() {
            Some(Expiration::DateTime(dt)) => Some(dt.unix_timestamp() as f64),
            _ => None,
        };
        Self {
            name: c.name().to_string(),
            value: c.value().to_string(),
            domain: c.domain().map(str::to_string),
            path: c.path().map(str::to_string),
            secure: c.secure(),
            http_only: c.http_only(),
            same_site,
            max_age,
            expires,
        }
    }

    pub(crate) fn to_wry(&self) -> PyResult<cookie::Cookie<'static>> {
        let mut builder = cookie::Cookie::build((self.name.clone(), self.value.clone()));
        if let Some(ref domain) = self.domain {
            builder = builder.domain(domain.clone());
        }
        if let Some(ref path) = self.path {
            builder = builder.path(path.clone());
        }
        if let Some(secure) = self.secure {
            builder = builder.secure(secure);
        }
        if let Some(http_only) = self.http_only {
            builder = builder.http_only(http_only);
        }
        if let Some(ref site) = self.same_site {
            builder = builder.same_site(parse_same_site(site)?);
        }
        if let Some(age) = self.max_age {
            let secs = age.round() as i64;
            builder = builder.max_age(cookie::time::Duration::seconds(secs));
        }
        if let Some(ts) = self.expires {
            let secs = ts.round() as i64;
            let dt = cookie::time::OffsetDateTime::from_unix_timestamp(secs).map_err(|e| {
                PyValueError::new_err(format!("Cookie.expires out of range: {e}"))
            })?;
            builder = builder.expires(dt);
        }
        Ok(builder.build())
    }
}

fn parse_same_site(site: &str) -> PyResult<SameSite> {
    match site {
        "Strict" => Ok(SameSite::Strict),
        "Lax" => Ok(SameSite::Lax),
        "None" => Ok(SameSite::None),
        _ => Err(PyValueError::new_err(
            "Cookie.same_site must be 'Strict', 'Lax', or 'None'",
        )),
    }
}
