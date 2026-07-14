"""JavaScript viewport size checks against Tk frame geometry."""

from __future__ import annotations

import sys

import pytest
from support.tk import pump, wait_ready, wait_until
from support.viewport import (
    VIEWPORT_HTML,
    read_viewport,
    read_viewport_via_callback,
    viewport_matches_frame,
)

from tkwry import PageLoadEvent, WebView


def test_viewport_matches_frame_after_web_pack(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)
    web.pack(fill="both", expand=True)

    wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)

    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


def test_viewport_matches_frame_after_late_host_pack(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)

    assert wait_until(tk_root, lambda: web.native is not None, steps=20) is False
    host.pack(fill="both", expand=True)
    wait_ready(tk_root, web)

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
    import tkinter as tk

    tk_root.geometry("520x380")
    body = tk.Frame(tk_root)
    body.pack(fill="both", expand=True)

    host = tk.Frame(body, bg="#222")
    host.pack(fill="both", expand=True)

    web = WebView(host, html=VIEWPORT_HTML)
    wait_ready(tk_root, web)

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


def test_viewport_stable_after_resize_and_redraw(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html=VIEWPORT_HTML)
    web.pack(fill="both", expand=True)
    wait_ready(tk_root, web)

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


def test_viewport_via_eval_callback_matches_frame(tk_root) -> None:
    import tkinter as tk

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

    viewport = read_viewport_via_callback(web, tk_root, steps=300)
    assert viewport_matches_frame(viewport, host), (
        f"callback viewport={viewport}, "
        f"frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


def test_viewport_not_stale_after_repack(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("400x300")
    host = tk.Frame(tk_root, width=320, height=200, bg="#222")
    host.pack_propagate(False)
    host.pack()

    web = WebView(host, html=VIEWPORT_HTML)
    wait_ready(tk_root, web)

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


def test_viewport_matches_frame_after_grid(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    host.grid(row=0, column=0, sticky="nsew")
    tk_root.grid_rowconfigure(0, weight=1)
    tk_root.grid_columnconfigure(0, weight=1)

    web = WebView(host, html=VIEWPORT_HTML)
    web.grid(sticky="nsew")
    host.grid_rowconfigure(0, weight=1)
    host.grid_columnconfigure(0, weight=1)

    wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)
    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


def test_viewport_matches_frame_after_place(tk_root) -> None:
    import tkinter as tk

    from support.viewport import VIEWPORT_TOLERANCE

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    placed = (480, 320)
    # Pass width/height to place() — configured Frame size is not always
    # reflected by winfo_* under Linux/Xvfb for place geometry.
    host.place(
        relx=0.5,
        rely=0.5,
        anchor="center",
        width=placed[0],
        height=placed[1],
    )

    web = WebView(host, html=VIEWPORT_HTML)
    # Pack the WebView into a placed host. Direct web.place() leaves WebKitGTK
    # without a reliable content load path on Linux headless.
    web.pack(fill="both", expand=True)

    wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)
    assert viewport is not None, "expected viewport measurement"
    matched_host = viewport_matches_frame(viewport, host)
    matched_place = (
        abs(viewport[0] - placed[0]) <= VIEWPORT_TOLERANCE
        and abs(viewport[1] - placed[1]) <= VIEWPORT_TOLERANCE
    )
    # Linux/Xvfb may report toplevel size via winfo_* while the WebView still
    # respects the place() request; accept either contract.
    assert matched_host or matched_place, (
        f"viewport={viewport}, placed={placed[0]}x{placed[1]}, "
        f"winfo={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


def test_viewport_matches_frame_with_explicit_width_only(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, height=280, bg="#222")
    host.pack_propagate(False)
    # Pack once via WebView.pack (packs the host frame). Do not pack the host
    # first and then call web.pack again — that remaps the frame.
    web = WebView(host, width=480, html=VIEWPORT_HTML)
    web.pack(fill="x", padx=8, pady=8)

    wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)
    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()


def test_viewport_matches_frame_with_explicit_height_only(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, width=360, bg="#222")
    host.pack_propagate(False)
    web = WebView(host, height=280, html=VIEWPORT_HTML)
    web.pack(side="left", fill="y", padx=8, pady=8)

    wait_ready(tk_root, web)
    viewport = read_viewport(web, tk_root)
    assert viewport_matches_frame(viewport, host), (
        f"viewport={viewport}, frame={host.winfo_width()}x{host.winfo_height()}"
    )

    web.destroy()
    host.destroy()
