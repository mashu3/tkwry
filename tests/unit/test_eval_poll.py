"""Tests for eval_js_with_callback polling and error handling."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep eval-poll unit tests off GTK/WebKitGTK in headless Linux CI."""
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def _make_web(tk_root):
    import tkinter as tk

    frame = tk.Frame(tk_root)
    # Avoid eager native create in headless Linux CI unit tests.
    return frame, WebView(frame)


def _stub_native_ready(
    web: WebView, native: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a mock native view without triggering layout or WebKit create."""
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)


def _configure_poll_test(web: WebView, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate poll logic from GTK pumps and rescheduling timers."""
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    original = web._frame.after

    def after(delay, func=None, *args):
        if func is web._poll_events:
            return ""
        if func is None:
            return original(delay)
        return original(delay, func, *args)

    monkeypatch.setattr(web._frame, "after", after)


def test_should_keep_polling_while_eval_pending(tk_root) -> None:
    _frame, web = _make_web(tk_root)

    web._pending_eval_callbacks = 1

    assert web._should_keep_polling() is True


def test_should_keep_polling_while_native_eval_wait(tk_root) -> None:
    _frame, web = _make_web(tk_root)

    web._native_eval_wait[1] = (0, 1, lambda _r: None, None)

    assert web._should_keep_polling() is True


def test_poll_events_keeps_polling_until_eval_delivers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    web._pending_eval_callbacks = 1
    web._event_poll_active = True

    web._poll_events()

    assert web._event_poll_active is True


def test_poll_events_drains_native_eval_callbacks(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    web._webview = native
    results: list[str] = []
    py_token = web._register_pending_eval(results.append, None)
    web._native_eval_wait[1] = (web._eval_epoch, py_token, results.append, None)
    native.drain_eval_callbacks.return_value = [(1, results.append, "ok")]
    web._event_poll_active = True

    web._poll_events()

    assert results == ["ok"]
    assert web._pending_eval_callbacks == 0
    assert web._event_poll_active is False


def test_poll_events_rearms_if_eval_pending_arrives_during_stop(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-check after stopping poll so a concurrent eval is not stranded."""
    _frame, web = _make_web(tk_root)
    results: list[str] = []
    ensure_calls: list[int] = []
    web._event_poll_active = True
    original_ensure = web._ensure_event_poll

    def tracking_ensure() -> None:
        ensure_calls.append(1)
        original_ensure()

    monkeypatch.setattr(web, "_ensure_event_poll", tracking_ensure)
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )

    check_count = {"n": 0}

    def should_keep() -> bool:
        check_count["n"] += 1
        if check_count["n"] == 1:
            token = web._register_pending_eval(results.append, None)
            web._native_eval_wait[99] = (web._eval_epoch, token, results.append, None)
            return False
        return web._pending_eval_callbacks > 0 or bool(web._native_eval_wait)

    monkeypatch.setattr(web, "_should_keep_polling", should_keep)
    original_after = web._frame.after

    def after(delay, func=None, *args):
        if func is web._poll_events:
            return ""
        if func is None:
            return original_after(delay)
        return original_after(delay, func, *args)

    monkeypatch.setattr(web._frame, "after", after)

    web._poll_events()

    assert web._event_poll_active is True
    assert ensure_calls == [1]
    assert not results


def test_eval_js_with_callback_uses_native_stub_not_user_callback(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    native.eval_js_with_callback.return_value = 1
    _stub_native_ready(web, native, monkeypatch)
    results: list[str] = []
    user_cb = results.append

    web.eval_js_with_callback("'ok'", user_cb)
    tk_root.update_idletasks()
    tk_root.update()

    native.eval_js_with_callback.assert_called_once()
    rust_cb = native.eval_js_with_callback.call_args[0][1]
    assert rust_cb is not user_cb
    assert rust_cb.__name__ == "_noop_native_eval_callback"


def test_eval_js_with_callback_on_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    native.eval_js_with_callback.side_effect = RuntimeError("boom")
    _stub_native_ready(web, native, monkeypatch)
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
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    results: list[str] = []

    poll_calls = {"n": 0}

    def native_eval(_script: str, callback: Callable[[str], None]) -> int:
        poll_calls["n"] += 1
        if poll_calls["n"] == 1:
            web._poll_events()
            assert web._event_poll_active is True
            assert results == []
            assert web._pending_eval_callbacks == 1
        return 1

    def drain_eval() -> list[tuple[int, Callable[[str], None], str]]:
        if poll_calls["n"] >= 1 and results == []:
            return [(1, results.append, "ok")]
        return []

    native.eval_js_with_callback.side_effect = native_eval
    native.drain_eval_callbacks.side_effect = drain_eval
    _stub_native_ready(web, native, monkeypatch)

    web.eval_js_with_callback("'ok'", results.append)
    for _ in range(5):
        tk_root.update_idletasks()
        tk_root.update()
        if results:
            break
        web._poll_events()

    assert results == ["ok"]
    assert web._pending_eval_callbacks == 0


def test_destroy_drops_pending_eval_callbacks(tk_root) -> None:
    _frame, web = _make_web(tk_root)
    results: list[str] = []
    web._register_pending_eval(results.append, None)
    epoch_before = web._eval_epoch

    web.destroy()

    assert web._eval_epoch == epoch_before + 1
    assert web._pending_eval_callbacks == 0
    assert not web._pending_eval_tokens
    assert results == []


def test_teardown_native_clears_eval_and_ready_like_destroy(tk_root) -> None:
    """Emergency teardown must share terminal bookkeeping with destroy()."""
    _frame, web = _make_web(tk_root)
    results: list[str] = []
    web._register_pending_eval(results.append, None)
    web._native_eval_wait[7] = (web._eval_epoch, 0, results.append, None)
    web._pending_eval_js = ("1+1", None)
    web._eval_js_scheduled = True
    web._ready_delivered = True
    web._ready_pending = True
    web._ready_callbacks.append(lambda: results.append("ready"))
    epoch_before = web._eval_epoch

    web._teardown_native_if_alive()

    assert web._destroyed is True
    assert web._eval_epoch == epoch_before + 1
    assert web._pending_eval_callbacks == 0
    assert not web._pending_eval_tokens
    assert not web._native_eval_wait
    assert web._pending_eval_js is None
    assert web._eval_js_scheduled is False
    assert web._ready_delivered is False
    assert web._ready_pending is False
    assert web._ready_callbacks == []
    assert results == []


def test_drain_drops_native_eval_when_epoch_mismatches(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    web._webview = native
    results: list[str] = []
    py_token = web._register_pending_eval(results.append, None)
    wait_epoch = web._eval_epoch
    web._native_eval_wait[1] = (wait_epoch, py_token, results.append, None)
    native.drain_eval_callbacks.return_value = [(1, results.append, "stale")]

    # Generation bumped without clearing waits (destructive race); drain must drop.
    web._eval_epoch = wait_epoch + 1
    web._event_poll_active = True
    web._poll_events()

    assert results == []
    assert py_token not in web._pending_eval_tokens
    assert not web._native_eval_wait
    assert web._eval_epoch == wait_epoch + 1

def test_poll_events_expires_stale_eval_callbacks(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    results: list[str] = []
    token = web._register_pending_eval(results.append, None)
    web._pending_eval_tokens[token] = (0.0, results.append, None)
    web._event_poll_active = True

    web._poll_events()

    assert results == []
    assert web._pending_eval_callbacks == 0
    assert not web._pending_eval_tokens
    assert web._event_poll_active is False


def test_poll_events_does_not_double_invoke_after_eval_timeout(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    web._webview = native
    results: list[str] = []
    py_token = web._register_pending_eval(results.append, None)
    web._pending_eval_tokens[py_token] = (0.0, results.append, None)
    web._native_eval_wait[1] = (web._eval_epoch, py_token, results.append, None)
    native.drain_eval_callbacks.return_value = [(1, results.append, "late")]
    web._event_poll_active = True

    web._poll_events()

    assert results == []
    assert web._pending_eval_callbacks == 0
    assert not web._native_eval_wait


def test_poll_stops_after_timeout_when_native_eval_never_returns(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    native = MagicMock()
    web._webview = native
    results: list[str] = []
    py_token = web._register_pending_eval(results.append, None)
    web._pending_eval_tokens[py_token] = (0.0, results.append, None)
    web._native_eval_wait[1] = (web._eval_epoch, py_token, results.append, None)
    native.drain_eval_callbacks.return_value = []
    web._event_poll_active = True

    web._poll_events()

    assert results == []
    assert web._pending_eval_callbacks == 0
    assert not web._native_eval_wait
    assert web._event_poll_active is False

    native.drain_eval_callbacks.return_value = [(1, results.append, "late")]
    web._poll_events()

    assert results == []
    assert not web._native_eval_wait
    assert web._event_poll_active is False


def test_poll_events_expires_stale_eval_callbacks_with_on_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _make_web(tk_root)
    _configure_poll_test(web, monkeypatch)
    errors: list[BaseException] = []
    token = web._register_pending_eval(lambda _r: None, errors.append)
    web._pending_eval_tokens[token] = (0.0, lambda _r: None, errors.append)
    web._event_poll_active = True

    web._poll_events()

    assert len(errors) == 1
    assert isinstance(errors[0], TimeoutError)
    assert web._pending_eval_callbacks == 0
