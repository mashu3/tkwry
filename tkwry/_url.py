"""URL normalization and validation for WebView navigation."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

_SUPPORTED_SCHEMES = frozenset({"http", "https", "file"})


def _looks_like_file_path(url: str) -> bool:
    if url.startswith(("/", "./", "../", "~")):
        return True
    if len(url) >= 2 and url[0] == "." and url[1] in "/\\":
        return True
    if len(url) >= 3 and url[0].isalpha() and url[1] == ":" and url[2] in "/\\":
        return True
    return Path(os.path.expanduser(url)).exists()


def _file_uri_from_path(path: str) -> str:
    expanded = os.path.expanduser(path.strip())
    return Path(expanded).resolve().as_uri()


def _normalize_file_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        return url
    pathname = url2pathname(unquote(parsed.path))
    if not pathname:
        raise ValueError("file URL must include a path")
    return _file_uri_from_path(pathname)


def _normalize_url(url: str) -> str:
    cleaned = url.strip()
    for invisible in ("\u200b", "\ufeff", "\u2060"):
        cleaned = cleaned.replace(invisible, "")
    if not cleaned:
        raise ValueError("URL is empty")
    parsed = urlparse(cleaned)
    if parsed.scheme == "file":
        return _normalize_file_url(cleaned)
    if not parsed.scheme:
        if " " in cleaned:
            raise ValueError("URL must not contain spaces")
        if _looks_like_file_path(cleaned):
            return _file_uri_from_path(cleaned)
        cleaned = f"https://{cleaned}"
    return cleaned


def _validate_url(url: str) -> None:
    if "\x00" in url:
        raise ValueError("invalid URL")
    if " " in url:
        raise ValueError("URL must not contain spaces")
    parsed = urlparse(url)
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("URL must include a host, e.g. https://example.com")
    if parsed.scheme == "file" and not parsed.path and not parsed.netloc:
        raise ValueError("file URL must include a path")
