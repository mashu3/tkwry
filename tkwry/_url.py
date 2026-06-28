"""URL normalization and validation for WebView navigation."""

from __future__ import annotations

from urllib.parse import urlparse


def _normalize_url(url: str) -> str:
    cleaned = url.strip()
    for invisible in ("\u200b", "\ufeff", "\u2060"):
        cleaned = cleaned.replace(invisible, "")
    if not cleaned:
        raise ValueError("URL is empty")
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        if " " in cleaned:
            raise ValueError("URL must not contain spaces")
        cleaned = f"https://{cleaned}"
    return cleaned


def _validate_url(url: str) -> None:
    if "\x00" in url:
        raise ValueError("invalid URL")
    if " " in url:
        raise ValueError("URL must not contain spaces")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("URL must include a host, e.g. https://example.com")
