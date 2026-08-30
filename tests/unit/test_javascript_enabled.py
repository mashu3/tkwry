"""Unit coverage for create-time javascript_enabled=."""

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


def test_javascript_enabled_default_true(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>j</p>")
    assert web.javascript_enabled is True
    web.destroy()
    assert web.javascript_enabled is True
    frame.destroy()


def test_javascript_enabled_false_readable_after_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>j</p>", javascript_enabled=False)
    assert web.javascript_enabled is False
    web.destroy()
    assert web.javascript_enabled is False
    frame.destroy()


def test_try_create_passes_javascript_enabled(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)

    web = WebView(
        frame, html="<p>j</p>", width=200, height=150, javascript_enabled=False
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
    assert captured.get("javascript_enabled") is False
    web.destroy()
    frame.destroy()
