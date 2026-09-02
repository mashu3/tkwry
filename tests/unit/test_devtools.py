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


def test_devtools_public_surface() -> None:
    """Unified DevTools API is exactly these three methods + create flag."""
    for name in ("open_devtools", "close_devtools", "is_devtools_open"):
        assert callable(getattr(WebView, name))
    assert "devtools" in WebView.__init__.__code__.co_varnames


def test_devtools_raises_after_destroy(tk_root) -> None:
    import tkinter as tk

    from tkwry import WebViewDestroyedError

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web.destroy()
    with pytest.raises(WebViewDestroyedError, match="open_devtools"):
        web.open_devtools()
    with pytest.raises(WebViewDestroyedError, match="close_devtools"):
        web.close_devtools()
    with pytest.raises(WebViewDestroyedError, match="is_devtools_open"):
        web.is_devtools_open()
    frame.destroy()
