"""RPC navigation epoch — stale settle rejection."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

from tkwry import WebView
from tkwry._core import PageLoadEvent


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

    web._deliver_page_load_events()

    assert web._rpc_epoch == 1
    assert any("_bumpEpoch(1)" in script for script in eval_scripts)

    web.destroy()
    frame.destroy()


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
