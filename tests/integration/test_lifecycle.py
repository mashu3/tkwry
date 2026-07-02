"""Ready events, initial size, and destroy safety."""

from __future__ import annotations

import sys

import pytest
from support.tk import bare_frame, pump, skip_linux_ci, wait_until

from tkwry import WebView, WebViewDestroyedError, WebViewNotReadyError

pytestmark = skip_linux_ci


def test_initial_size_creates_without_pack(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=640, height=480, html="<p>size</p>")

    assert web.wait_until_ready(timeout=10.0)
    assert web.ready
    assert web.native is not None

    web.destroy()
    frame.destroy()


def test_eval_js_raises_before_ready(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)

    with pytest.raises(WebViewNotReadyError, match="eval_js"):
        web.eval_js("1+1")

    assert web.wait_until_ready(timeout=10.0)
    web.destroy()
    frame.destroy()


def test_reload_raises_before_ready(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)

    with pytest.raises(WebViewNotReadyError, match="reload"):
        web.reload()

    assert web.wait_until_ready(timeout=10.0)
    web.destroy()
    frame.destroy()


def test_load_url_pending_before_ready_still_works(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.load_url("example.com")

    assert web.url == "https://example.com"
    assert web.wait_until_ready(timeout=10.0)

    web.destroy()
    frame.destroy()


def test_webview_ready_virtual_event(tk_root) -> None:
    frame = bare_frame(tk_root)
    fired: list[bool] = []
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")
    web.bind("<<WebViewReady>>", lambda _evt: fired.append(True))

    assert wait_until(tk_root, lambda: fired, steps=200)
    assert web.ready

    web.destroy()
    frame.destroy()


def test_when_ready_callback(tk_root) -> None:
    frame = bare_frame(tk_root)
    fired: list[bool] = []
    web = WebView(frame, width=400, height=300, html="<p>when</p>")
    web.when_ready(lambda: fired.append(True))

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


def test_double_destroy_is_safe(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>destroy</p>")
    assert web.wait_until_ready(timeout=10.0)

    web.destroy()
    web.destroy()
    assert web.destroyed
    assert web.native is None

    frame.destroy()


def test_eval_js_raises_after_destroy(tk_root) -> None:
    frame = bare_frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>x</p>")
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
    assert web.wait_until_ready(timeout=10.0)
    web.eval_js_with_callback("'ok'", lambda val: results.append(val))
    web.destroy()
    pump(tk_root, steps=80)
    assert not results

    frame.destroy()
