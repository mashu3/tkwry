"""macOS Tk ↔ WebView keyboard focus tests."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pytest
from support.tk import is_github_actions

from tkwry import WebView

if sys.platform == "darwin":
    from support.macos_input import (
        activate_window,
        center,
        cgevent_clicks_reach_tk,
        click_entry,
        post_screen_click,
        pump,
        rapid_entry_keypresses,
        type_a_on_entry,
        wait_tcl_focus_leaves,
        wait_until,
        wry_point,
    )


@pytest.fixture
def url_demo_layout(tk_root):
    tk_root.geometry("640x480")
    toolbar = ttk.Frame(tk_root)
    toolbar.pack(fill="x", padx=8, pady=(8, 0))
    url_entry = ttk.Entry(toolbar)
    url_entry.pack(side="left", fill="x", expand=True)
    web_frame = tk.Frame(tk_root, bg="#1e1e1e")
    web_frame.pack(fill="both", expand=True, padx=8, pady=8)
    tk_root.update_idletasks()
    return SimpleNamespace(
        root=tk_root,
        toolbar=toolbar,
        url_entry=url_entry,
        web_frame=web_frame,
    )


def _wait_native(web: WebView, root: tk.Misc) -> None:
    assert wait_until(root, lambda: web.native is not None), (
        "native WebView not created"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_hit_test_separates_url_bar_from_web(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>hit</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        root = url_demo_layout.root
        native = web.native
        assert native is not None

        ex, ey = wry_point(root, url_demo_layout.url_entry)
        wx, wy = wry_point(root, url_demo_layout.web_frame)
        assert not native.mac_hit_test_wry_point(ex, ey), (
            f"URL bar ({ex:.0f},{ey:.0f}) must not hit webview"
        )
        assert native.mac_hit_test_wry_point(wx, wy), (
            f"web frame ({wx:.0f},{wy:.0f}) must hit webview"
        )
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_url_bar_types_after_leaving_web(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>focus</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        activate_window(url_demo_layout.root)
        entry = url_demo_layout.url_entry
        entry.delete(0, tk.END)

        web.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert web.native is not None and web.native.mac_web_input_active()

        web.focus_parent()
        pump(url_demo_layout.root, seconds=0.05)
        assert web.native is not None and not web.native.mac_web_input_active()

        click_entry(url_demo_layout.root, entry)
        assert wait_until(
            url_demo_layout.root,
            lambda: url_demo_layout.root.focus_get() is entry,
        )

        type_a_on_entry(url_demo_layout.root, entry)
        assert entry.get() == "a"
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_focus_parent_round_trip_restores_web_mode(url_demo_layout) -> None:
    """focus → focus_parent → focus must toggle mac_web_input_active reliably."""
    web = WebView(url_demo_layout.web_frame, html="<p>round-trip</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        activate_window(url_demo_layout.root)
        entry = url_demo_layout.url_entry
        entry.delete(0, tk.END)
        native = web.native
        assert native is not None

        web.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert native.mac_web_input_active()

        web.focus_parent()
        pump(url_demo_layout.root, seconds=0.05)
        assert not native.mac_web_input_active()

        click_entry(url_demo_layout.root, entry)
        type_a_on_entry(url_demo_layout.root, entry)
        assert entry.get() == "a"

        web.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert native.mac_web_input_active()

        before = entry.get()
        type_a_on_entry(url_demo_layout.root, entry)
        assert entry.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_multi_webview_focus_is_exclusive(url_demo_layout) -> None:
    """Only one WebView should own macOS keyboard input at a time."""
    left = tk.Frame(url_demo_layout.web_frame, bg="#111")
    right = tk.Frame(url_demo_layout.web_frame, bg="#222")
    left.pack(side="left", fill="both", expand=True)
    right.pack(side="right", fill="both", expand=True)

    web_a = WebView(left, html="<p>a</p>")
    web_b = WebView(right, html="<p>b</p>")
    try:
        _wait_native(web_a, url_demo_layout.root)
        assert wait_until(url_demo_layout.root, lambda: web_b.native is not None)
        native_a = web_a.native
        native_b = web_b.native
        assert native_a is not None and native_b is not None

        web_a.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert native_a.mac_web_input_active()
        assert not native_b.mac_web_input_active()

        web_b.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert not native_a.mac_web_input_active()
        assert native_b.mac_web_input_active()

        web_b.focus_parent()
        pump(url_demo_layout.root, seconds=0.05)
        assert not native_a.mac_web_input_active()
        assert not native_b.mac_web_input_active()

        web_a.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert native_a.mac_web_input_active()
        assert not native_b.mac_web_input_active()
    finally:
        web_b.destroy()
        web_a.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
@pytest.mark.skipif(
    is_github_actions(),
    reason="GHA macOS: Tcl focus drain timing not reliable on virtual runners",
)
def test_tcl_unfocus_drains_within_50ms(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>latency</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        entry = url_demo_layout.url_entry
        native = web.native
        assert native is not None
        entry.focus_force()
        entry.insert(0, "https://example.com")
        url_demo_layout.root.update()

        native.mac_request_tk_unfocus()
        elapsed = wait_tcl_focus_leaves(url_demo_layout.root, entry, timeout=0.25)
        assert elapsed < 0.05, (
            f"Tcl focus stayed on URL bar for {elapsed * 1000:.0f}ms "
            "(input lag if Entry keeps Tcl focus during web typing)"
        )
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_rapid_keys_do_not_reach_entry_while_web_active(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>latency</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        entry = url_demo_layout.url_entry
        entry.focus_force()
        entry.insert(0, "https://example.com")
        url_demo_layout.root.update()

        web.focus()
        assert web.native is not None and web.native.mac_web_input_active()

        before = entry.get()
        rapid_entry_keypresses(entry, url_demo_layout.root, 50)
        url_demo_layout.root.update()
        assert entry.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_dynamic_entry_gets_key_guard_while_web_active(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>keys</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        web.focus()
        pump(url_demo_layout.root, seconds=0.05)
        assert web.native is not None and web.native.mac_web_input_active()

        entry = ttk.Entry(url_demo_layout.toolbar)
        entry.pack(side="left", padx=(8, 0))
        url_demo_layout.root.update_idletasks()
        assert entry.bindtags()[0] == "TkwryMacWebKeyGuard"

        entry.focus_force()
        entry.insert(0, "https://example.com")
        url_demo_layout.root.update()

        before = entry.get()
        entry.event_generate("<KeyPress-a>")
        url_demo_layout.root.update()
        assert entry.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_key_guard_blocks_combobox_while_web_active(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>keys</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        combo = ttk.Combobox(url_demo_layout.toolbar, values=("a", "b"))
        combo.pack(side="left", padx=(8, 0))
        url_demo_layout.root.update_idletasks()

        combo.focus_force()
        combo.set("a")
        url_demo_layout.root.update()

        web.focus()
        pump(url_demo_layout.root, seconds=0.05)
        assert combo.bindtags()[0] == "TkwryMacWebKeyGuard"

        before = combo.get()
        combo.event_generate("<KeyPress-b>")
        url_demo_layout.root.update()
        assert combo.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_key_guard_blocks_entry_while_web_active(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>keys</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        entry = url_demo_layout.url_entry
        entry.focus_force()
        entry.insert(0, "https://example.com")
        url_demo_layout.root.update()

        web.focus()
        pump(url_demo_layout.root, seconds=0.05)
        assert entry.bindtags()[0] == "TkwryMacWebKeyGuard"

        before = entry.get()
        entry.event_generate("<KeyPress-a>")
        url_demo_layout.root.update()
        assert entry.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_web_mode_blocks_url_bar_keys(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>focus</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        activate_window(url_demo_layout.root)
        entry = url_demo_layout.url_entry
        entry.delete(0, tk.END)
        entry.insert(0, "https://example.com")
        entry.focus_force()
        url_demo_layout.root.update()

        web.focus()
        pump(url_demo_layout.root, seconds=0.15)
        assert web.native is not None and web.native.mac_web_input_active()
        assert wait_until(
            url_demo_layout.root,
            lambda: url_demo_layout.root.focus_get() is not entry,
        )

        before = entry.get()
        type_a_on_entry(url_demo_layout.root, entry)
        assert entry.get() == before
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
@pytest.mark.skipif(
    is_github_actions(),
    reason="GHA macOS: CGEvent / Accessibility unavailable (can abort)",
)
def test_cgevent_web_click_activates_web_mode(url_demo_layout) -> None:
    web = WebView(url_demo_layout.web_frame, html="<p>focus</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        entry = url_demo_layout.url_entry
        if not cgevent_clicks_reach_tk(url_demo_layout.root, entry):
            pytest.skip("CGEvent clicks do not reach Tk (grant Accessibility)")

        activate_window(url_demo_layout.root)
        wx, wy = center(url_demo_layout.web_frame)
        post_screen_click(wx, wy)
        assert wait_until(
            url_demo_layout.root,
            lambda: web.native is not None and web.native.mac_web_input_active(),
        )
    finally:
        web.destroy()
