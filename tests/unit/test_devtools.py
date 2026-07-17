"""Unit coverage for DevTools binder wiring (no native inspector)."""

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


def test_devtools_methods_delegate_to_native(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    native.is_devtools_open.return_value = False
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    assert web.is_devtools_open() is False
    native.is_devtools_open.assert_called_once_with()

    web.open_devtools()
    native.open_devtools.assert_called_once_with()

    native.is_devtools_open.return_value = True
    assert web.is_devtools_open() is True

    web.close_devtools()
    native.close_devtools.assert_called_once_with()

    frame.destroy()
