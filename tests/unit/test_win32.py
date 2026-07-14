"""Unit tests for Windows WebView2 hard-fail helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import tkinter as tk

from tkwry import WebView
from tkwry._win32 import (
    WEBVIEW2_MISSING_MESSAGE,
    looks_like_webview2_missing,
    webview2_missing_error,
)
from tkwry.exceptions import WebViewCreationError

_real_try_create = WebView._try_create


def test_looks_like_webview2_missing_specific_hresult() -> None:
    assert looks_like_webview2_missing(
        "WebView2 error: WindowsError(Error { code: HRESULT(0x80070002), ... })"
    )
    assert looks_like_webview2_missing("0x8007007E module not found")
    assert not looks_like_webview2_missing(
        "WebView2 error: WindowsError(Error { code: HRESULT(0x80004005), ... })"
    )


def test_webview2_missing_error_message_and_cause() -> None:
    cause = RuntimeError("native boom")
    err = webview2_missing_error(cause)
    assert isinstance(err, WebViewCreationError)
    assert WEBVIEW2_MISSING_MESSAGE in str(err)
    assert err.__cause__ is cause


def test_registry_available_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    from tkwry import _win32

    assert _win32.is_webview2_runtime_available() is False


def test_registry_available_reads_pv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=1,
        HKEY_CURRENT_USER=2,
        OpenKey=MagicMock(return_value=_Key()),
        QueryValueEx=MagicMock(return_value=("120.0.0.1", 1)),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    from tkwry import _win32

    assert _win32.is_webview2_runtime_available() is True


def test_try_create_hard_fails_when_runtime_missing(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)
    monkeypatch.setattr(WebView, "_try_create", lambda self: None)
    web = WebView(frame, width=400, height=300)
    scheduled: list[int] = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "tkwry._win32.is_webview2_runtime_available", lambda: False
    )
    monkeypatch.setattr(WebView, "_try_create", _real_try_create)
    monkeypatch.setattr(
        web, "_schedule_try_create", lambda **_k: scheduled.append(1), raising=False
    )

    web._try_create()

    assert web._webview is None
    assert scheduled == []
    assert web.creation_failed is True
    assert isinstance(web.creation_error, WebViewCreationError)
    assert WEBVIEW2_MISSING_MESSAGE in str(web.creation_error)


def test_try_create_hard_fails_on_missing_hresult(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)
    monkeypatch.setattr(WebView, "_try_create", lambda self: None)
    web = WebView(frame, width=400, height=300)
    scheduled: list[int] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("WebView2 error: HRESULT(0x80070002)")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "tkwry._win32.is_webview2_runtime_available", lambda: True
    )
    monkeypatch.setattr("tkwry.webview.NativeWebView", boom)
    monkeypatch.setattr(WebView, "_try_create", _real_try_create)
    monkeypatch.setattr(
        web, "_schedule_try_create", lambda **_k: scheduled.append(1), raising=False
    )

    web._try_create()

    assert web._webview is None
    assert scheduled == []
    assert WEBVIEW2_MISSING_MESSAGE in str(web.creation_error)
