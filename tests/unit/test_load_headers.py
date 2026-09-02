"""load_url(headers=) validation and native delegation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from support.linux import noop_linux_runtime

from tkwry import WebView
from tkwry._url import _normalize_load_headers


@pytest.fixture(autouse=True)
def _noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    noop_linux_runtime(monkeypatch)


def test_normalize_load_headers_empty_is_none() -> None:
    assert _normalize_load_headers(None) is None
    assert _normalize_load_headers({}) is None


def test_normalize_load_headers_rejects_crlf_in_value() -> None:
    with pytest.raises(ValueError, match="invalid header value for 'X-Token'"):
        _normalize_load_headers({"X-Token": "a\r\nb"})


def test_normalize_load_headers_error_omits_secret() -> None:
    with pytest.raises(ValueError) as excinfo:
        _normalize_load_headers({"Authorization": "Bearer SECRET\n"})
    assert "SECRET" not in str(excinfo.value)


def test_normalize_load_headers_rejects_invalid_token_name() -> None:
    with pytest.raises(ValueError, match="invalid header name"):
        _normalize_load_headers({"Bad Name": "1"})


def test_load_url_headers_requires_https(tk_root, tmp_path) -> None:
    import tkinter as tk

    page = tmp_path / "x.html"
    page.write_text("<p>x</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    try:
        with pytest.raises(ValueError, match="http\\(s\\)"):
            web.load_url(str(page), headers={"X-Test": "1"})
    finally:
        web.destroy()


def test_load_url_with_headers_delegates(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    try:
        web.load_url(
            "https://example.com/api",
            headers={"X-Request-Id": "abc", "Accept": "application/json"},
        )
        if web._pending_load is not None:
            web._flush_load()
        native.load_url_with_headers.assert_called_once()
        args = native.load_url_with_headers.call_args[0]
        assert args[0] == "https://example.com/api"
        assert ("X-Request-Id", "abc") in args[1]
        assert ("Accept", "application/json") in args[1]
        native.load_url.assert_not_called()
    finally:
        web._webview = None
        web.destroy()


def test_load_url_headers_last_wins(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    # Avoid auto-flush on Linux during queue.
    monkeypatch.setattr(web, "_dispatch_pending_load", lambda: None)
    try:
        web.load_url("https://example.com/a", headers={"X-A": "1"})
        web.load_url("https://example.com/b", headers={"X-B": "2"})
        assert web._pending_load == (
            "url",
            "https://example.com/b",
            (("X-B", "2"),),
        )
        web._flush_load()
        native.load_url_with_headers.assert_called_once_with(
            "https://example.com/b", [("X-B", "2")]
        )
    finally:
        web._webview = None
        web.destroy()


def test_flush_load_skips_retry_on_value_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    native.load_url.side_effect = ValueError("engine rejected load")
    web._webview = native
    web._pending_load = ("url", "https://example.com", None)
    retries: list[int] = []

    def _track_retry(**_kwargs: object) -> None:
        retries.append(1)

    monkeypatch.setattr(web, "_schedule_flush_load", _track_retry)
    try:
        web._flush_load()
        assert retries == []
        assert web._pending_load is None
    finally:
        web._webview = None
        web.destroy()
