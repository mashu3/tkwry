"""Tests for URL normalization (no native WebView required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry._url import _normalize_url, _validate_url


def _paths_equal(actual: str, expected: str) -> bool:
    return actual.replace("\\", "/") == expected.replace("\\", "/")


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


def test_normalize_host_port_without_scheme() -> None:
    assert _normalize_url("localhost:8080") == "https://localhost:8080"
    assert _normalize_url("localhost:8080/") == "https://localhost:8080/"
    assert _normalize_url("localhost:8080/api") == "https://localhost:8080/api"
    assert _normalize_url("127.0.0.1:8080") == "https://127.0.0.1:8080"
    assert _normalize_url("example.com:8080") == "https://example.com:8080"
    assert _normalize_url("example.com:8080/api") == "https://example.com:8080/api"


def test_localhost_port_recovers_from_urlparse_misparsing() -> None:
    """Regression: urlparse treats ``host:port`` as scheme + path, not netloc."""
    from urllib.parse import urlparse

    raw = "localhost:8080"
    parsed = urlparse(raw)
    assert parsed.scheme == "localhost"
    assert parsed.netloc == ""
    assert parsed.path == "8080"

    with pytest.raises(ValueError, match="unsupported URL scheme: 'localhost'"):
        _validate_url(raw)

    normalized = _normalize_url(raw)
    assert normalized == "https://localhost:8080"
    _validate_url(normalized)


def test_normalize_host_path_without_scheme() -> None:
    assert _normalize_url("localhost/path") == "https://localhost/path"
    assert _normalize_url("example.com/path") == "https://example.com/path"


def test_relative_paths_are_not_https_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme").write_text("x", encoding="utf-8")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "v1").write_text("x", encoding="utf-8")
    (tmp_path / "myserver").mkdir()
    (tmp_path / "myserver" / "api").write_text("x", encoding="utf-8")

    assert _normalize_url("README") == (tmp_path / "README").resolve().as_uri()
    assert (
        _normalize_url("docs/readme")
        == (tmp_path / "docs" / "readme").resolve().as_uri()
    )
    assert _normalize_url("api/v1") == (tmp_path / "api" / "v1").resolve().as_uri()
    assert (
        _normalize_url("myserver/api")
        == (tmp_path / "myserver" / "api").resolve().as_uri()
    )


def test_normalize_host_port_passes_validation() -> None:
    cases = (
        "localhost:8080",
        "localhost:8080/api",
        "127.0.0.1:8080",
        "example.com:8080",
        "localhost/path",
        "example.com/path",
    )
    for url in cases:
        _validate_url(_normalize_url(url))


def test_file_url_requires_path() -> None:
    with pytest.raises(ValueError, match="path"):
        _normalize_url("file://server")
    with pytest.raises(ValueError, match="path"):
        _validate_url("file://server")
    # UNC-style with a share path remains allowed.
    assert _normalize_url("file://server/share/x") == "file://server/share/x"
    _validate_url("file://server/share/x")


def test_normalize_relative_path_still_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "subdir" / "page.html"
    page.parent.mkdir()
    page.write_text("<p>local</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _normalize_url("subdir/page.html") == page.resolve().as_uri()
    assert _normalize_url("./subdir/page.html") == page.resolve().as_uri()


def test_normalize_bare_filename_to_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "index.html"
    page.write_text("<p>local</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _normalize_url("index.html") == page.resolve().as_uri()


def test_normalize_ipv6_host_port_without_scheme() -> None:
    assert _normalize_url("::1:8080") == "https://[::1]:8080"
    assert _normalize_url("[::1]:8080") == "https://[::1]:8080"
    assert _normalize_url("::1") == "https://[::1]"
    assert _normalize_url("[::1]/path") == "https://[::1]/path"
    assert _normalize_url("::1:8080/api") == "https://[::1]:8080/api"


def test_normalize_ipv6_https_authority() -> None:
    assert _normalize_url("https://::1:8080") == "https://[::1]:8080"
    assert _normalize_url("https://::1") == "https://[::1]"


def test_normalize_idn_hostname_without_scheme() -> None:
    assert _normalize_url("例子.test") == "https://例子.test"
    assert _normalize_url("例子.test/path") == "https://例子.test/path"


def test_normalize_unicode_filename_to_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "ページ.html"
    page.write_text("<p>local</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _normalize_url("ページ.html") == page.resolve().as_uri()


def test_normalize_windows_drive_root_file_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_file_uri(path: str) -> str:
        seen.append(path)
        return "file:///C:/"

    monkeypatch.setattr("tkwry._url._file_uri_from_path", fake_file_uri)

    assert _normalize_url("file:///C:") == "file:///C:/"
    assert _normalize_url("file:///C:/") == "file:///C:/"
    assert seen == ["C:\\", "C:\\"]


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


def test_normalize_windows_file_uri_two_slash_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_file_uri(path: str) -> str:
        seen.append(path)
        return "file:///C:/Users/foo/index.html"

    monkeypatch.setattr("tkwry._url._file_uri_from_path", fake_file_uri)

    assert (
        _normalize_url("file://C:/Users/foo/index.html")
        == "file:///C:/Users/foo/index.html"
    )
    assert seen == ["C:/Users/foo/index.html"]


def test_normalize_windows_file_uri_three_slash_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_file_uri(path: str) -> str:
        seen.append(path)
        return "file:///C:/Users/foo/index.html"

    monkeypatch.setattr("tkwry._url._file_uri_from_path", fake_file_uri)

    assert (
        _normalize_url("file:///C:/Users/foo/index.html")
        == "file:///C:/Users/foo/index.html"
    )
    assert len(seen) == 1
    assert _paths_equal(seen[0], "C:/Users/foo/index.html")
