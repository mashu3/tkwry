"""Content loading, callbacks, and drag-and-drop queueing."""

from __future__ import annotations

import sys

import pytest
from support.tk import host_frame, pump, wait_until

from tkwry import DragDropEvent, PageLoadEvent, WebView


def test_create_with_html_and_destroy(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p id='t'>ok</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    assert web.native is not None

    web.destroy()
    assert web.native is None

    frame.destroy()


def test_load_url_before_create_normalizes_pending(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("example.com")

    assert web.url == "https://example.com"
    web.destroy()
    frame.destroy()


def test_load_html_supersedes_pending_url_before_create(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("example.com")
    web.load_html("<p>html wins</p>")

    assert web.url is None
    assert web._pending_html == "<p>html wins</p>"
    assert web._pending_url is None

    web.destroy()
    frame.destroy()


def test_initial_url_loads_after_bounds_sync(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, url="https://example.com")

    assert wait_until(tk_root, lambda: web.ready, steps=200)

    def url_loaded() -> bool:
        if not web.ready:
            return False
        current = web.url
        return current is not None and current.startswith("https://example.com")

    assert wait_until(tk_root, url_loaded, steps=400), f"url={web.url!r}"

    web.destroy()
    frame.destroy()


def test_load_coalesces_to_last_pending(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>init</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_url("https://example.com/a")
    web.load_url("https://example.com/b")
    web.load_url("https://example.com/c")

    assert web._pending_load == ("url", "https://example.com/c")

    web.destroy()
    frame.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI does not reliably deliver page-load callbacks",
)
def test_page_load_callback_receives_finished(tk_root) -> None:
    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
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
    reason="WebKitGTK headless CI does not reliably deliver page-load callbacks",
)
def test_page_load_discards_backlog_before_handler_attach(tk_root) -> None:
    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>first</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_html("<p>before handler</p>")
    pump(tk_root, steps=80)

    web.set_on_page_load(lambda evt, url: events.append((evt, url)))
    events.clear()
    web.load_html("<p>after handler</p>")

    def finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    assert wait_until(tk_root, finished, steps=400), (
        f"expected Finished after handler attach, got {events!r}"
    )
    assert not any("before handler" in url for _, url in events)

    web.destroy()
    frame.destroy()


def test_ipc_handler_exception_does_not_stop_poll(tk_root) -> None:
    received: list[str] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>ipc</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(msg: str) -> None:
        if msg == "bad":
            raise ValueError("boom")
        received.append(msg)

    web.set_ipc_handler(handler)
    web._enqueue_ipc("bad")
    web._enqueue_ipc("ok")
    pump(tk_root, steps=50)

    assert wait_until(tk_root, lambda: received == ["ok"], steps=100)

    web.destroy()
    frame.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI: drag-drop event poll unreliable",
)
def test_drag_drop_native_queues_without_blocking(tk_root) -> None:
    """Queue Enter/Drop on the Tk thread without registering OS drag with wry."""
    received: list[tuple] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>dnd</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(evt, paths, pos):
        received.append((evt, paths, pos))
        return True

    web._drag_drop_handler = handler
    web._ensure_event_poll()

    assert web._native_drag_drop(DragDropEvent.Enter, ["/tmp/a.txt"], (1, 2)) is True
    assert web._native_drag_drop(DragDropEvent.Over, [], (3, 4)) is True
    assert web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a.txt"], (5, 6)) is True

    pump(tk_root, steps=30)
    assert wait_until(tk_root, lambda: len(received) >= 2, steps=100), (
        f"expected queued drag events, got {received!r}"
    )

    web.destroy()
    frame.destroy()
