"""Unit coverage for print() / print_with_options binder wiring (no dialog)."""

from __future__ import annotations

import sys
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


def test_print_with_options_delegates_to_native(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    web.print_with_options(top=1.0, right=2.0, bottom=3.0, left=4.0)
    native.print_with_options.assert_called_once_with(
        top=1.0, right=2.0, bottom=3.0, left=4.0
    )

    frame.destroy()


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS OSError path")
def test_print_with_options_raises_off_macos_when_native_rejects(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python forwards OSError from native on Win/Linux (no fake no-op)."""
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    native.print_with_options.side_effect = OSError("only available on macOS")
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    with pytest.raises(OSError, match="macOS"):
        web.print_with_options(top=1.0)

    frame.destroy()


def test_no_print_to_pdf_api() -> None:
    """F21: do not ship a fake print_to_pdf while wry lacks PDF export."""
    assert not hasattr(WebView, "print_to_pdf")
