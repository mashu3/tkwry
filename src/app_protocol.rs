//! Local app asset serving via the ``tkwry://`` custom protocol.

use std::borrow::Cow;
use std::path::{Component, Path, PathBuf};

use wry::http::{
    header::{CACHE_CONTROL, CONTENT_TYPE},
    HeaderValue, Request, Response, StatusCode,
};

/// Options for ``tkwry://`` static serving.
#[derive(Clone, Debug, Default)]
pub(crate) struct AppServeOptions {
    pub spa_fallback: bool,
    pub cache_control: Option<String>,
}

/// Map a ``tkwry://`` URL to the WebView2 navigation form used with
/// ``with_https_scheme(true)``.
///
/// wry rewrites ``{scheme}://localhost/...`` → ``https://{scheme}.localhost/...``
/// for ``with_url`` at create time, but **not** for later ``WebView::load_url``.
/// tkwry always loads ``app=`` content via deferred ``load_url``, so Windows
/// needs the same rewrite here.
#[cfg(target_os = "windows")]
pub(crate) fn navigate_url(url: &str) -> Cow<'_, str> {
    if let Some(rest) = url.strip_prefix("tkwry://") {
        Cow::Owned(format!("https://tkwry.{rest}"))
    } else {
        Cow::Borrowed(url)
    }
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn navigate_url(url: &str) -> Cow<'_, str> {
    Cow::Borrowed(url)
}

/// Resolve a request path under ``root``, rejecting ``..`` and absolute escapes.
///
/// This only sanitizes URL components. Callers must still
/// [`resolve_under_root`] so symlinks / junctions / reparse points cannot
/// escape ``root``.
pub(crate) fn safe_join(root: &Path, url_path: &str) -> Option<PathBuf> {
    let trimmed = url_path.trim_start_matches('/');
    let mut out = root.to_path_buf();
    if trimmed.is_empty() {
        out.push("index.html");
        return Some(out);
    }
    for comp in Path::new(trimmed).components() {
        match comp {
            Component::Normal(c) => out.push(c),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return None;
            }
        }
    }
    Some(out)
}

enum ServeResolve {
    Ok(PathBuf),
    Forbidden,
    NotFound,
}

enum ServeError {
    Forbidden,
    NotFound,
}

/// Canonicalize *candidate* and require the real path to stay under *root*.
///
/// Follows symlinks, Windows junctions, and other reparse points. Internal
/// links that remain inside *root* are allowed; anything that escapes is
/// forbidden.
fn resolve_under_root(root: &Path, candidate: &Path) -> ServeResolve {
    let Ok(root) = root.canonicalize() else {
        return ServeResolve::NotFound;
    };
    match candidate.canonicalize() {
        Ok(path) if path.starts_with(&root) => ServeResolve::Ok(path),
        Ok(_) => ServeResolve::Forbidden,
        Err(_) => ServeResolve::NotFound,
    }
}

fn mime_for_path(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" | "cjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "webmanifest" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "avif" => "image/avif",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "wasm" => "application/wasm",
        "map" => "application/json",
        "txt" | "md" => "text/plain; charset=utf-8",
        "xml" => "application/xml",
        "csv" => "text/csv; charset=utf-8",
        "pdf" => "application/pdf",
        "mp3" => "audio/mpeg",
        "wav" => "audio/wav",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "toml" => "application/toml",
        "yaml" | "yml" => "application/yaml",
        _ => "application/octet-stream",
    }
}

fn looks_like_static_asset(url_path: &str) -> bool {
    let trimmed = url_path.trim_start_matches('/');
    if trimmed.is_empty() {
        return false;
    }
    Path::new(trimmed)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|ext| {
            let ext = ext.to_ascii_lowercase();
            !matches!(ext.as_str(), "html" | "htm")
        })
}

fn error_response(status: StatusCode, message: impl Into<String>) -> Response<Cow<'static, [u8]>> {
    let body = message.into().into_bytes();
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Cow::Owned(body))
        .unwrap_or_else(|_| {
            Response::builder()
                .status(StatusCode::INTERNAL_SERVER_ERROR)
                .body(Cow::Borrowed(b"internal error" as &[u8]))
                .expect("static error response")
        })
}

fn file_response(
    file_path: &Path,
    bytes: Vec<u8>,
    options: &AppServeOptions,
) -> Response<Cow<'static, [u8]>> {
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, mime_for_path(file_path));
    if let Some(cache) = options.cache_control.as_deref() {
        if let Ok(value) = HeaderValue::from_str(cache) {
            builder = builder.header(CACHE_CONTROL, value);
        }
    }
    builder.body(Cow::Owned(bytes)).unwrap_or_else(|e| {
        error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("response build failed: {e}"),
        )
    })
}

fn read_under_root(
    root: &Path,
    candidate: &Path,
    options: &AppServeOptions,
) -> Result<Response<Cow<'static, [u8]>>, ServeError> {
    match resolve_under_root(root, candidate) {
        ServeResolve::Ok(file_path) => match std::fs::read(&file_path) {
            Ok(bytes) => Ok(file_response(&file_path, bytes, options)),
            Err(_) => Err(ServeError::NotFound),
        },
        ServeResolve::Forbidden => Err(ServeError::Forbidden),
        ServeResolve::NotFound => Err(ServeError::NotFound),
    }
}

/// Serve a file from ``root`` for a ``tkwry://`` request.
pub(crate) fn serve_app_request(
    root: &Path,
    request: Request<Vec<u8>>,
    options: &AppServeOptions,
) -> Response<Cow<'static, [u8]>> {
    let path = request.uri().path();
    let Some(file_path) = safe_join(root, path) else {
        return error_response(StatusCode::FORBIDDEN, "forbidden path");
    };
    match read_under_root(root, &file_path, options) {
        Ok(response) => response,
        Err(ServeError::Forbidden) => error_response(StatusCode::FORBIDDEN, "forbidden path"),
        Err(ServeError::NotFound) => {
            if options.spa_fallback && !looks_like_static_asset(path) {
                let index = root.join("index.html");
                match read_under_root(root, &index, options) {
                    Ok(response) => return response,
                    Err(ServeError::Forbidden) => {
                        return error_response(StatusCode::FORBIDDEN, "forbidden path");
                    }
                    Err(ServeError::NotFound) => {}
                }
            }
            error_response(
                StatusCode::NOT_FOUND,
                format!("not found: {}", path.trim_start_matches('/')),
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_join_rejects_parent_dir() {
        let root = Path::new("/tmp/app");
        assert!(safe_join(root, "../etc/passwd").is_none());
        assert!(safe_join(root, "foo/../../etc/passwd").is_none());
    }

    #[test]
    fn safe_join_defaults_index() {
        let root = Path::new("/tmp/app");
        assert_eq!(
            safe_join(root, "/").unwrap(),
            PathBuf::from("/tmp/app/index.html")
        );
        assert_eq!(
            safe_join(root, "").unwrap(),
            PathBuf::from("/tmp/app/index.html")
        );
    }

    #[test]
    fn safe_join_nested() {
        let root = Path::new("/tmp/app");
        assert_eq!(
            safe_join(root, "/assets/main.js").unwrap(),
            PathBuf::from("/tmp/app/assets/main.js")
        );
    }

    #[test]
    fn navigate_url_rewrites_tkwry_on_windows_only() {
        let input = "tkwry://localhost/index.html";
        let out = navigate_url(input);
        if cfg!(target_os = "windows") {
            assert_eq!(out.as_ref(), "https://tkwry.localhost/index.html");
        } else {
            assert_eq!(out.as_ref(), input);
        }
        assert_eq!(
            navigate_url("https://example.com/").as_ref(),
            "https://example.com/"
        );
    }

    #[test]
    fn looks_like_static_asset_rules() {
        assert!(!looks_like_static_asset("/"));
        assert!(!looks_like_static_asset("/app/route"));
        assert!(!looks_like_static_asset("/index.html"));
        assert!(looks_like_static_asset("/assets/app.js"));
        assert!(looks_like_static_asset("/style.css"));
    }

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let dir =
            std::env::temp_dir().join(format!("tkwry-app-{label}-{}-{nanos}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn try_symlink_file(link: &Path, target: &Path) -> bool {
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link).is_ok()
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (link, target);
            false
        }
    }

    fn dummy_request(path: &str) -> Request<Vec<u8>> {
        Request::builder()
            .uri(format!("tkwry://localhost{path}"))
            .body(Vec::new())
            .unwrap()
    }

    #[test]
    fn serve_rejects_symlink_outside_root() {
        let tmp = make_temp_dir("symlink-escape");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        let secret = tmp.join("secret.txt");
        std::fs::write(&secret, b"secret").unwrap();
        let link = root.join("leak.txt");
        if !try_symlink_file(&link, &secret) {
            let _ = std::fs::remove_dir_all(&tmp);
            eprintln!("skip serve_rejects_symlink_outside_root: cannot create symlink");
            return;
        }
        let resp = serve_app_request(
            &root,
            dummy_request("/leak.txt"),
            &AppServeOptions::default(),
        );
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn serve_allows_symlink_inside_root() {
        let tmp = make_temp_dir("symlink-internal");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        let target = root.join("real.js");
        std::fs::write(&target, b"console.log(1)").unwrap();
        let link = root.join("alias.js");
        if !try_symlink_file(&link, &target) {
            let _ = std::fs::remove_dir_all(&tmp);
            eprintln!("skip serve_allows_symlink_inside_root: cannot create symlink");
            return;
        }
        let resp = serve_app_request(
            &root,
            dummy_request("/alias.js"),
            &AppServeOptions::default(),
        );
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(resp.body().as_ref(), b"console.log(1)");
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[cfg(windows)]
    #[test]
    fn serve_rejects_junction_outside_root() {
        let tmp = make_temp_dir("junction-escape");
        let root = tmp.join("app");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        std::fs::write(outside.join("secret.txt"), b"secret").unwrap();
        let link = root.join("escape");
        let ok = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                &link.to_string_lossy(),
                &outside.to_string_lossy(),
            ])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !ok {
            let _ = std::fs::remove_dir_all(&tmp);
            eprintln!("skip serve_rejects_junction_outside_root: cannot create junction");
            return;
        }
        let resp = serve_app_request(
            &root,
            dummy_request("/escape/secret.txt"),
            &AppServeOptions::default(),
        );
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn serve_regular_file_under_root() {
        let tmp = make_temp_dir("regular");
        let root = tmp.join("app");
        std::fs::create_dir_all(root.join("assets")).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        std::fs::write(root.join("assets").join("main.js"), b"1").unwrap();
        let resp = serve_app_request(
            &root,
            dummy_request("/assets/main.js"),
            &AppServeOptions::default(),
        );
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(resp.body().as_ref(), b"1");
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
