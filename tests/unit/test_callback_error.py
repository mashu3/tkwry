"""Provisional on_callback_error / set_on_callback_error."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import PageLoadEvent, WebView, WebViewDestroyedError


def _make_web(tk_root, **kwargs: object) -> WebView:
    frame = tk.Frame(tk_root)
    return WebView(frame, **kwargs)


def test_invoke_callback_default_logs_without_handler(
    tk_root, capsys: pytest.CaptureFixture[str]
) -> None:
    web = _make_web(tk_root)

    def boom() -> None:
        raise RuntimeError("boom")

    try:
        web._invoke_callback(boom, kind="test_hook")
        err = capsys.readouterr().err
        assert "RuntimeError: boom" in err
    finally:
        web.destroy()


def test_on_callback_error_receives_exception_and_kind(tk_root) -> None:
    seen: list[tuple[BaseException, str]] = []

    def error_handler(exc: BaseException, kind: str) -> None:
        seen.append((exc, kind))

    web = _make_web(tk_root, on_callback_error=error_handler)
    try:
        web._invoke_callback(
            lambda: (_ for _ in ()).throw(ValueError("bad")),
            kind="on_page_load",
        )
        assert len(seen) == 1
        exc, kind = seen[0]
        assert isinstance(exc, ValueError)
        assert str(exc) == "bad"
        assert kind == "on_page_load"
    finally:
        web.destroy()


def test_set_on_callback_error(tk_root) -> None:
    seen: list[str] = []
    web = _make_web(tk_root)
    try:
        web.set_on_callback_error(lambda _exc, kind: seen.append(kind))
        web._invoke_callback(
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
            kind="ipc_handler",
        )
        assert seen == ["ipc_handler"]
        web.set_on_callback_error(None)
        web._invoke_callback(
            lambda: (_ for _ in ()).throw(RuntimeError("y")),
            kind="ipc_handler",
        )
        assert seen == ["ipc_handler"]
    finally:
        web.destroy()


def test_error_handler_exception_falls_back_to_stderr(
    tk_root, capsys: pytest.CaptureFixture[str]
) -> None:
    def bad_handler(_exc: BaseException, _kind: str) -> None:
        raise TypeError("handler failed")

    web = _make_web(tk_root, on_callback_error=bad_handler)
    try:
        web._invoke_callback(
            lambda: (_ for _ in ()).throw(RuntimeError("orig")),
            kind="on_title_changed",
        )
        err = capsys.readouterr().err
        assert "TypeError: handler failed" in err
    finally:
        web.destroy()


def test_error_handler_failure_does_not_recurse(
    tk_root, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the failing callback *is* the error handler, route to stderr only."""
    calls = 0

    def error_handler(exc: BaseException, kind: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("from handler")

    web = _make_web(tk_root, on_callback_error=error_handler)
    try:
        web._invoke_callback(
            error_handler,
            ValueError("direct"),
            "passed_as_arg",
            kind="should_not_recurse",
        )
        assert calls == 1
        err = capsys.readouterr().err
        assert "RuntimeError: from handler" in err
    finally:
        web.destroy()


def test_set_on_callback_error_raises_after_destroy(tk_root) -> None:
    web = _make_web(tk_root)
    web.destroy()
    with pytest.raises(WebViewDestroyedError, match="set_on_callback_error"):
        web.set_on_callback_error(lambda *_a: None)


def test_deliver_page_load_routes_through_callback_error(tk_root) -> None:
    seen: list[str] = []
    web = _make_web(tk_root, on_callback_error=lambda _e, k: seen.append(k))
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/")
    ]
    web._webview = native

    def boom(_evt: object, _url: str) -> None:
        raise RuntimeError("page load")

    web.set_on_page_load(boom)
    try:
        assert seen == ["on_page_load"]
    finally:
        web._webview = None
        web.destroy()
