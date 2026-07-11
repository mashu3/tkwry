"""Tests for buffered page-load events before handler registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tkwry import PageLoadEvent, WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._runtime.GtkPump.attach", lambda _widget: None)


def test_set_on_page_load_delivers_buffered_events(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "about:blank"),
        (PageLoadEvent.Finished, "about:blank"),
    ]
    web._webview = native
    events: list[tuple[PageLoadEvent, str]] = []

    web.set_on_page_load(lambda evt, url: events.append((evt, url)))

    native.set_page_load_listening.assert_called_once_with(True)
    assert events == [
        (PageLoadEvent.Started, "about:blank"),
        (PageLoadEvent.Finished, "about:blank"),
    ]
