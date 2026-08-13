"""Origin helpers for IPC/RPC allowlists and navigation policy."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Literal, TypeAlias
from urllib.parse import urlparse

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


def normalize_bridge_origin(value: str) -> str:
    text = value.strip()
    if not text or text == "*":
        raise ValueError(
            "bridge origin must be a concrete origin; use bridge_origins='*' "
            "to allow every page"
        )
    if text in {"null", "about:blank"}:
        return text
    return origin_of(text)


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
        origins = frozenset(normalize_bridge_origin(item) for item in explicit)
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
    if allowlist == "*":
        return True
    origin = origin_of(url)
    if origin in allowlist:
        return True
    if origin == "null" and "about:blank" in allowlist:
        return True
    if origin == "about:blank" and "null" in allowlist:
        return True
    return False


def app_navigation_allowed(url: str) -> bool:
    """Default ``app=`` policy: stay on ``tkwry://`` (plus blank during load)."""
    origin = origin_of(url)
    return origin in APP_ORIGINS or origin in INLINE_ORIGINS


def untrusted_navigation_allowed(url: str) -> bool:
    """Viewer policy: http(s) only; never ``tkwry://`` / ``file:`` / opaque apps."""
    origin = origin_of(url)
    if origin in INLINE_ORIGINS:
        return True
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if host == "tkwry.localhost" or host.endswith(".tkwry.localhost"):
        return False
    return True


def format_allowlist(allowlist: BridgeAllowlist) -> str:
    if allowlist == "*":
        return "*"
    return ", ".join(sorted(allowlist))


def iter_allowlist(allowlist: BridgeAllowlist) -> Iterable[str]:
    if allowlist == "*":
        return ("*",)
    return sorted(allowlist)
