//! Local app asset serving via the ``tkwry://`` custom protocol.

use std::borrow::Cow;
use std::path::{Component, Path, PathBuf};

use wry::http::{header::CONTENT_TYPE, Request, Response, StatusCode};

/// Resolve a request path under ``root``, rejecting ``..`` and absolute escapes.
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

fn mime_for_path(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "wasm" => "application/wasm",
        "map" => "application/json",
        "txt" | "md" => "text/plain; charset=utf-8",
        "xml" => "application/xml",
        _ => "application/octet-stream",
    }
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

/// Serve a file from ``root`` for a ``tkwry://`` request.
pub(crate) fn serve_app_request(
    root: &Path,
    request: Request<Vec<u8>>,
) -> Response<Cow<'static, [u8]>> {
    let path = request.uri().path();
    let Some(file_path) = safe_join(root, path) else {
        return error_response(StatusCode::FORBIDDEN, "forbidden path");
    };
    match std::fs::read(&file_path) {
        Ok(bytes) => Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, mime_for_path(&file_path))
            .body(Cow::Owned(bytes))
            .unwrap_or_else(|e| {
                error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("response build failed: {e}"),
                )
            }),
        Err(_) => error_response(
            StatusCode::NOT_FOUND,
            format!("not found: {}", path.trim_start_matches('/')),
        ),
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
}
