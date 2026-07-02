"""Tests for URL normalization (no native WebView required)."""

from __future__ import annotations

import pytest

from tkwry._url import _normalize_url, _validate_url


def test_normalize_adds_https() -> None:
    assert _normalize_url("example.com") == "https://example.com"


def test_normalize_strips_whitespace() -> None:
    assert _normalize_url("  https://example.com  ") == "https://example.com"


def test_normalize_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        _normalize_url("   ")


def test_validate_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("file:///etc/passwd")


def test_validate_requires_host() -> None:
    with pytest.raises(ValueError, match="host"):
        _validate_url("https://")


def test_normalize_strips_zero_width_chars() -> None:
    assert _normalize_url("\u200bhttps://example.com\u200b") == "https://example.com"


def test_validate_rejects_javascript_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("javascript:alert(1)")


def test_validate_rejects_null_byte() -> None:
    with pytest.raises(ValueError, match="invalid"):
        _validate_url("https://example.com/\x00")
