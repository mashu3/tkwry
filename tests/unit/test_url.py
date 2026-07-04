"""Tests for URL normalization (no native WebView required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry._url import _normalize_url, _validate_url


def test_normalize_adds_https() -> None:
    assert _normalize_url("example.com") == "https://example.com"


def test_normalize_strips_whitespace() -> None:
    assert _normalize_url("  https://example.com  ") == "https://example.com"


def test_normalize_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        _normalize_url("   ")


def test_validate_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("javascript:alert(1)")


def test_validate_requires_host() -> None:
    with pytest.raises(ValueError, match="host"):
        _validate_url("https://")


def test_normalize_strips_zero_width_chars() -> None:
    assert _normalize_url("\u200bhttps://example.com\u200b") == "https://example.com"


def test_validate_rejects_null_byte() -> None:
    with pytest.raises(ValueError, match="invalid"):
        _validate_url("https://example.com/\x00")


def test_normalize_absolute_path_to_file_uri(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text("<p>local</p>", encoding="utf-8")

    assert _normalize_url(str(page)) == page.resolve().as_uri()


def test_normalize_file_uri(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text("<p>local</p>", encoding="utf-8")
    file_url = page.resolve().as_uri()

    assert _normalize_url(file_url) == file_url


def test_validate_accepts_file_uri(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text("<p>local</p>", encoding="utf-8")
    file_url = page.resolve().as_uri()

    _validate_url(file_url)


def test_normalize_relative_path_to_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "index.html"
    page.write_text("<p>local</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _normalize_url("./index.html") == page.resolve().as_uri()


def test_normalize_windows_drive_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_file_uri(path: str) -> str:
        seen.append(path)
        return "file:///C:/Users/foo/index.html"

    monkeypatch.setattr("tkwry._url._file_uri_from_path", fake_file_uri)

    assert (
        _normalize_url(r"C:\Users\foo\index.html") == "file:///C:/Users/foo/index.html"
    )
    assert seen == [r"C:\Users\foo\index.html"]
