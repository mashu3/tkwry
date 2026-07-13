"""Tests for queue overflow drop reporting."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _noop_linux_gtk_pump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr("tkwry._linux.GtkPump.detach", lambda _widget: None)
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events", lambda **_kwargs: False, raising=False
    )


def test_take_queue_drop_counts_before_native_returns_zeros(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0)

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_delegates_to_native(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.take_queue_drop_counts.return_value = (1, 2, 3, 4, 5)
    web._webview = native

    assert web.take_queue_drop_counts() == (1, 2, 3, 4, 5)
    native.take_queue_drop_counts.assert_called_once_with()

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_after_destroy_returns_zeros(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.destroy()

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0)

    frame.destroy()


def test_take_queue_drop_counts_reports_local_eval_drops_on_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._register_pending_eval(lambda _r: None, None)
    web._register_pending_eval(lambda _r: None, None)

    web.destroy()

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 2)

    frame.destroy()
