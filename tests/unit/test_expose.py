"""Unit tests for WebView.expose registration rules."""

from __future__ import annotations

import json
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView


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
