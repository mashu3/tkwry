"""Tests for buffered page-load events before handler registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tkwry import PageLoadEvent, WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._core.pump_events", lambda: None, raising=False)
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


def test_set_on_page_load_none_disables_native_collection(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300, on_page_load=lambda *_a: None)
    native = MagicMock()
    web._webview = native

    web.set_on_page_load(None)

    native.set_page_load_listening.assert_called_with(False)


def test_poll_drains_page_load_without_handler(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com"),
    ]
    web._webview = native
    web._page_load_collecting = True

    web._poll_events()

    assert web._page_load_buffer == [(PageLoadEvent.Started, "https://example.com")]
    native.drain_page_load_events.assert_called_once_with()


def test_set_on_page_load_delivers_python_buffered_events(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Finished, "https://example.com"),
    ]
    web._webview = native
    web._page_load_buffer = [(PageLoadEvent.Started, "https://example.com")]
    events: list[tuple[PageLoadEvent, str]] = []

    web.set_on_page_load(lambda evt, url: events.append((evt, url)))

    assert events == [
        (PageLoadEvent.Started, "https://example.com"),
        (PageLoadEvent.Finished, "https://example.com"),
    ]
    assert web._page_load_buffer == []
