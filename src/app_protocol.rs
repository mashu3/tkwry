//! Local app asset serving via the ``tkwry://`` custom protocol.

use std::borrow::Cow;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};

use wry::http::{
    header::{
        HeaderName, ACCEPT, CACHE_CONTROL, CONTENT_LENGTH, CONTENT_RANGE, CONTENT_SECURITY_POLICY,
        CONTENT_TYPE, ETAG, IF_NONE_MATCH, ORIGIN, RANGE, REFERER,
    },
    HeaderValue, Method, Request, Response, StatusCode,
};

const CROSS_ORIGIN_OPENER_POLICY: HeaderName =
    HeaderName::from_static("cross-origin-opener-policy");
const CROSS_ORIGIN_RESOURCE_POLICY: HeaderName =
    HeaderName::from_static("cross-origin-resource-policy");

/// Options for ``tkwry://`` static serving.
#[derive(Clone, Debug, Default)]
pub(crate) struct AppServeOptions {
    pub spa_fallback: bool,
    pub cache_control: Option<String>,
    pub csp: Option<String>,
    pub coop: bool,
    pub corp: bool,
}

/// Map a ``tkwry://`` URL to the WebView2 navigation form used with
/// ``with_https_scheme``.
///
/// wry rewrites ``{scheme}://localhost/...`` → ``http(s)://{scheme}.localhost/...``
/// for ``with_url`` at create time, but **not** for later ``WebView::load_url``.
/// tkwry always loads ``app=`` content via deferred ``load_url``, so Windows
/// needs the same rewrite here. ``https_scheme`` selects ``https`` (tkwry
/// default / secure context) vs ``http`` (wry default / mixed content).
#[cfg(target_os = "windows")]
pub(crate) fn navigate_url(url: &str, https_scheme: bool) -> Cow<'_, str> {
    if let Some(rest) = url.strip_prefix("tkwry://") {
        let scheme = if https_scheme { "https" } else { "http" };
        Cow::Owned(format!("{scheme}://tkwry.{rest}"))
    } else {
        Cow::Borrowed(url)
    }
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn navigate_url(url: &str, _https_scheme: bool) -> Cow<'_, str> {
    Cow::Borrowed(url)
}

fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// Percent-decode one path segment. Rejects NUL / invalid UTF-8 / embedded slashes.
fn decode_path_segment(raw: &str) -> Option<String> {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' => {
                if i + 2 >= bytes.len() {
                    return None;
                }
                let high = hex_nibble(bytes[i + 1])?;
                let low = hex_nibble(bytes[i + 2])?;
                out.push((high << 4) | low);
                i += 3;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    if out.contains(&0) {
        return None;
    }
    let decoded = String::from_utf8(out).ok()?;
    if decoded.contains('/') || decoded.contains('\\') {
        return None;
    }
    Some(decoded)
}

fn looks_like_windows_drive(segment: &str) -> bool {
    let bytes = segment.as_bytes();
    bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':'
}

/// Resolve a request path under ``root``, rejecting ``..`` and absolute escapes.
///
/// Percent-decodes each segment independently so ``%2e%2e`` cannot sneak past
/// ``..`` checks. Callers must still [`open_under_root`] so symlinks /
/// junctions / reparse points cannot escape ``root``.
pub(crate) fn safe_join(root: &Path, url_path: &str) -> Option<PathBuf> {
    let trimmed = url_path.trim_start_matches('/');
    let mut out = root.to_path_buf();
    if trimmed.is_empty() {
        out.push("index.html");
        return Some(out);
    }
    let mut pushed = false;
    for raw_seg in trimmed.split('/') {
        if raw_seg.is_empty() {
            continue;
        }
        let seg = decode_path_segment(raw_seg)?;
        if seg.is_empty() || seg == "." {
            continue;
        }
        if seg == ".." || looks_like_windows_drive(&seg) || seg.starts_with("\\\\") {
            return None;
        }
        // ``Path`` would treat some of these as Prefix / ParentDir.
        if Path::new(&seg)
            .components()
            .any(|c| !matches!(c, Component::Normal(_) | Component::CurDir))
        {
            return None;
        }
        out.push(seg);
        pushed = true;
    }
    if !pushed {
        out.push("index.html");
    }
    Some(out)
}

enum ServeError {
    Forbidden,
    NotFound,
}

/// True when the opened *file* and *path* refer to the same inode / file index.
fn same_file_identity(file: &File, path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let Ok(opened_meta) = file.metadata() else {
            return false;
        };
        let Ok(path_meta) = std::fs::metadata(path) else {
            return false;
        };
        opened_meta.dev() == path_meta.dev() && opened_meta.ino() == path_meta.ino()
    }
    #[cfg(windows)]
    {
        let Ok(other) = File::open(path) else {
            return false;
        };
        match (windows_file_id(file), windows_file_id(&other)) {
            (Some(a), Some(b)) => a == b,
            _ => false,
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (file, path);
        false
    }
}

/// Volume serial + file index from an open handle (stable; no `windows_by_handle`).
#[cfg(windows)]
fn windows_file_id(file: &File) -> Option<(u32, u64)> {
    use std::mem::MaybeUninit;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    struct FileTime {
        dw_low_date_time: u32,
        dw_high_date_time: u32,
    }

    #[repr(C)]
    #[allow(dead_code)]
    struct ByHandleFileInformation {
        dw_file_attributes: u32,
        ft_creation_time: FileTime,
        ft_last_access_time: FileTime,
        ft_last_write_time: FileTime,
        dw_volume_serial_number: u32,
        n_file_size_high: u32,
        n_file_size_low: u32,
        n_number_of_links: u32,
        n_file_index_high: u32,
        n_file_index_low: u32,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(
            handle: *mut core::ffi::c_void,
            info: *mut ByHandleFileInformation,
        ) -> i32;
    }

    let mut info = MaybeUninit::<ByHandleFileInformation>::uninit();
    let ok = unsafe { GetFileInformationByHandle(file.as_raw_handle(), info.as_mut_ptr()) };
    if ok == 0 {
        return None;
    }
    let info = unsafe { info.assume_init() };
    let index = (u64::from(info.n_file_index_high) << 32) | u64::from(info.n_file_index_low);
    Some((info.dw_volume_serial_number, index))
}

/// Open *candidate* and require the opened file to stay under *root*.
///
/// Follows symlinks / junctions at open time, then re-canonicalizes and
/// compares device+inode (Unix) or volume+file index (Windows) so a TOCTOU
/// swap between canonicalize and read cannot serve a file outside *root*.
/// Internal links that remain inside *root* are allowed.
fn open_under_root(root: &Path, candidate: &Path) -> Result<(File, PathBuf), ServeError> {
    let Ok(root) = root.canonicalize() else {
        return Err(ServeError::NotFound);
    };
    let file = File::open(candidate).map_err(|_| ServeError::NotFound)?;
    let opened_meta = file.metadata().map_err(|_| ServeError::NotFound)?;
    if opened_meta.is_dir() {
        return Err(ServeError::NotFound);
    }
    let Ok(real) = candidate.canonicalize() else {
        return Err(ServeError::NotFound);
    };
    if !real.starts_with(&root) {
        return Err(ServeError::Forbidden);
    }
    if !same_file_identity(&file, &real) {
        return Err(ServeError::Forbidden);
    }
    Ok((file, real))
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

pub(crate) fn looks_like_static_asset(url_path: &str) -> bool {
    let trimmed = url_path.trim_start_matches('/');
    if trimmed.is_empty() {
        return false;
    }
    let last = trimmed.rsplit('/').next().unwrap_or(trimmed);
    let decoded = decode_path_segment(last).unwrap_or_else(|| last.to_string());
    Path::new(&decoded)
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|ext| {
            let ext = ext.to_ascii_lowercase();
            !matches!(ext.as_str(), "html" | "htm")
        })
}

fn accepts_html(request: &Request<Vec<u8>>) -> bool {
    let Some(raw) = request.headers().get(ACCEPT).and_then(|v| v.to_str().ok()) else {
        return true;
    };
    let lower = raw.to_ascii_lowercase();
    if lower.contains("text/html") {
        return true;
    }
    lower
        .split(',')
        .map(str::trim)
        .any(|part| part == "*/*" || part.starts_with("*/*;"))
}

fn etag_for_meta(meta: &std::fs::Metadata) -> String {
    let size = meta.len();
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("\"{size:x}-{mtime:x}\"")
}

fn header_str(request: &Request<Vec<u8>>, name: wry::http::header::HeaderName) -> Option<&str> {
    request.headers().get(name).and_then(|v| v.to_str().ok())
}

fn if_none_match_hits(request: &Request<Vec<u8>>, etag: &str) -> bool {
    let Some(raw) = header_str(request, IF_NONE_MATCH) else {
        return false;
    };
    raw.split(',')
        .map(str::trim)
        .any(|token| token == "*" || token == etag || token.strip_prefix("W/") == Some(etag))
}

#[derive(Clone, Copy)]
struct ByteRange {
    start: u64,
    end: u64, // inclusive
}

fn parse_byte_range(header: &str, size: u64) -> Option<ByteRange> {
    let spec = header.strip_prefix("bytes=")?;
    if spec.contains(',') {
        return None; // single range only
    }
    let (start_raw, end_raw) = spec.split_once('-')?;
    if size == 0 {
        return None;
    }
    if start_raw.is_empty() {
        let suffix: u64 = end_raw.parse().ok()?;
        if suffix == 0 {
            return None;
        }
        let len = suffix.min(size);
        return Some(ByteRange {
            start: size - len,
            end: size - 1,
        });
    }
    let start: u64 = start_raw.parse().ok()?;
    if start >= size {
        return None;
    }
    let end = if end_raw.is_empty() {
        size - 1
    } else {
        end_raw.parse::<u64>().ok()?.min(size - 1)
    };
    if end < start {
        return None;
    }
    Some(ByteRange { start, end })
}

fn apply_serve_headers(
    mut builder: wry::http::response::Builder,
    options: &AppServeOptions,
    etag: &str,
    content_len: u64,
) -> wry::http::response::Builder {
    if let Ok(value) = HeaderValue::from_str(etag) {
        builder = builder.header(ETAG, value);
    }
    builder = builder.header(CONTENT_LENGTH, content_len);
    if let Some(cache) = options.cache_control.as_deref() {
        if let Ok(value) = HeaderValue::from_str(cache) {
            builder = builder.header(CACHE_CONTROL, value);
        }
    }
    if let Some(csp) = options.csp.as_deref() {
        if let Ok(value) = HeaderValue::from_str(csp) {
            builder = builder.header(CONTENT_SECURITY_POLICY, value);
        }
    }
    if options.coop {
        builder = builder.header(
            CROSS_ORIGIN_OPENER_POLICY,
            HeaderValue::from_static("same-origin"),
        );
    }
    if options.corp {
        builder = builder.header(
            CROSS_ORIGIN_RESOURCE_POLICY,
            HeaderValue::from_static("same-origin"),
        );
    }
    builder
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
    etag: &str,
    status: StatusCode,
    extra: Option<(wry::http::header::HeaderName, HeaderValue)>,
) -> Response<Cow<'static, [u8]>> {
    let len = bytes.len() as u64;
    let mut builder = apply_serve_headers(
        Response::builder()
            .status(status)
            .header(CONTENT_TYPE, mime_for_path(file_path)),
        options,
        etag,
        len,
    );
    if let Some((name, value)) = extra {
        builder = builder.header(name, value);
    }
    builder.body(Cow::Owned(bytes)).unwrap_or_else(|e| {
        error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("response build failed: {e}"),
        )
    })
}

fn serve_open_file(
    request: &Request<Vec<u8>>,
    mut file: File,
    file_path: &Path,
    options: &AppServeOptions,
) -> Result<Response<Cow<'static, [u8]>>, ServeError> {
    let meta = file.metadata().map_err(|_| ServeError::NotFound)?;
    if meta.is_dir() {
        return Err(ServeError::NotFound);
    }
    let etag = etag_for_meta(&meta);
    let size = meta.len();
    if if_none_match_hits(request, &etag) {
        let builder = apply_serve_headers(
            Response::builder().status(StatusCode::NOT_MODIFIED),
            options,
            &etag,
            0,
        );
        return Ok(builder
            .body(Cow::Borrowed(b"" as &[u8]))
            .unwrap_or_else(|_| {
                error_response(StatusCode::INTERNAL_SERVER_ERROR, "304 build failed")
            }));
    }
    if request.method() == Method::HEAD {
        let builder = apply_serve_headers(
            Response::builder()
                .status(StatusCode::OK)
                .header(CONTENT_TYPE, mime_for_path(file_path)),
            options,
            &etag,
            size,
        );
        return Ok(builder
            .body(Cow::Borrowed(b"" as &[u8]))
            .unwrap_or_else(|_| {
                error_response(StatusCode::INTERNAL_SERVER_ERROR, "HEAD build failed")
            }));
    }
    if let Some(range_header) = header_str(request, RANGE) {
        let Some(range) = parse_byte_range(range_header, size) else {
            let builder = apply_serve_headers(
                Response::builder()
                    .status(StatusCode::RANGE_NOT_SATISFIABLE)
                    .header(CONTENT_RANGE, format!("bytes */{size}")),
                options,
                &etag,
                0,
            );
            return Ok(builder
                .body(Cow::Borrowed(b"" as &[u8]))
                .unwrap_or_else(|_| {
                    error_response(StatusCode::INTERNAL_SERVER_ERROR, "416 build failed")
                }));
        };
        file.seek(SeekFrom::Start(range.start))
            .map_err(|_| ServeError::NotFound)?;
        let len = (range.end - range.start + 1) as usize;
        let mut bytes = vec![0u8; len];
        file.read_exact(&mut bytes)
            .map_err(|_| ServeError::NotFound)?;
        let content_range = format!("bytes {}-{}/{}", range.start, range.end, size);
        let extra = HeaderValue::from_str(&content_range)
            .ok()
            .map(|value| (CONTENT_RANGE, value));
        return Ok(file_response(
            file_path,
            bytes,
            options,
            &etag,
            StatusCode::PARTIAL_CONTENT,
            extra,
        ));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|_| ServeError::NotFound)?;
    Ok(file_response(
        file_path,
        bytes,
        options,
        &etag,
        StatusCode::OK,
        None,
    ))
}

fn read_under_root(
    root: &Path,
    candidate: &Path,
    request: &Request<Vec<u8>>,
    options: &AppServeOptions,
) -> Result<Response<Cow<'static, [u8]>>, ServeError> {
    let (file, file_path) = open_under_root(root, candidate)?;
    serve_open_file(request, file, &file_path, options)
}

fn spa_fallback_allowed(request: &Request<Vec<u8>>, options: &AppServeOptions) -> bool {
    options.spa_fallback && !looks_like_static_asset(request.uri().path()) && accepts_html(request)
}

fn is_tkwry_origin(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "tkwry://localhost" | "tkwry://app" | "https://tkwry.localhost" | "http://tkwry.localhost"
    )
}

fn is_tkwry_referer(value: &str) -> bool {
    let lower = value.trim().to_ascii_lowercase();
    lower.starts_with("tkwry://localhost")
        || lower == "tkwry://app"
        || lower.starts_with("tkwry://app/")
        || lower.starts_with("tkwry://app?")
        || lower.starts_with("https://tkwry.localhost")
        || lower.starts_with("http://tkwry.localhost")
}

/// True when a custom-protocol request clearly comes from another origin.
///
/// Missing Origin/Referer is allowed (top-level navigation often has neither).
/// Cross-origin ``fetch`` / ``<script src>`` typically send Origin or Referer.
fn cross_origin_app_request(request: &Request<Vec<u8>>) -> bool {
    if let Some(origin) = header_str(request, ORIGIN) {
        return !is_tkwry_origin(origin);
    }
    if let Some(referer) = header_str(request, REFERER) {
        return !is_tkwry_referer(referer);
    }
    false
}

/// Serve a file from ``root`` for a ``tkwry://`` request.
pub(crate) fn serve_app_request(
    root: &Path,
    request: Request<Vec<u8>>,
    options: &AppServeOptions,
) -> Response<Cow<'static, [u8]>> {
    if !matches!(*request.method(), Method::GET | Method::HEAD) {
        return error_response(StatusCode::METHOD_NOT_ALLOWED, "method not allowed");
    }
    if cross_origin_app_request(&request) {
        return error_response(StatusCode::FORBIDDEN, "cross-origin tkwry:// request");
    }
    let path = request.uri().path();
    let Some(file_path) = safe_join(root, path) else {
        return error_response(StatusCode::FORBIDDEN, "forbidden path");
    };
    match read_under_root(root, &file_path, &request, options) {
        Ok(response) => response,
        Err(ServeError::Forbidden) => error_response(StatusCode::FORBIDDEN, "forbidden path"),
        Err(ServeError::NotFound) => {
            if spa_fallback_allowed(&request, options) {
                let index = root.join("index.html");
                match read_under_root(root, &index, &request, options) {
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
        let https = navigate_url(input, true);
        let http = navigate_url(input, false);
        if cfg!(target_os = "windows") {
            assert_eq!(https.as_ref(), "https://tkwry.localhost/index.html");
            assert_eq!(http.as_ref(), "http://tkwry.localhost/index.html");
        } else {
            assert_eq!(https.as_ref(), input);
            assert_eq!(http.as_ref(), input);
        }
        assert_eq!(
            navigate_url("https://example.com/", true).as_ref(),
            "https://example.com/"
        );
    }

    #[test]
    fn looks_like_static_asset_rules() {
        assert!(!looks_like_static_asset("/"));
        assert!(!looks_like_static_asset("/app/route"));
        assert!(!looks_like_static_asset("/index.html"));
        assert!(!looks_like_static_asset("/about.htm"));
        assert!(looks_like_static_asset("/assets/app.js"));
        assert!(looks_like_static_asset("/style.css"));
        assert!(looks_like_static_asset("/video.mp4"));
        assert!(looks_like_static_asset("/missing.js"));
    }

    #[test]
    fn safe_join_rejects_percent_encoded_parent_and_nul() {
        let root = Path::new("/tmp/app");
        assert!(safe_join(root, "/%2e%2e/etc/passwd").is_none());
        assert!(safe_join(root, "/foo/%2e%2e/%2e%2e/etc/passwd").is_none());
        assert!(safe_join(root, "/foo%00.txt").is_none());
        assert!(safe_join(root, "/C:/Windows/win.ini").is_none());
        assert!(safe_join(root, "/C%3A/Windows/win.ini").is_none());
        assert!(safe_join(root, r"/\\server\share/file").is_none());
        assert!(safe_join(root, "/%ff").is_none()); // invalid UTF-8 after decode
    }

    #[test]
    fn safe_join_decodes_ordinary_percent_segments() {
        let root = Path::new("/tmp/app");
        assert_eq!(
            safe_join(root, "/assets/hello%20world.js").unwrap(),
            PathBuf::from("/tmp/app/assets/hello world.js")
        );
    }

    #[test]
    fn safe_join_preserves_case() {
        let root = Path::new("/tmp/app");
        assert_eq!(
            safe_join(root, "/Assets/Main.JS").unwrap(),
            PathBuf::from("/tmp/app/Assets/Main.JS")
        );
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

    fn request_with(method: Method, path: &str, headers: &[(&str, &str)]) -> Request<Vec<u8>> {
        let mut builder = Request::builder()
            .method(method)
            .uri(format!("tkwry://localhost{path}"));
        for (name, value) in headers {
            builder = builder.header(*name, *value);
        }
        builder.body(Vec::new()).unwrap()
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
        assert!(resp.headers().get(ETAG).is_some());
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn spa_fallback_skips_missing_static_assets() {
        let tmp = make_temp_dir("spa-js");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>spa</p>").unwrap();
        let options = AppServeOptions {
            spa_fallback: true,
            cache_control: None,
            ..Default::default()
        };
        let missing_js = serve_app_request(&root, dummy_request("/missing.js"), &options);
        assert_eq!(missing_js.status(), StatusCode::NOT_FOUND);

        let route = serve_app_request(&root, dummy_request("/app/settings"), &options);
        assert_eq!(route.status(), StatusCode::OK);
        assert_eq!(route.body().as_ref(), b"<p>spa</p>");

        let json_accept = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/app/settings",
                &[("accept", "application/json")],
            ),
            &options,
        );
        assert_eq!(json_accept.status(), StatusCode::NOT_FOUND);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn serve_head_etag_and_range() {
        let tmp = make_temp_dir("http-meta");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        std::fs::write(root.join("clip.bin"), b"abcdefghij").unwrap();
        let options = AppServeOptions {
            spa_fallback: false,
            cache_control: Some("no-store".into()),
            ..Default::default()
        };

        let head = serve_app_request(
            &root,
            request_with(Method::HEAD, "/clip.bin", &[]),
            &options,
        );
        assert_eq!(head.status(), StatusCode::OK);
        assert!(head.body().as_ref().is_empty());
        assert_eq!(head.headers().get(CONTENT_LENGTH).unwrap(), "10");
        assert_eq!(head.headers().get(CACHE_CONTROL).unwrap(), "no-store");
        let etag = head.headers().get(ETAG).unwrap().clone();

        let not_modified = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/clip.bin",
                &[("if-none-match", etag.to_str().unwrap())],
            ),
            &options,
        );
        assert_eq!(not_modified.status(), StatusCode::NOT_MODIFIED);

        let partial = serve_app_request(
            &root,
            request_with(Method::GET, "/clip.bin", &[("range", "bytes=2-5")]),
            &options,
        );
        assert_eq!(partial.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(partial.body().as_ref(), b"cdef");
        assert_eq!(
            partial.headers().get(CONTENT_RANGE).unwrap(),
            "bytes 2-5/10"
        );
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn serve_rejects_cross_origin_app_request() {
        let tmp = make_temp_dir("cors-origin");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        let options = AppServeOptions::default();

        let foreign = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/index.html",
                &[("origin", "https://evil.example")],
            ),
            &options,
        );
        assert_eq!(foreign.status(), StatusCode::FORBIDDEN);

        let referer = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/index.html",
                &[("referer", "https://evil.example/page")],
            ),
            &options,
        );
        assert_eq!(referer.status(), StatusCode::FORBIDDEN);

        let same = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/index.html",
                &[("origin", "tkwry://localhost")],
            ),
            &options,
        );
        assert_eq!(same.status(), StatusCode::OK);

        let windows_origin = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/index.html",
                &[("origin", "https://tkwry.localhost")],
            ),
            &options,
        );
        assert_eq!(windows_origin.status(), StatusCode::OK);

        let windows_http_origin = serve_app_request(
            &root,
            request_with(
                Method::GET,
                "/index.html",
                &[("origin", "http://tkwry.localhost")],
            ),
            &options,
        );
        assert_eq!(windows_http_origin.status(), StatusCode::OK);

        let top_level = serve_app_request(&root, dummy_request("/index.html"), &options);
        assert_eq!(top_level.status(), StatusCode::OK);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn serve_applies_csp_coop_corp() {
        let tmp = make_temp_dir("csp");
        let root = tmp.join("app");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("index.html"), b"<p>ok</p>").unwrap();
        let options = AppServeOptions {
            csp: Some("default-src 'self'".into()),
            coop: true,
            corp: true,
            ..Default::default()
        };
        let resp = serve_app_request(&root, dummy_request("/index.html"), &options);
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(CONTENT_SECURITY_POLICY).unwrap(),
            "default-src 'self'"
        );
        assert_eq!(
            resp.headers().get("cross-origin-opener-policy").unwrap(),
            "same-origin"
        );
        assert_eq!(
            resp.headers().get("cross-origin-resource-policy").unwrap(),
            "same-origin"
        );

        let plain = serve_app_request(
            &root,
            dummy_request("/index.html"),
            &AppServeOptions::default(),
        );
        assert!(plain.headers().get(CONTENT_SECURITY_POLICY).is_none());
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
