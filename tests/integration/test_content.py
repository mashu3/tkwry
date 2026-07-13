"""Content loading, callbacks, and drag-and-drop queueing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from support.tk import host_frame, pump, wait_until

from tkwry import DragDropEvent, PageLoadEvent, WebView
from tkwry.exceptions import WebViewDestroyedError


def test_create_with_html_and_destroy(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p id='t'>ok</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    assert web.native is not None

    web.destroy()
    with pytest.raises(WebViewDestroyedError):
        _ = web.native

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

    assert web.url == "<html>"
    assert web._pending_html == "<p>html wins</p>"
    assert web._pending_url is None

    web.destroy()
    frame.destroy()


def test_initial_load_runs_after_bounds_sync(tk_root) -> None:
    """Deferred initial content load completes after bounds sync (no network)."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>deferred</title><p>sync</p>")

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    pump(tk_root, steps=80)
    assert web._initial_load is None, (
        f"initial_load still pending: {web._initial_load!r}"
    )

    web.destroy()
    frame.destroy()


def test_load_url_coalesces_before_create(tk_root) -> None:
    """Rapid load_url calls before native create keep only the last URL."""
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("https://example.com/a")
    web.load_url("https://example.com/b")
    web.load_url("https://example.com/c")

    assert web._pending_url == "https://example.com/c"
    assert web._pending_load is None

    web.destroy()
    frame.destroy()


def test_load_coalesces_to_last_pending(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>init</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_url("https://example.com/a")
    web.load_url("https://example.com/b")
    web.load_url("https://example.com/c")

    assert web._initial_load is None
    # Linux may flush pending loads synchronously; Win/macOS keep the coalesced entry.
    assert web._pending_load in (None, ("url", "https://example.com/c"))
    if web._pending_load is not None:
        assert web._pending_load == ("url", "https://example.com/c")

    web.destroy()
    frame.destroy()


def test_load_after_create_cancels_deferred_initial_load(tk_root) -> None:
    """Post-create load_* must win over the delayed constructor reload."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>A</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web._initial_load = ("html", "<p>A</p>")  # re-arm as if delay not yet fired
    web.load_url("https://example.com/B")

    assert web._initial_load is None
    # Linux may flush pending loads synchronously; do not rely on native url().
    assert web._pending_load in (None, ("url", "https://example.com/B"))
    if web._pending_load is not None:
        assert web._pending_load == ("url", "https://example.com/B")
    web._run_initial_load()  # delayed callback must be a no-op
    assert web._initial_load is None
    assert web._pending_load != ("html", "<p>A</p>")

    web.destroy()
    frame.destroy()


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

    assert wait_until(tk_root, finished, steps=400), (
        f"expected PageLoadEvent.Finished, got {events!r}"
    )

    web.destroy()
    frame.destroy()


def test_reload_after_ready_fires_page_load(tk_root, tmp_path: Path) -> None:
    page = tmp_path / "reload.html"
    page.write_text(
        "<title>reload-test</title><p id='t'>v1</p>",
        encoding="utf-8",
    )

    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )
    web.load_url(str(page))

    assert wait_until(tk_root, lambda: web.native is not None)

    def initial_finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    assert wait_until(tk_root, initial_finished, steps=400), (
        f"expected initial PageLoadEvent.Finished, got {events!r}"
    )
    finished_before = sum(1 for evt, _ in events if evt == PageLoadEvent.Finished)
    started_before = sum(1 for evt, _ in events if evt == PageLoadEvent.Started)

    web.reload()
    pump(tk_root, steps=50)

    def reload_finished() -> bool:
        finished = sum(1 for evt, _ in events if evt == PageLoadEvent.Finished)
        started = sum(1 for evt, _ in events if evt == PageLoadEvent.Started)
        return finished >= finished_before + 1 and started >= started_before + 1

    assert wait_until(tk_root, reload_finished, steps=400), (
        f"expected Started+Finished after reload(), got {events!r}"
    )

    text: list[str] = []
    web.eval_js_with_callback("document.getElementById('t').textContent", text.append)
    assert wait_until(tk_root, lambda: text, steps=200), (
        f"expected reloaded document content, got {text!r}"
    )
    assert json.loads(text[0]) == "v1"

    web.destroy()
    frame.destroy()


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
    reason="WebKitGTK: IPC e2e hangs after other WebViews in the same pytest process",
)
def test_ipc_post_message_reaches_handler(tk_root) -> None:
    """End-to-end: JS window.ipc.postMessage -> Tk-thread handler."""
    received: list[str] = []
    loaded: list[PageLoadEvent] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="<p>ipc-e2e</p>",
        ipc_handler=lambda msg: received.append(msg),
        on_page_load=lambda evt, _url: loaded.append(evt),
    )
    assert web.wait_until_ready(timeout=10.0)
    assert wait_until(
        tk_root,
        lambda: PageLoadEvent.Finished in loaded,
        steps=400,
    ), f"expected page load Finished before IPC, got {loaded!r}"

    # WebView2 may need a few retries before window.ipc is callable.
    for _ in range(10):
        web.eval_js("window.ipc && window.ipc.postMessage('hello-from-js')")
        if wait_until(tk_root, lambda: "hello-from-js" in received, steps=40):
            break
    assert "hello-from-js" in received, f"expected JS IPC message, got {received!r}"

    web.destroy()
    frame.destroy()


def test_title_changed_delivers_on_document_title_set(tk_root) -> None:
    titles: list[str] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="<title>initial</title><p>title</p>",
        on_title_changed=lambda title: titles.append(title),
    )

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    pump(tk_root, steps=50)
    titles.clear()

    web.eval_js("document.title = 'tkwry-title-test'")
    assert wait_until(
        tk_root,
        lambda: "tkwry-title-test" in titles,
        steps=200,
    ), f"expected title callback, got {titles!r}"

    titles.clear()
    web.set_on_title_changed(lambda title: titles.append(title))
    web.eval_js("document.title = 'tkwry-title-after-set'")
    assert wait_until(
        tk_root,
        lambda: "tkwry-title-after-set" in titles,
        steps=200,
    ), f"expected title after set_on_title_changed, got {titles!r}"

    web.destroy()
    frame.destroy()


def test_drag_drop_native_queues_without_blocking(tk_root) -> None:
    """Queue Enter/Drop on the Tk thread (same queue OS drops use).

    Full Finder/Explorer drops cannot be synthesized reliably in CI; this
    covers enqueue -> poll -> Python handler on that path.
    """
    received: list[tuple] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>dnd</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(evt, paths, pos) -> None:
        received.append((evt, paths, pos))

    web.set_drag_drop_handler(handler)

    web._native_drag_drop(DragDropEvent.Enter, ["/tmp/a.txt"], (1, 2))
    web._native_drag_drop(DragDropEvent.Over, [], (3, 4))
    web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a.txt"], (5, 6))

    pump(tk_root, steps=30)
    assert wait_until(tk_root, lambda: len(received) >= 2, steps=100), (
        f"expected queued drag events, got {received!r}"
    )

    web.destroy()
    frame.destroy()


def test_load_local_html_resolves_relative_resources(tk_root, tmp_path: Path) -> None:
    (tmp_path / "style.css").write_text(
        "p { color: rgb(255, 0, 0); }", encoding="utf-8"
    )
    (tmp_path / "index.html").write_text(
        (
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="style.css">'
            "</head><body><p id='t'>local</p></body></html>"
        ),
        encoding="utf-8",
    )

    events: list[tuple[PageLoadEvent, str]] = []
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )
    web.load_url(str(tmp_path / "index.html"))

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    assert wait_until(
        tk_root,
        lambda: any(evt == PageLoadEvent.Finished for evt, _ in events),
        steps=400,
    ), f"expected page load, got {events!r}"
    pump(tk_root, steps=50)

    colors: list[str] = []

    def on_color(value: str) -> None:
        colors.append(value)

    script = "getComputedStyle(document.getElementById('t')).color"
    web.eval_js_with_callback(script, on_color)
    assert wait_until(tk_root, lambda: colors, steps=100), "expected computed color"
    assert "255" in colors[0] or "rgb(255" in colors[0].replace(" ", "")

    web.destroy()
    frame.destroy()
