"""Cookie type + WebView cookie API surface (no live engine)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from support.linux import noop_linux_runtime

from tkwry import Cookie, WebView, WebViewDestroyedError, WebViewNotReadyError


@pytest.fixture(autouse=True)
def _noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    noop_linux_runtime(monkeypatch)


def test_cookie_repr_omits_value() -> None:
    c = Cookie("session", "secret-token", domain="example.com", path="/")
    text = repr(c)
    assert "session" in text
    assert "example.com" in text
    assert "secret-token" not in text
    assert c.value == "secret-token"


def test_cookie_same_site_validation() -> None:
    Cookie("a", "b", same_site="Lax")
    with pytest.raises(ValueError, match="same_site"):
        Cookie("a", "b", same_site="invalid")


def test_cookie_methods_require_ready(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    try:
        with pytest.raises(WebViewNotReadyError):
            web.cookies()
        with pytest.raises(WebViewNotReadyError):
            web.cookies_for_url("https://example.com")
        with pytest.raises(WebViewNotReadyError):
            web.set_cookie(Cookie("n", "v"))
        with pytest.raises(WebViewNotReadyError):
            web.delete_cookie(Cookie("n", "v"))
        with pytest.raises(WebViewNotReadyError):
            web.delete_cookie("n", "https://example.com/")
        with pytest.raises(WebViewNotReadyError):
            web.clear_all_browsing_data()
    finally:
        web.destroy()


def test_cookie_methods_raise_after_destroy(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web.destroy()
    with pytest.raises(WebViewDestroyedError):
        web.cookies()
    with pytest.raises(WebViewDestroyedError):
        web.clear_all_browsing_data()


def test_cookie_methods_delegate_to_native(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    sample = Cookie("sid", "x", domain="example.com", path="/")
    native.cookies.return_value = [sample]
    native.cookies_for_url.return_value = [sample]
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    try:
        assert web.cookies() == [sample]
        assert web.cookies_for_url("https://example.com/") == [sample]
        native.cookies_for_url.assert_called_once_with("https://example.com/")
        web.set_cookie(sample)
        native.set_cookie.assert_called_once_with(sample)
        web.delete_cookie(sample)
        native.delete_cookie.assert_called_once_with(sample)
        native.delete_cookie.reset_mock()
        web.delete_cookie("sid", "https://example.com/app")
        built = native.delete_cookie.call_args[0][0]
        assert built.name == "sid"
        assert built.value == ""
        assert built.domain == "example.com"
        assert built.path == "/"
        web.clear_all_browsing_data()
        native.clear_all_browsing_data.assert_called_once_with()
    finally:
        web._webview = None
        web.destroy()


def test_delete_cookie_name_requires_url(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    try:
        with pytest.raises(TypeError, match="requires a url"):
            web.delete_cookie("sid")
        with pytest.raises(ValueError, match="hostname"):
            web.delete_cookie("sid", "not-a-url")
        native.delete_cookie.assert_not_called()
    finally:
        web._webview = None
        web.destroy()
