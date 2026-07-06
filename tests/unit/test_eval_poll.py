"""Tests for eval_js_with_callback polling and error handling."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from tkwry import WebView


def _suppress_poll_timer(web: WebView, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block _poll_events reschedule; after_idle and other timers still run."""
    original = web._frame.after

    def after(delay, func=None, *args):
        if func is web._poll_events:
            return ""
        if func is None:
            return original(delay)
        return original(delay, func, *args)

    monkeypatch.setattr(web._frame, "after", after)


def test_should_keep_polling_while_eval_pending(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    web._pending_eval_callbacks = 1

    assert web._should_keep_polling() is True


def test_poll_events_keeps_polling_until_eval_delivers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    _suppress_poll_timer(web, monkeypatch)
    web._pending_eval_callbacks = 1
    web._event_poll_active = True

    web._poll_events()

    assert web._event_poll_active is True


def test_poll_events_drains_late_eval_result(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    _suppress_poll_timer(web, monkeypatch)
    results: list[str] = []
    web._eval_result_queue.put((results.append, "ok"))
    web._event_poll_active = True

    web._poll_events()

    assert results == ["ok"]
    assert web._event_poll_active is False


def test_eval_js_with_callback_on_error(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._webview = MagicMock()
    web._webview.eval_js_with_callback.side_effect = RuntimeError("boom")
    errors: list[BaseException] = []

    web.eval_js_with_callback("1", lambda _result: None, on_error=errors.append)
    tk_root.update_idletasks()
    tk_root.update()

    assert len(errors) == 1
    assert str(errors[0]) == "boom"
    assert web._pending_eval_callbacks == 0


def test_eval_js_with_callback_pending_keeps_poll_until_deliver(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    _suppress_poll_timer(web, monkeypatch)
    web._webview = MagicMock()
    results: list[str] = []

    def native_eval(_script: str, deliver: Callable[[str], None]) -> None:
        web._poll_events()
        assert web._event_poll_active is True
        assert results == []
        deliver("ok")

    web._webview.eval_js_with_callback.side_effect = native_eval

    web.eval_js_with_callback("'ok'", results.append)
    for _ in range(5):
        tk_root.update_idletasks()
        tk_root.update()
        if results:
            break
        web._poll_events()

    assert results == ["ok"]
    assert web._pending_eval_callbacks == 0
