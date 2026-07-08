"""Ready events, initial size, and destroy safety."""

from __future__ import annotations

import sys
import threading

import pytest
from support.tk import bare_frame, layout_bare_frame, pump, skip_linux_ci, wait_until

from tkwry import WebView, WebViewDestroyedError, WebViewNotReadyError

pytestmark = skip_linux_ci


def test_initial_size_creates_without_pack(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=640, height=480, html="<p>size</p>")

    pump(tk_root, steps=5)
    assert web.native is not None
    assert not web.ready
    assert not web.wait_until_ready(timeout=0.05)

    layout_bare_frame(frame, width=640, height=480)
    assert web.wait_until_ready(timeout=10.0)
    assert web.ready

    web.destroy()
    frame.destroy()


def test_explicit_default_size_creates_without_pack(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=800, height=600, html="<p>default-size</p>")

    pump(tk_root, steps=5)
    assert web.native is not None
    assert not web.ready

    layout_bare_frame(frame, width=800, height=600)
    assert web.wait_until_ready(timeout=10.0)
    assert web.ready

    web.destroy()
    frame.destroy()


def test_eval_js_raises_before_ready(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)

    with pytest.raises(WebViewNotReadyError, match="eval_js"):
        web.eval_js("1+1")

    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)
    web.destroy()
    frame.destroy()


def test_reload_raises_before_ready(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)

    with pytest.raises(WebViewNotReadyError, match="reload"):
        web.reload()

    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)
    web.destroy()
    frame.destroy()


def test_load_url_pending_before_ready_still_works(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.load_url("example.com")

    assert web.url == "https://example.com"
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    web.destroy()
    frame.destroy()


def test_webview_ready_virtual_event(tk_root) -> None:
    frame = bare_frame(tk_root)
    fired: list[bool] = []
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")
    web.bind("<<WebViewReady>>", lambda _evt: fired.append(True))
    layout_bare_frame(frame, width=400, height=300)

    assert wait_until(tk_root, lambda: fired, steps=200)
    assert web.ready
    assert web.native is not None
    assert web.native.url() is None

    web.destroy()
    frame.destroy()


def test_when_ready_callback(tk_root) -> None:
    frame = bare_frame(tk_root)
    fired: list[bool] = []
    web = WebView(frame, width=400, height=300, html="<p>when</p>")
    web.when_ready(lambda: fired.append(True))
    layout_bare_frame(frame, width=400, height=300)

    assert wait_until(tk_root, lambda: fired, steps=200)
    assert web.ready

    web.destroy()
    frame.destroy()


def test_when_ready_fires_immediately_if_already_ready(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    web = WebView(frame, html="<p>immediate</p>")
    assert web.wait_until_ready(timeout=10.0)

    fired: list[bool] = []
    web.when_ready(lambda: fired.append(True))
    pump(tk_root, steps=5)
    assert fired

    web.destroy()
    frame.destroy()


def test_ready_bind_after_ready_passes_event(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    web = WebView(frame, html="<p>bind-after-ready</p>")
    assert web.wait_until_ready(timeout=10.0)

    events: list[tk.Event] = []
    web.bind("<<WebViewReady>>", lambda evt: events.append(evt))
    pump(tk_root, steps=5)
    assert len(events) == 1
    assert events[0].widget is web._frame

    web.destroy()
    frame.destroy()


def test_set_on_new_window_none_clears_handler(tk_root) -> None:
    from tkwry import NewWindowResponse

    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>newwin</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    calls: list[str] = []

    def handler(url: str) -> NewWindowResponse:
        calls.append(url)
        return NewWindowResponse.Deny

    web.set_on_new_window(handler)
    assert web._native_new_window("https://example.com/") == NewWindowResponse.Deny
    assert calls == ["https://example.com/"]

    web.set_on_new_window(None)
    assert web._on_new_window is None
    assert web._native_new_window("https://example.com/2") == NewWindowResponse.Allow
    assert calls == ["https://example.com/"]

    web.destroy()
    frame.destroy()


def test_set_on_navigation_none_clears_handler(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>nav</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    calls: list[str] = []

    def handler(url: str) -> bool:
        calls.append(url)
        return False

    web.set_on_navigation(handler)
    assert web._native_navigation("https://example.com/") is False
    assert calls == ["https://example.com/"]

    web.set_on_navigation(None)
    assert web._on_navigation is None
    assert web._native_navigation("https://example.com/2") is True
    assert calls == ["https://example.com/"]

    web.destroy()
    frame.destroy()


def test_set_on_title_changed_none_clears_handler(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>title</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    received: list[str] = []

    def handler(title: str) -> None:
        received.append(title)

    web.set_on_title_changed(handler)
    web._native_title_changed("First")
    pump(tk_root, steps=10)
    assert received == ["First"]

    web.set_on_title_changed(None)
    web._native_title_changed("Second")
    pump(tk_root, steps=10)
    assert received == ["First"]
    assert web.native is not None
    assert web.native.drain_title_events() == []

    web.destroy()
    frame.destroy()


def test_set_drag_drop_handler_none_clears_handler(tk_root) -> None:
    from tkwry import DragDropEvent

    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>dnd</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    received: list[tuple] = []

    def handler(evt, paths, pos):
        received.append((evt, paths, pos))
        return True

    web.set_drag_drop_handler(handler)
    assert web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a.txt"], (1, 2)) is True
    pump(tk_root, steps=10)
    assert len(received) == 1

    web.set_drag_drop_handler(None)
    assert web._native_drag_drop(DragDropEvent.Drop, ["/tmp/b.txt"], (3, 4)) is True
    pump(tk_root, steps=10)
    assert len(received) == 1
    assert web.native is not None
    assert web.native.drain_drag_drop_events() == []

    web.destroy()
    frame.destroy()


def test_set_ipc_handler_none_stops_collecting(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>ipc</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    received: list[str] = []
    web.set_ipc_handler(lambda msg: received.append(msg))
    web._enqueue_ipc("one")
    pump(tk_root, steps=10)
    assert received == ["one"]

    web.set_ipc_handler(None)
    web._enqueue_ipc("two")
    pump(tk_root, steps=10)
    assert received == ["one"]
    assert web.native is not None
    assert web.native.drain_ipc_messages() == []

    web.destroy()
    frame.destroy()


def test_set_on_page_load_none_stops_collecting(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>load</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    web.set_on_page_load(lambda evt, _url: None)
    assert web.native is not None
    web.set_on_page_load(None)
    pump(tk_root, steps=5)
    assert web.native.drain_page_load_events() == []

    web.destroy()
    frame.destroy()


def test_drain_async_events_does_not_invoke_native_callbacks(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>drain</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    native = web.native
    assert native is not None
    native.set_ipc_listening(True)
    native.set_title_listening(True)

    web._enqueue_ipc("ipc")
    web._native_title_changed("t")
    assert native.drain_ipc_messages() == ["ipc"]
    assert native.drain_title_events() == ["t"]
    # drain_* is queue-only; a second drain must be empty (no hidden re-delivery).
    assert native.drain_ipc_messages() == []
    assert native.drain_title_events() == []

    web.destroy()
    frame.destroy()


def test_double_destroy_is_safe(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>destroy</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    web.destroy()
    web.destroy()
    assert web.destroyed
    assert web.native is None

    frame.destroy()


def test_native_rejects_other_thread(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>x</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    native = web.native
    assert native is not None
    errors: list[str] = []

    def worker() -> None:
        try:
            native.eval_js("1+1")
        except BaseException as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert "thread" in errors[0].lower()

    web.destroy()
    frame.destroy()


def test_navigation_callback_can_clear_handler(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>nav-reentrant</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    def handler(url: str) -> bool:
        web.set_on_navigation(None)
        return True

    web.set_on_navigation(handler)
    assert web._native_navigation("https://example.com/") is True
    assert web._on_navigation is None

    web.destroy()
    frame.destroy()


def test_navigation_callback_can_destroy_without_deadlock(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>nav-destroy</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    def handler(url: str) -> bool:
        web.destroy()
        return False

    web.set_on_navigation(handler)
    assert web._native_navigation("https://example.com/") is False
    assert web.destroyed

    frame.destroy()


def test_eval_js_raises_after_destroy(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>x</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)

    native = web.native
    web.destroy()

    with pytest.raises(WebViewDestroyedError, match="eval_js"):
        web.eval_js("1+1")

    if native is not None:
        with pytest.raises(RuntimeError, match="already destroyed"):
            native.eval_js("1+1")

    frame.destroy()


def test_url_raises_after_destroy(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>x</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)
    web.destroy()

    with pytest.raises(WebViewDestroyedError):
        _ = web.url

    frame.destroy()


def test_ready_event_does_not_fire_after_destroy(tk_root) -> None:
    frame = bare_frame(tk_root)
    fired: list[bool] = []
    web = WebView(frame, width=400, height=300)
    web.bind("<<WebViewReady>>", lambda _evt: fired.append(True))
    web.destroy()

    pump(tk_root, steps=30)
    assert not fired

    frame.destroy()


def test_late_when_ready_does_not_fire_after_destroy(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    web = WebView(frame, html="<p>late-when-ready</p>")
    assert web.wait_until_ready(timeout=10.0)

    fired: list[bool] = []
    web.when_ready(lambda: fired.append(True))
    web.destroy()
    pump(tk_root, steps=5)
    assert not fired

    frame.destroy()


def test_late_ready_bind_does_not_fire_after_destroy(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    web = WebView(frame, html="<p>late-bind</p>")
    assert web.wait_until_ready(timeout=10.0)

    fired: list[bool] = []
    web.bind("<<WebViewReady>>", lambda _evt: fired.append(True))
    web.destroy()
    pump(tk_root, steps=5)
    assert not fired

    frame.destroy()


def test_configure_triggers_create(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=500, height=350)
    web = WebView(frame, html="<p>cfg</p>")

    assert web.native is None
    frame.pack()
    tk_root.update_idletasks()
    tk_root.update()

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    assert web.native is not None

    web.destroy()
    frame.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI does not reliably deliver page-load callbacks",
)
def test_no_eval_callback_after_destroy(tk_root) -> None:
    results: list[str] = []
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>x</p>")
    layout_bare_frame(frame, width=400, height=300)
    assert web.wait_until_ready(timeout=10.0)
    web.eval_js_with_callback("'ok'", lambda val: results.append(val))
    web.destroy()
    pump(tk_root, steps=80)
    assert not results

    frame.destroy()
