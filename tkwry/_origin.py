"""Origin helpers for IPC/RPC allowlists and navigation policy."""

from __future__ import annotations

import warnings
import webbrowser
from collections.abc import Collection, Iterable
from typing import Literal, TypeAlias
from urllib.parse import urlparse

from tkwry.exceptions import TkwrySecurityWarning

BridgeAllowlist: TypeAlias = Literal["*"] | frozenset[str]

APP_ORIGINS = frozenset(
    {
        "tkwry://localhost",
        "tkwry://app",
        "https://tkwry.localhost",
    }
)
INLINE_ORIGINS = frozenset({"about:blank", "null"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
# app= must not escape via opaque documents. Not applied globally in Rust:
# WebView2 ``html=`` uses NavigateToString (``data:``). untrusted+html= still
# allows ``data:`` (null origin, no bridge).
_APP_NAV_DENIED_SCHEMES = frozenset(
    {"javascript", "data", "vbscript", "blob", "mailto"}
)

STAR_BRIDGE_WARNING = (
    'bridge_origins="*" lets every page call window.ipc / window.tkwry; '
    "prefer concrete origins or path prefixes, or untrusted=True for a viewer. "
    "expose() also requires allow_any_origin=True when using '*'."
)


def origin_of(url: str | None) -> str:
    """Return a comparable origin (``scheme://host[:port]``) for *url*."""
    if url is None:
        return "null"
    raw = url.strip()
    if not raw:
        return "null"
    lower = raw.lower()
    if lower in {"about:blank", "about:srcdoc", "about:"}:
        return "about:blank"
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"", "about"}:
        return "about:blank" if "blank" in lower else "null"
    if scheme == "data":
        return "null"
    if scheme == "blob":
        return "null"
    host = (parsed.hostname or "").lower()
    if not host:
        netloc = (parsed.netloc or "").lower()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        host = netloc.split(":")[0]
    if scheme == "file":
        return "file://"
    if not host:
        return f"{scheme}://" if scheme else "null"
    port = parsed.port
    default_port = _DEFAULT_PORTS.get(scheme)
    if port is not None and port == default_port:
        port = None
    if port is not None:
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{scheme}://{host}:{port}"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}"


def url_path_of(url: str | None) -> str:
    """Return the URL path (``/`` when missing) for prefix matching."""
    if not url or not url.strip():
        return "/"
    path = urlparse(url.strip()).path or "/"
    return path if path.startswith("/") else f"/{path}"


def path_prefix_matches(path: str, prefix: str) -> bool:
    """True when *path* is *prefix* or a descendant.

    ``/app`` matches ``/app`` and ``/app/x``, not ``/application``.
    """

    if not prefix or prefix == "/":
        return True
    if path == prefix:
        return True
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path.startswith(prefix + "/")


def _split_bridge_entry(entry: str) -> tuple[str, str | None]:
    """Return ``(origin, path_prefix)``; ``None`` prefix means any path."""
    if entry in {"null", "about:blank", "*"}:
        return entry, None
    origin = origin_of(entry)
    path = urlparse(entry.strip()).path or ""
    if not path or path == "/":
        return origin, None
    if not path.startswith("/"):
        path = f"/{path}"
    return origin, path


def normalize_bridge_entry(value: str) -> str:
    """Normalize one allowlist entry: origin, or origin + path prefix."""
    text = value.strip()
    if not text or text == "*":
        raise ValueError(
            "bridge origin must be a concrete origin or origin+path; "
            "use bridge_origins='*' to allow every page"
        )
    if text in {"null", "about:blank"}:
        return text
    origin = origin_of(text)
    path = urlparse(text).path or ""
    if not path or path == "/":
        return origin
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"


def warn_star_bridge_origins(*, stacklevel: int) -> None:
    warnings.warn(STAR_BRIDGE_WARNING, TkwrySecurityWarning, stacklevel=stacklevel)


def resolve_bridge_origins(
    explicit: Literal["*"] | Collection[str] | None,
    *,
    url: str | None,
    html: str | None,
    app: bool,
) -> BridgeAllowlist:
    """Resolve constructor ``bridge_origins`` (``None`` infers from content)."""
    if explicit == "*":
        return "*"
    if isinstance(explicit, str):
        raise TypeError(
            "bridge_origins must be '*' or a sequence of origin strings, "
            "not a single URL string"
        )
    if explicit is not None:
        origins = frozenset(normalize_bridge_entry(item) for item in explicit)
        if not origins:
            raise ValueError("bridge_origins must not be empty")
        return origins
    if html is not None:
        return INLINE_ORIGINS
    if app:
        return APP_ORIGINS
    if url:
        return frozenset({origin_of(url)})
    return INLINE_ORIGINS


def origin_allowed(url: str | None, allowlist: BridgeAllowlist) -> bool:
    """True when *url* matches ``"*"``, an origin, or an origin+path prefix."""
    if allowlist == "*":
        return True
    origin = origin_of(url)
    path = url_path_of(url)
    for entry in allowlist:
        entry_origin, prefix = _split_bridge_entry(entry)
        if (
            origin == "null"
            and entry_origin in {"null", "about:blank"}
            and prefix is None
        ):
            return True
        if (
            origin == "about:blank"
            and entry_origin in {"null", "about:blank"}
            and prefix is None
        ):
            return True
        if origin != entry_origin:
            continue
        if prefix is None or path_prefix_matches(path, prefix):
            return True
    return False


def _nav_scheme(url: str) -> str:
    return (urlparse(url.strip()).scheme or "").lower()


def app_navigation_allowed(url: str) -> bool:
    """Default ``app=`` policy: stay on ``tkwry://`` (plus blank during load)."""
    if _nav_scheme(url) in _APP_NAV_DENIED_SCHEMES:
        return False
    origin = origin_of(url)
    return origin in APP_ORIGINS or origin in INLINE_ORIGINS


def untrusted_navigation_allowed(url: str) -> bool:
    """Viewer policy: http(s) only; never ``tkwry://`` / ``file:`` / opaque apps."""
    origin = origin_of(url)
    if origin in INLINE_ORIGINS:
        return True
    return is_external_http_url(url)


def is_external_http_url(url: str) -> bool:
    """True for http(s) that is not the WebView2 ``tkwry.localhost`` rewrite."""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if host == "tkwry.localhost" or host.endswith(".tkwry.localhost"):
        return False
    return True


def normalize_navigation_allow(explicit: Collection[str]) -> frozenset[str]:
    """Normalize ``navigation_allow`` (origins or origin+path; no ``"*"``)."""
    if isinstance(explicit, str):
        raise TypeError(
            "navigation_allow must be a sequence of origin strings, "
            "not a single URL string"
        )
    origins = frozenset(normalize_bridge_entry(item) for item in explicit)
    if not origins:
        raise ValueError("navigation_allow must not be empty")
    return origins


def open_in_browser(url: str) -> bool:
    """Open *url* in the system browser.

    Only http(s) (not ``tkwry.localhost``). Returns ``True`` when
    ``webbrowser.open`` was invoked. Do **not** create a WebView from
    ``on_new_window`` — call this instead (preferably via ``after``).
    """
    if not is_external_http_url(url):
        return False
    return bool(webbrowser.open(url, new=2))


def format_allowlist(allowlist: BridgeAllowlist) -> str:
    if allowlist == "*":
        return "*"
    return ", ".join(sorted(allowlist))


def iter_allowlist(allowlist: BridgeAllowlist) -> Iterable[str]:
    if allowlist == "*":
        return ("*",)
    return sorted(allowlist)
