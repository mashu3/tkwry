"""Unit coverage for print() binder wiring (no system dialog)."""

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


def test_print_delegates_to_native(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    web.print()
    native.print.assert_called_once_with()

    frame.destroy()
