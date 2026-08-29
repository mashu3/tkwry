"""Unit coverage for set_zoom / reset_zoom binder wiring."""

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


def test_set_zoom_delegates_to_native(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    web.set_zoom(1.25)
    native.set_zoom.assert_called_once_with(1.25)

    web.reset_zoom()
    assert native.set_zoom.call_args_list[-1].args == (1.0,)

    frame.destroy()


def test_set_zoom_rejects_non_finite(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import math
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame)
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)

    with pytest.raises(ValueError, match="finite"):
        web.set_zoom(math.nan)
    with pytest.raises(ValueError, match="finite"):
        web.set_zoom(math.inf)
    with pytest.raises(TypeError, match="finite"):
        web.set_zoom(True)  # type: ignore[arg-type]
    native.set_zoom.assert_not_called()

    frame.destroy()
