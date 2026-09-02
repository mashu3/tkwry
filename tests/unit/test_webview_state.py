"""WebView.get_state / WebViewState."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import PageLoadEvent, WebView, WebViewPhase, WebViewState


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_get_state_before_ready(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    state = web.get_state()
    assert isinstance(state, WebViewState)
    assert state.destroyed is False
    assert state.ready is False
    assert state.phase is WebViewPhase.PRE_CREATE
    assert state.url is None
    assert state.title is None
    assert state.loading is False
    assert state.can_go_back is False
    assert state.can_go_forward is False
    assert state.zoom == 1.0
    assert state.devtools_open is False
    assert web._state_wanted is True
    web.destroy()
    frame.destroy()


def test_get_state_after_ready_reads_nav_flags(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.can_go_back.return_value = True
    native.can_go_forward.return_value = False
    native.is_devtools_open.return_value = False
    native.drain_title_events.return_value = ["Hello"]
    native.drain_page_load_events.return_value = []
    native.url.return_value = "https://example.com/"
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    # Prefer property path used by get_state
    monkeypatch.setattr(
        type(web),
        "url",
        property(lambda self: "https://example.com/"),
    )

    state = web.get_state()
    assert state.ready is True
    assert state.url == "https://example.com/"
    assert state.title == "Hello"
    assert state.can_go_back is True
    assert state.can_go_forward is False
    native.set_title_listening.assert_called_with(True)
    native.set_page_load_listening.assert_called_with(True)
    web.destroy()
    frame.destroy()


def test_get_state_tracks_loading_and_zoom(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.can_go_back.return_value = False
    native.can_go_forward.return_value = False
    native.is_devtools_open.return_value = False
    native.drain_title_events.return_value = []
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Started, "https://example.com/"),
    ]
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(type(web), "url", property(lambda self: None))

    state = web.get_state()
    assert state.loading is True

    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Finished, "https://example.com/"),
    ]
    native.drain_title_events.return_value = []
    state = web.get_state()
    assert state.loading is False

    web._zoom = 1.25  # set_zoom would need ready native.zoom
    state = web.get_state()
    assert state.zoom == 1.25
    web.destroy()
    frame.destroy()


def test_get_state_readable_after_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._document_title = "Kept"
    web._zoom = 1.5
    web.destroy()
    state = web.get_state()
    assert state.destroyed is True
    assert state.phase is WebViewPhase.DESTROYED
    assert state.ready is False
    assert state.title == "Kept"
    assert state.zoom == 1.5
    assert state.can_go_back is False
    frame.destroy()


def test_set_zoom_updates_cached_zoom(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web.set_zoom(1.5)
    assert web._zoom == 1.5
    native.set_zoom.assert_called_once_with(1.5)
    web.reset_zoom()
    assert web._zoom == 1.0
    web.destroy()
    frame.destroy()
