"""Unit tests for WebView.expose registration rules."""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView, rpc_cancelled


def test_expose_rejects_duplicate_names(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def greet() -> str:
        return "hi"

    with pytest.raises(ValueError, match="already exposed"):

        @web.expose
        def greet() -> str:  # noqa: F811
            return "bye"

    @web.expose(replace=True)
    def greet() -> str:  # noqa: F811
        return "ok"

    assert web.unexpose("greet") is True
    assert web.unexpose("greet") is False

    web.destroy()
    frame.destroy()


def test_expose_thread_conflicts_with_main(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    with pytest.raises(ValueError, match="conflicts"):

        @web.expose(thread=True, run_in="main")
        def bad() -> None:
            return None

    web.destroy()
    frame.destroy()


def test_rpc_delivered_from_dedicated_queue(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    sums: list[int] = []
    ipc_seen: list[str] = []

    @web.expose
    def add(a: int, b: int) -> int:
        total = int(a) + int(b)
        sums.append(total)
        return total

    web.set_ipc_handler(ipc_seen.append)
    native = MagicMock()
    native.drain_rpc_messages.return_value = [
        json.dumps({"__tkwry": "rpc", "id": "r1", "method": "add", "params": [2, 3]})
    ]
    native.drain_ipc_messages.return_value = ["flood"]
    web._webview = native
    web._deliver_ipc_messages()

    assert sums == [5]
    assert ipc_seen == ["flood"]
    native.eval_js.assert_called_once()

    web.destroy()
    frame.destroy()


def test_rpc_timeout_sets_cancel_flag(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True, timeout=0.15)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                return "cancelled"
            time.sleep(0.02)
        return "done"

    web._cancel_deferred_callbacks()
    native = MagicMock()
    native.drain_rpc_messages.return_value = [
        json.dumps({"__tkwry": "rpc", "id": "r1", "method": "slow", "params": []})
    ]
    native.drain_ipc_messages.return_value = []
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not saw_cancel.is_set():
        tk_root.update()
        time.sleep(0.02)
    assert saw_cancel.is_set()

    web.destroy()
    frame.destroy()


def test_rpc_cancel_envelope_sets_flag_and_rejects(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                return "cancelled"
            time.sleep(0.02)
        return "done"

    web._cancel_deferred_callbacks()
    native = MagicMock()
    native.drain_rpc_messages.return_value = [
        json.dumps({"__tkwry": "rpc", "id": "r9", "method": "slow", "params": []})
    ]
    native.drain_ipc_messages.return_value = []
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)

    native.drain_rpc_messages.return_value = [
        json.dumps({"__tkwry": "rpc", "id": "r9", "cancel": True})
    ]
    web._deliver_ipc_messages()
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not saw_cancel.is_set():
        tk_root.update()
        time.sleep(0.02)
    assert saw_cancel.is_set()

    web.destroy()
    frame.destroy()


def test_emit_rejects_non_json(tk_root) -> None:
    from tkwry import RpcSerializationError

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    with pytest.raises(RpcSerializationError):
        web.emit("bad", object())

    web.destroy()
    frame.destroy()
