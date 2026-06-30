"""End-to-end viewport size checks via JavaScript eval."""

from __future__ import annotations

import sys

import pytest
from helpers import (
    VIEWPORT_HTML,
    pump,
    read_viewport,
    skip_linux_layout,
    viewport_matches_frame,
    wait_until,
)

from tkwry import WebView

pytestmark = pytest.mark.integration


def _wait_ready(tk_root, web: WebView) -> None:
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=40)


@skip_linux_layout
def test_viewport_matches_frame_after_web_pack(tk_root) -> None:
    """``web.pack()`` before native create: JS viewport must match host frame."""
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)
    web.pack(fill="both", expand=True)

    _wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)

    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


@skip_linux_layout
def test_viewport_matches_frame_after_late_host_pack(tk_root) -> None:
    """Host ``pack()`` after ``WebView()``: JS viewport must match final geometry."""
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)

    assert wait_until(tk_root, lambda: web.native is not None, steps=20) is False
    host.pack(fill="both", expand=True)
    _wait_ready(tk_root, web)

    viewport = read_viewport(web, tk_root)
    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows WebView2 layout",
)
def test_viewport_shrinks_after_sibling_pack(tk_root) -> None:
    """Packing a header after WebView exists must shrink JS viewport height."""
    import tkinter as tk

    tk_root.geometry("520x380")
    body = tk.Frame(tk_root)
    body.pack(fill="both", expand=True)

    host = tk.Frame(body, bg="#222")
    host.pack(fill="both", expand=True)

    web = WebView(host, html=VIEWPORT_HTML)
    _wait_ready(tk_root, web)

    before = read_viewport(web, tk_root)
    assert before is not None
    assert viewport_matches_frame(before, host)

    header = tk.Frame(body, height=48, bg="#444")
    header.pack(side="top", fill="x", before=host)
    header.pack_propagate(False)
    pump(tk_root, steps=80)

    after = read_viewport(web, tk_root)
    assert after is not None
    assert after[1] < before[1] - 30, (
        f"viewport height should shrink: before={before} after={after}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )
    assert viewport_matches_frame(after, host), (
        f"stale viewport after sibling pack: {after}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    body.destroy()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows WebView2 layout",
)
def test_viewport_stable_after_resize_and_redraw(tk_root) -> None:
    """Resize/redraw keeps JS viewport matching the frame on re-read."""
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)
    web.pack(fill="both", expand=True)
    _wait_ready(tk_root, web)

    tk_root.geometry("460x300")
    pump(tk_root, steps=60)
    web._sync_bounds()
    pump(tk_root, steps=40)

    first = read_viewport(web, tk_root)
    second = read_viewport(web, tk_root)

    assert viewport_matches_frame(first, host), (
        f"viewport={first}, frame={host.winfo_width()}x{host.winfo_height()}"
    )
    assert first == second, (
        f"viewport unstable after redraw: first={first} second={second}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="eval_js_with_callback viewport check is WebKitGTK-only; IPC covers macOS and Windows",
)
def test_viewport_via_eval_callback_matches_frame(tk_root) -> None:
    """``eval_js_with_callback`` path must agree with Tk frame size after page load."""
    import tkinter as tk

    from tkwry import PageLoadEvent

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    loaded: list[PageLoadEvent] = []
    web = WebView(
        host,
        html=VIEWPORT_HTML,
        on_page_load=lambda evt, _url: loaded.append(evt),
    )
    web.pack(fill="both", expand=True)

    assert wait_until(tk_root, lambda: web.native is not None)
    assert wait_until(
        tk_root,
        lambda: PageLoadEvent.Finished in loaded,
        steps=300,
    )
    pump(tk_root, steps=20)

    from helpers import read_viewport_via_callback

    viewport = read_viewport_via_callback(web, tk_root, steps=300)
    assert viewport_matches_frame(viewport, host), (
        f"callback viewport={viewport}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


@skip_linux_layout
def test_viewport_not_stale_after_repack(tk_root) -> None:
    """Re-``pack()`` with identical options must not leave a stale JS viewport."""
    import tkinter as tk

    tk_root.geometry("400x300")
    host = tk.Frame(tk_root, width=320, height=200, bg="#222")
    host.pack_propagate(False)
    host.pack()

    web = WebView(host, html=VIEWPORT_HTML)
    _wait_ready(tk_root, web)

    before = read_viewport(web, tk_root)
    assert viewport_matches_frame(before, host)

    web.pack(fill="both", expand=True)
    pump(tk_root, steps=40)

    after = read_viewport(web, tk_root)
    assert viewport_matches_frame(after, host), (
        f"stale viewport after repack: before={before} after={after}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()
