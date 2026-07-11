"""URL normalization and validation for WebView navigation."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse
from urllib.request import url2pathname

_SUPPORTED_SCHEMES = frozenset({"http", "https", "file"})
_UNSUPPORTED_NAV_SCHEMES = frozenset(
    {"about", "blob", "data", "javascript", "mailto", "vbscript"}
)
_MIN_TCP_PORT = 1
_MAX_TCP_PORT = 65535

_FILE_EXTENSIONS = frozenset(
    {
        "asp",
        "css",
        "csv",
        "eot",
        "gif",
        "htm",
        "html",
        "ico",
        "jpeg",
        "jpg",
        "js",
        "json",
        "jsx",
        "map",
        "md",
        "mjs",
        "pdf",
        "php",
        "png",
        "py",
        "svg",
        "ts",
        "tsx",
        "ttf",
        "txt",
        "wasm",
        "webp",
        "woff",
        "woff2",
        "xml",
    }
)

_HOSTNAME_RE = re.compile(
    r"^([a-zA-Z0-9_]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?\.)*"
    r"[a-zA-Z0-9_]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?$"
)
_WINDOWS_DRIVE_ONLY_RE = re.compile(r"^[A-Za-z]:$")


def _contains_non_ascii(value: str) -> bool:
    return not value.isascii()


def _strip_ipv6_zone(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    zone_idx = host.find("%")
    if zone_idx >= 0:
        host = host[:zone_idx]
    return host


def _is_ipv6_literal(host: str) -> bool:
    try:
        ipaddress.IPv6Address(_strip_ipv6_zone(host))
    except ValueError:
        return False
    return True


def _encode_ipv6_zone_suffix(zone_suffix: str) -> str:
    if not zone_suffix:
        return ""
    if not zone_suffix.startswith("%"):
        zone_suffix = f"%{zone_suffix}"
    if zone_suffix.lower().startswith("%25"):
        return zone_suffix
    return f"%25{zone_suffix[1:]}"


def _split_ipv6_with_zone(url: str) -> tuple[str, str, str] | None:
    """Parse bare ``[ipv6%zone]:port``, ``ipv6%zone:port``, or ``ipv6%zone``."""
    if "://" in url or " " in url:
        return None

    slash_pos = url.find("/")
    authority = url if slash_pos < 0 else url[:slash_pos]
    path_suffix = url[slash_pos:] if slash_pos >= 0 else ""

    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return None
        inner = authority[1:end]
        zone_idx = inner.find("%")
        host = inner[:zone_idx] if zone_idx >= 0 else inner
        zone = inner[zone_idx:] if zone_idx >= 0 else ""
        if not _is_ipv6_literal(host):
            return None
        bracketed = f"[{host}{_encode_ipv6_zone_suffix(zone)}]"
        rest = authority[end + 1 :]
        if rest.startswith(":"):
            port = rest[1:]
            if port and not port.isdigit():
                return None
            return bracketed, port, path_suffix
        if not rest:
            return bracketed, "", path_suffix
        return None

    if "%" not in authority or "::" not in authority.split("%", 1)[0]:
        return None

    percent_idx = authority.index("%")
    host_part = authority[:percent_idx]
    after_percent = authority[percent_idx:]
    if not _is_ipv6_literal(host_part):
        return None

    colon_after_zone = after_percent.find(":")
    if colon_after_zone < 0:
        zone_suffix = after_percent
        port = ""
    else:
        zone_suffix = after_percent[:colon_after_zone]
        port = after_percent[colon_after_zone + 1 :]
        if port and not port.isdigit():
            return None

    bracketed = f"[{host_part}{_encode_ipv6_zone_suffix(zone_suffix)}]"
    return bracketed, port, path_suffix


def _split_ipv6_host_port(url: str) -> tuple[str, str, str] | None:
    """Parse ``[ipv6]:port``, ``ipv6:port``, or bare ``ipv6`` without a scheme."""
    if url.startswith("["):
        end = url.find("]")
        if end < 0:
            return None
        addr = url[1:end]
        if not _is_ipv6_literal(addr):
            return None
        rest = url[end + 1 :]
        if rest.startswith(":"):
            port, _, path = rest[1:].partition("/")
            if port and not port.isdigit():
                return None
            path_suffix = f"/{path}" if path else ""
            return f"[{_strip_ipv6_zone(addr)}]", port, path_suffix
        if rest.startswith("/") or not rest:
            return f"[{_strip_ipv6_zone(addr)}]", "", rest
        return None

    if url.count(":") < 2:
        return None

    slash_pos = url.find("/")
    authority = url if slash_pos < 0 else url[:slash_pos]
    path_suffix = url[slash_pos:] if slash_pos >= 0 else ""

    last_colon = authority.rfind(":")
    maybe_port = authority[last_colon + 1 :]
    maybe_host = authority[:last_colon]
    if maybe_port.isdigit() and _is_ipv6_literal(maybe_host):
        return f"[{_strip_ipv6_zone(maybe_host)}]", maybe_port, path_suffix
    if _is_ipv6_literal(authority):
        return f"[{_strip_ipv6_zone(authority)}]", "", path_suffix
    return None


def _https_url_from_ipv6_parts(host: str, port: str, path: str) -> str:
    if port:
        return f"https://{host}:{port}{path}"
    return f"https://{host}{path}"


def _fix_https_ipv6_netloc(url: str) -> str:
    """Bracket unescaped IPv6 literals in ``http``/``https`` authorities."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url
    netloc = parsed.netloc
    if netloc.startswith("["):
        return url

    host = netloc
    port = ""
    if netloc.count(":") >= 2:
        host, sep, port = netloc.rpartition(":")
        if not sep or not port.isdigit() or not _is_ipv6_literal(host):
            host = netloc
            port = ""
    if not _is_ipv6_literal(host):
        return url

    new_netloc = f"[{host}]:{port}" if port else f"[{host}]"
    return urlunparse(parsed._replace(netloc=new_netloc))


def _looks_like_idn_hostname(host: str) -> bool:
    if not host or len(host) > 253 or host.startswith("."):
        return False
    if host == "localhost":
        return True
    if "." not in host:
        return False
    if _HOSTNAME_RE.match(host):
        return True
    if not _contains_non_ascii(host):
        return False
    try:
        host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    return True


def _is_windows_drive_path(url: str) -> bool:
    return len(url) >= 3 and url[0].isalpha() and url[1] == ":" and url[2] in "/\\"


def _is_network_host(host: str) -> bool:
    """Hosts that are safe to treat as network names without a scheme.

    Single-label names (``README``, ``api``) are rejected so relative paths are
    not rewritten to ``https://…``. ``localhost`` and dotted names (DNS / IPv4)
    remain allowed.
    """
    if not host or len(host) > 253 or host.startswith("."):
        return False
    if host == "localhost":
        return True
    if "." not in host:
        return False
    return _looks_like_hostname(host)


def _looks_like_hostname(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    if host == "localhost":
        return True
    if _HOSTNAME_RE.match(host):
        return True
    return _looks_like_idn_hostname(host)


def _looks_like_filename(name: str) -> bool:
    if "/" in name or "\\" in name:
        name = name.rsplit("/", 1)[-1]
    if "." not in name:
        return False
    ext = name.rsplit(".", 1)[-1].lower()
    return ext in _FILE_EXTENSIONS


def _reject_unsupported_scheme(scheme: str) -> None:
    if scheme and scheme.lower() in _UNSUPPORTED_NAV_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {scheme!r}")


def _is_misparsed_host_port(parsed) -> bool:
    """urlparse treats ``host:port`` as ``scheme='host', path='port'``."""
    if not parsed.scheme or parsed.netloc:
        return False
    if parsed.scheme in _SUPPORTED_SCHEMES:
        return False
    if parsed.scheme.lower() in _UNSUPPORTED_NAV_SCHEMES:
        return False
    if not parsed.path:
        return False
    port_segment = parsed.path.split("/", 1)[0]
    if not port_segment.isdigit():
        return False
    # Port form is strong signal; allow single-label hosts here only.
    return _looks_like_hostname(parsed.scheme)


def _looks_like_url_without_scheme(url: str) -> bool:
    """Heuristic: host, host:port, or host/path without an explicit scheme."""
    if "://" in url or " " in url:
        return False

    if _split_ipv6_with_zone(url) is not None:
        return True

    if _split_ipv6_host_port(url) is not None:
        return True

    colon = url.find(":")
    slash = url.find("/")

    if colon >= 0 and (slash < 0 or colon < slash):
        host = url[:colon]
        rest = url[colon + 1 :]
        port_str = rest.split("/", 1)[0]
        # ``host:port`` is a strong URL signal (including single-label hosts).
        if port_str.isdigit() and _looks_like_hostname(host):
            return True

    if slash > 0:
        host = url[:slash]
        path = url[slash + 1 :]
        if not host or not path:
            return False
        # Require a real network host (dotted / localhost), not ``api/v1``.
        return _is_network_host(host) or host == "localhost"

    if colon < 0:
        if _looks_like_filename(url):
            return False
        if _contains_non_ascii(url):
            return _looks_like_idn_hostname(url)
        # Bare names need a dot (example.com) or be localhost — not README.
        return _is_network_host(url) or url == "localhost"

    return False


def _looks_like_file_path(url: str) -> bool:
    if url.startswith(("/", "./", "../", "~")):
        return True
    if len(url) >= 2 and url[0] == "." and url[1] in "/\\":
        return True
    if _is_windows_drive_path(url):
        return True
    if _looks_like_url_without_scheme(url):
        return False
    if "\\" in url:
        return True
    if "/" in url:
        return True
    if _looks_like_filename(url):
        return True
    # Bare relative segment without dots (e.g. README) — not a network host.
    if ":" not in url and "." not in url and url.isascii():
        return True
    return False


def _resolve_local_path_for_file_url(parsed) -> str:
    """Return the local filesystem path for a parsed ``file:`` URL."""
    if parsed.netloc:
        if parsed.netloc in ("", "localhost"):
            pathname = url2pathname(unquote(parsed.path))
            pathname = _strip_leading_slash_from_windows_path(pathname)
            pathname = _windows_drive_root_path(pathname)
            if not pathname:
                raise ValueError("file URL must include a path")
            return pathname
        if _is_windows_drive_netloc(parsed.netloc):
            pathname = f"{parsed.netloc}{parsed.path}"
            pathname = _windows_drive_root_path(pathname)
            if not pathname or pathname.endswith(":"):
                raise ValueError("file URL must include a path")
            return pathname
        server = parsed.netloc
        path = unquote(parsed.path)
        if not path or path == "/":
            raise ValueError("file URL must include a path")
        if os.name != "nt":
            raise ValueError(
                "UNC file URLs "
                f"(file://{server}/...) are not supported on this platform"
            )
        return f"\\\\{server}{path.replace('/', os.sep)}"

    pathname = url2pathname(unquote(parsed.path))
    pathname = _strip_leading_slash_from_windows_path(pathname)
    pathname = _windows_drive_root_path(pathname)
    if not pathname:
        raise ValueError("file URL must include a path")
    return pathname


def _require_local_path_exists(path: str, *, display: str) -> None:
    if not os.path.exists(path):
        raise ValueError(f"file URL path does not exist: {display}")


def _is_windows_drive_netloc(netloc: str) -> bool:
    return len(netloc) == 2 and netloc[0].isalpha() and netloc[1] == ":"


def _strip_leading_slash_from_windows_path(pathname: str) -> str:
    if (
        len(pathname) >= 3
        and pathname[0] == "/"
        and pathname[1].isalpha()
        and pathname[2] == ":"
    ):
        return pathname[1:]
    return pathname


def _windows_drive_root_path(pathname: str) -> str:
    if _WINDOWS_DRIVE_ONLY_RE.fullmatch(pathname):
        return f"{pathname}\\"
    if (
        len(pathname) == 3
        and pathname[0].isalpha()
        and pathname[1] == ":"
        and pathname[2] == "/"
    ):
        return f"{pathname[0]}:\\"
    return pathname


def _file_uri_from_path(path: str) -> str:
    expanded = os.path.expanduser(path.strip())
    expanded = _strip_leading_slash_from_windows_path(expanded)
    expanded = _windows_drive_root_path(expanded)
    _require_local_path_exists(expanded, display=expanded)
    # Do not follow symlinks; resolve() can load a different file than requested.
    return Path(expanded).absolute().as_uri()


def _normalize_unc_file_url(parsed) -> str:
    unc = _resolve_local_path_for_file_url(parsed)
    _require_local_path_exists(unc, display=f"file://{parsed.netloc}{parsed.path}")
    return urlunparse(parsed)


def _normalize_file_url(url: str) -> str:
    parsed = urlparse(url)
    local_path = _resolve_local_path_for_file_url(parsed)
    if (
        parsed.netloc
        and parsed.netloc not in ("", "localhost")
        and not _is_windows_drive_netloc(parsed.netloc)
    ):
        return _normalize_unc_file_url(parsed)
    return _file_uri_from_path(local_path)


def _normalize_url(url: str) -> str:
    cleaned = url.strip()
    for invisible in ("\u200b", "\ufeff", "\u2060"):
        cleaned = cleaned.replace(invisible, "")
    if not cleaned:
        raise ValueError("URL is empty")
    if _is_windows_drive_path(cleaned):
        return _file_uri_from_path(cleaned)
    if "://" not in cleaned:
        ipv6_zone = _split_ipv6_with_zone(cleaned)
        if ipv6_zone is not None:
            host, port, path = ipv6_zone
            return _https_url_from_ipv6_parts(host, port, path)
    parsed = urlparse(cleaned)
    _reject_unsupported_scheme(parsed.scheme)
    if (
        parsed.scheme
        and parsed.scheme not in _SUPPORTED_SCHEMES
        and _is_misparsed_host_port(parsed)
    ):
        cleaned = f"https://{cleaned}"
        parsed = urlparse(cleaned)
    if parsed.scheme == "file":
        return _normalize_file_url(cleaned)
    if parsed.scheme in {"http", "https"}:
        return _fix_https_ipv6_netloc(cleaned)
    if not parsed.scheme:
        if " " in cleaned:
            raise ValueError("URL must not contain spaces")
        ipv6_zone = _split_ipv6_with_zone(cleaned)
        if ipv6_zone is not None:
            host, port, path = ipv6_zone
            return _https_url_from_ipv6_parts(host, port, path)
        ipv6 = _split_ipv6_host_port(cleaned)
        if ipv6 is not None:
            host, port, path = ipv6
            return _https_url_from_ipv6_parts(host, port, path)
        if _looks_like_file_path(cleaned):
            return _file_uri_from_path(cleaned)
        cleaned = f"https://{cleaned}"
    return _fix_https_ipv6_netloc(cleaned)


def _is_valid_http_host(host: str) -> bool:
    if not host:
        return False
    if _is_ipv6_literal(host):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    return _looks_like_hostname(host)


def _validate_http_port(parsed) -> None:
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"invalid URL port: {parsed.netloc.rpartition(':')[-1]!r}"
        ) from exc
    if port is None:
        return
    if not (_MIN_TCP_PORT <= port <= _MAX_TCP_PORT):
        raise ValueError(f"invalid URL port: {port}")


def _validate_http_url(parsed) -> None:
    host = parsed.hostname
    if not host:
        if parsed.netloc:
            raise ValueError("invalid URL host")
        raise ValueError("URL must include a host, e.g. https://example.com")
    if not _is_valid_http_host(host):
        raise ValueError(f"invalid URL host: {host!r}")
    _validate_http_port(parsed)


def _validate_file_url(url: str) -> None:
    parsed = urlparse(url)
    if not parsed.path or parsed.path == "/":
        raise ValueError("file URL must include a path")
    local_path = _resolve_local_path_for_file_url(parsed)
    _require_local_path_exists(local_path, display=url)


def _validate_url(url: str) -> None:
    if "\x00" in url:
        raise ValueError("invalid URL")
    if " " in url:
        raise ValueError("URL must not contain spaces")
    parsed = urlparse(url)
    _reject_unsupported_scheme(parsed.scheme)
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    if parsed.scheme in {"http", "https"}:
        _validate_http_url(parsed)
    if parsed.scheme == "file":
        _validate_file_url(url)
