"""Unit coverage for create-time back_forward_gestures=."""

from __future__ import annotations

import sys
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_back_forward_gestures_default_false(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>g</p>")
    assert web.back_forward_gestures is False
    web.destroy()
    assert web.back_forward_gestures is False
    frame.destroy()


def test_back_forward_gestures_true_readable_after_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>g</p>", back_forward_gestures=True)
    assert web.back_forward_gestures is True
    web.destroy()
    assert web.back_forward_gestures is True
    frame.destroy()


def test_try_create_passes_back_forward_gestures(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)

    web = WebView(
        frame, html="<p>g</p>", width=200, height=150, back_forward_gestures=True
    )
    captured: dict[str, object] = {}

    def fake_native(*_args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        native = MagicMock()
        native.is_alive.return_value = False
        return native

    monkeypatch.setattr("tkwry.webview.NativeWebView", fake_native)
    monkeypatch.setattr(web, "_sync_bounds", lambda: True)
    monkeypatch.setattr(web, "_maybe_fire_ready", lambda: None)
    monkeypatch.setattr(web, "_schedule_initial_load", lambda: None)
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    monkeypatch.setattr(web, "_needs_event_poll", lambda: False)
    if sys.platform == "darwin":
        monkeypatch.setattr("tkwry.webview._ensure_mac_wakeup_pipe", lambda *_a: None)
        monkeypatch.setattr("tkwry.webview._ensure_mac_pump", lambda *_a: None)

    web._try_create()
    assert captured.get("back_forward_gestures") is True
    web.destroy()
    frame.destroy()
