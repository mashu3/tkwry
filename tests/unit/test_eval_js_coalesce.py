"""Tests for eval_js last-wins coalescing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_eval_js_coalesces_rapid_calls(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    queued: list[object] = []
    monkeypatch.setattr(
        frame,
        "after_idle",
        lambda fn: queued.append(fn) or "after#eval",
        raising=False,
    )

    web.eval_js("first")
    web.eval_js("second")
    web.eval_js("third")

    assert len(queued) == 1
    queued[0]()
    native.eval_js.assert_called_once_with("third")

    web.destroy()
