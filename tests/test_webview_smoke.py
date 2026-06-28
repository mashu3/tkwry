"""Integration tests that create a real embedded WebView."""

from __future__ import annotations

import sys

import pytest
from helpers import pump, wait_until

from tkwry import PageLoadEvent, WebView

pytestmark = pytest.mark.integration


def _host_frame(root):
    import tkinter as tk

    root.update_idletasks()
    frame = tk.Frame(root, width=400, height=280, bg="#222")
    frame.pack_propagate(False)
    frame.pack(fill="both", expand=True)
    root.update_idletasks()
    return frame


def test_create_with_html_and_destroy(tk_root) -> None:
    frame = _host_frame(tk_root)
    web = WebView(frame, html="<p id='t'>ok</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    assert web.native is not None

    web.destroy()
    assert web.native is None

    frame.destroy()


def test_load_url_before_create_normalizes_pending(tk_root) -> None:
    frame = _host_frame(tk_root)
    web = WebView(frame)
    web.load_url("example.com")

    assert web.url == "https://example.com"
    web.destroy()
    frame.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI does not reliably deliver page-load callbacks",
)
def test_page_load_callback_receives_finished(tk_root) -> None:
    events: list[tuple[PageLoadEvent, str]] = []

    frame = _host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_html("<title>smoke</title><p>load</p>")

    def finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    if not wait_until(tk_root, finished, steps=400):
        web.reload()
        pump(tk_root, steps=50)
        assert wait_until(tk_root, finished, steps=200), (
            f"expected PageLoadEvent.Finished, got {events!r}"
        )

    web.destroy()
    frame.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI: drag-drop event poll unreliable",
)
def test_drag_drop_native_queues_without_blocking(tk_root) -> None:
    """Queue Enter/Drop on the Tk thread without registering OS drag with wry."""
    received: list[tuple] = []

    frame = _host_frame(tk_root)
    web = WebView(frame, html="<p>dnd</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(evt, paths, pos):
        received.append((evt, paths, pos))
        return True

    # Simulate Rust calling _native_drag_drop — avoid wry OS drag (hangs headless GTK).
    web._drag_drop_handler = handler
    web._ensure_event_poll()

    from tkwry import DragDropEvent

    assert web._native_drag_drop(DragDropEvent.Enter, ["/tmp/a.txt"], (1, 2)) is True
    assert web._native_drag_drop(DragDropEvent.Over, [], (3, 4)) is True
    assert web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a.txt"], (5, 6)) is True

    pump(tk_root, steps=30)
    assert wait_until(tk_root, lambda: len(received) >= 2, steps=100), (
        f"expected queued drag events, got {received!r}"
    )

    web.destroy()
    frame.destroy()
