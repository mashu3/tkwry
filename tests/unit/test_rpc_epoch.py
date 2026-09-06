"""RPC navigation epoch — stale settle rejection."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView
from tkwry._core import PageLoadEvent
from tkwry.ipc import rpc_bootstrap_js


def test_page_load_started_bumps_rpc_epoch(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/next")
    ]
    eval_scripts: list[str] = []
    native.eval_js = lambda script: eval_scripts.append(script)
    web._webview = native  # type: ignore[assignment]
    web._document_loaded_once = True
    web._rpc_page_started_once = True

    web._deliver_page_load_events()

    assert web._rpc_epoch == 1

    web.destroy()
    frame.destroy()


def test_create_time_first_started_does_not_bump_rpc_epoch(tk_root) -> None:
    """First Started must not cancel same-poll IPC already drained as RPC."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/")
    ]
    web._webview = native  # type: ignore[assignment]
    assert web._rpc_page_started_once is False
    assert web._document_loaded_once is False

    web._deliver_page_load_events()

    assert web._rpc_epoch == 0
    assert web._rpc_page_started_once is True

    web.destroy()
    frame.destroy()


def test_page_load_started_bumps_rpc_epoch_before_first_finished(tk_root) -> None:
    """Early re-navigation must bump epoch even when Finished never ran."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/first"),
        (PageLoadEvent.Started, "https://example.com/next"),
    ]
    web._webview = native  # type: ignore[assignment]
    assert web._document_loaded_once is False

    web._deliver_page_load_events()

    assert web._rpc_page_started_once is True
    assert web._rpc_epoch == 1

    web.destroy()
    frame.destroy()


def test_rpc_bootstrap_reinjected_on_page_load_finished(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    injected: list[str] = []

    class _Native:
        def drain_page_load_events(self):
            return [
                (PageLoadEvent.Started, "https://example.com/next"),
                (PageLoadEvent.Finished, "https://example.com/next"),
            ]

        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            injected.append(script)

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web._webview = _Native()  # type: ignore[assignment]
    web._rpc_page_started_once = True
    web._rpc_bootstrap_injected = True
    injected.clear()

    web._deliver_page_load_events()

    assert web._rpc_epoch == 1
    assert any(script == rpc_bootstrap_js(1) for script in injected)

    web.destroy()
    frame.destroy()


def test_emit_bridge_keeps_event_poll_for_page_load(tk_root) -> None:
    """emit()-only must keep the poll so Started can reinject the bridge."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>emit</p>")
    assert web._needs_event_poll() is False

    web._rpc_bridge_wanted = True

    assert web._ipc_listening_wanted() is False
    assert web._page_load_listening_wanted() is True
    assert web._needs_event_poll() is True

    web.destroy()
    frame.destroy()


def test_emit_only_bootstrap_does_not_force_ipc_listening(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap inject must not enable IPC listen when nothing will drain it."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>emit</p>")
    ipc_flags: list[bool] = []

    class _Native:
        def set_ipc_listening(self, enabled: bool) -> None:
            ipc_flags.append(enabled)

        def eval_js(self, script: str) -> None:
            pass

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web._webview = _Native()  # type: ignore[assignment]
    web._rpc_bridge_wanted = True
    assert web._ipc_listening_wanted() is False

    web._inject_rpc_bootstrap()

    assert ipc_flags == [False]
    assert web._rpc_bootstrap_injected is True

    web.destroy()
    frame.destroy()


def test_emit_wakeup_schedules_bridge_sync_on_started(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wakeup after emit must schedule bridge sync for Started (not hang poll)."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>emit</p>")
    scheduled: list[object] = []

    class _Native:
        def drain_page_load_events(self):
            return [(PageLoadEvent.Started, "https://example.com/next")]

        def drain_download_complete_events(self):
            return []

        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            pass

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        web._frame,
        "after_idle",
        lambda fn: scheduled.append(fn) or "after-idle",
        raising=False,
    )
    web._webview = _Native()  # type: ignore[assignment]
    web._rpc_bridge_wanted = True
    web._rpc_page_started_once = True
    web._rpc_bootstrap_injected = True

    web._wake_async_events()

    assert web._rpc_epoch == 1
    assert scheduled
    injected: list[str] = []
    web._webview.eval_js = lambda script: injected.append(script)  # type: ignore[method-assign]
    scheduled[0]()
    assert injected == [rpc_bootstrap_js(1)]

    web.destroy()
    frame.destroy()


def test_first_started_does_not_cancel_inflight_rpc(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    from concurrent.futures import Future

    fut: Future[object] = Future()
    web._rpc_inflight["0:r1"] = fut
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/")
    ]
    web._webview = native  # type: ignore[assignment]

    web._deliver_page_load_events()

    assert "0:r1" in web._rpc_inflight
    assert not fut.cancelled()

    web.destroy()
    frame.destroy()


def test_rpc_bootstrap_js_bakes_epoch() -> None:
    assert "var initialEpoch = 0;" in rpc_bootstrap_js(0)
    assert "var initialEpoch = 3;" in rpc_bootstrap_js(3)
    assert "_bumpEpoch(initialEpoch)" in rpc_bootstrap_js(1)


def test_stale_rpc_settle_dropped_after_epoch_bump(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    native = MagicMock()
    web._webview = native  # type: ignore[assignment]
    web._rpc_epoch = 2

    web._settle_rpc("0:r1", ok=True, value={"ok": True})
    web._settle_rpc("2:r1", ok=True, value={"ok": True})

    native.eval_js.assert_called_once()
    assert "2:r1" in native.eval_js.call_args.args[0]
    assert "0:r1" not in native.eval_js.call_args.args[0]

    web.destroy()
    frame.destroy()
