"""URL normalization and validation for WebView navigation."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse
from urllib.request import url2pathname

_SUPPORTED_SCHEMES = frozenset({"http", "https", "file"})

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
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
_WINDOWS_DRIVE_ONLY_RE = re.compile(r"^[A-Za-z]:$")


def _contains_non_ascii(value: str) -> bool:
    return not value.isascii()


def _is_ipv6_literal(host: str) -> bool:
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


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
            return f"[{addr}]", port, path_suffix
        if rest.startswith("/") or not rest:
            return f"[{addr}]", "", rest
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
        return f"[{maybe_host}]", maybe_port, path_suffix
    if _is_ipv6_literal(authority):
        return f"[{authority}]", "", path_suffix
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


def _is_misparsed_host_port(parsed) -> bool:
    """urlparse treats ``host:port`` as ``scheme='host', path='port'``."""
    if not parsed.scheme or parsed.netloc:
        return False
    if parsed.scheme in _SUPPORTED_SCHEMES:
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
    if _contains_non_ascii(url):
        return True
    # Bare relative segment without dots (e.g. README) — not a network host.
    if ":" not in url and "." not in url:
        return True
    return False


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
    # Do not follow symlinks; resolve() can load a different file than requested.
    return Path(expanded).absolute().as_uri()


def _normalize_file_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        if parsed.netloc in ("", "localhost"):
            pass
        elif _is_windows_drive_netloc(parsed.netloc):
            pathname = f"{parsed.netloc}{parsed.path}"
            pathname = _windows_drive_root_path(pathname)
            if not pathname or pathname.endswith(":"):
                raise ValueError("file URL must include a path")
            return _file_uri_from_path(pathname)
        else:
            # UNC-style ``file://server/share`` requires a non-empty path.
            if not parsed.path or parsed.path == "/":
                raise ValueError("file URL must include a path")
            return url
    pathname = url2pathname(unquote(parsed.path))
    pathname = _strip_leading_slash_from_windows_path(pathname)
    pathname = _windows_drive_root_path(pathname)
    if not pathname:
        raise ValueError("file URL must include a path")
    return _file_uri_from_path(pathname)


def _normalize_url(url: str) -> str:
    cleaned = url.strip()
    for invisible in ("\u200b", "\ufeff", "\u2060"):
        cleaned = cleaned.replace(invisible, "")
    if not cleaned:
        raise ValueError("URL is empty")
    if _is_windows_drive_path(cleaned):
        return _file_uri_from_path(cleaned)
    parsed = urlparse(cleaned)
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


def _validate_http_url(parsed) -> None:
    host = parsed.hostname
    if not host:
        if parsed.netloc:
            raise ValueError("invalid URL host")
        raise ValueError("URL must include a host, e.g. https://example.com")
    if not _is_valid_http_host(host):
        raise ValueError(f"invalid URL host: {host!r}")


def _validate_url(url: str) -> None:
    if "\x00" in url:
        raise ValueError("invalid URL")
    if " " in url:
        raise ValueError("URL must not contain spaces")
    parsed = urlparse(url)
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    if parsed.scheme in {"http", "https"}:
        _validate_http_url(parsed)
    if parsed.scheme == "file" and (not parsed.path or parsed.path == "/"):
        raise ValueError("file URL must include a path")
