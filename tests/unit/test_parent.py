"""Tests for Tk parent-handle resolution (no WebView required)."""

from __future__ import annotations

import sys
import threading

import pytest

from tkwry import _parent
from tkwry._parent import (
    EmbedParent,
    require_tk_thread,
    tk_embed_origin,
    tk_embed_parent,
)


@pytest.fixture(autouse=True)
def _clear_parent_thread_maps() -> None:
    _parent._interp_threads.clear()
    _parent._widget_threads.clear()
    _parent._interp_refcounts.clear()
    _parent._interp_root_hooks.clear()
    yield
    _parent._interp_threads.clear()
    _parent._widget_threads.clear()
    _parent._interp_refcounts.clear()
    _parent._interp_root_hooks.clear()


def test_embed_origin_not_root_relative(tk_root) -> None:
    import tkinter as tk

    tk_root.withdraw()
    frame = tk.Frame(tk_root)
    frame.place(x=12, y=24)
    tk_root.update_idletasks()
    assert tk_embed_origin(frame, root_relative=False) == (0, 0)


def test_embed_origin_root_relative_uses_toplevel_offset(tk_root) -> None:
    import tkinter as tk

    tk_root.withdraw()
    frame = tk.Frame(tk_root)
    frame.place(x=30, y=40, width=200, height=120)
    tk_root.update_idletasks()
    x, y = tk_embed_origin(frame, root_relative=True)
    assert x == frame.winfo_rootx() - tk_root.winfo_rootx()
    assert y == frame.winfo_rooty() - tk_root.winfo_rooty()


def test_embed_parent_returns_nonzero_handle(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, width=200, height=150)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    embed = tk_embed_parent(frame)
    assert isinstance(embed, EmbedParent)
    assert embed.handle != 0


def test_require_tk_thread_rejects_other_thread(tk_root) -> None:
    import tkinter as tk

    from tkwry._parent import check_tk_thread_id

    frame = tk.Frame(tk_root)
    require_tk_thread(frame)
    # Capture a plain int on the Tk thread. Never pass the widget into a
    # worker — even reading it from another thread can abort on Linux.
    owner = threading.get_ident()
    errors: list[str] = []

    def worker() -> None:
        try:
            check_tk_thread_id(owner)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert "thread" in errors[0].lower()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only lookup path")
def test_mac_nsview_lookup_uses_existing_widget_tcl(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    from tkwry import _parent

    created: list[object] = []
    original_tk = tk.Tk

    def tracking_tk(*args: object, **kwargs: object):
        root = original_tk(*args, **kwargs)
        created.append(root)
        return root

    monkeypatch.setattr(tk, "Tk", tracking_tk)
    _parent._mac_tk_dylib_cache.clear()

    frame = tk.Frame(tk_root, width=200, height=150)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    embed = tk_embed_parent(frame)
    assert embed.handle != 0
    assert created == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only dylib cache")
def test_mac_tk_dylib_cached_per_tcl_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tkwry import _parent

    loaded: list[str] = []

    def fake_cdll(path: str) -> object:
        loaded.append(path)
        return object()

    monkeypatch.setattr(_parent, "CDLL", fake_cdll)
    monkeypatch.setattr(
        _parent,
        "_mac_libtk_path",
        lambda tcl_lib: f"/fake/{tcl_lib}/libtk.dylib",
    )
    _parent._mac_tk_dylib_cache.clear()

    dylib_a = _parent._mac_tk_dylib("tcl-a")
    dylib_b = _parent._mac_tk_dylib("tcl-b")
    dylib_a_again = _parent._mac_tk_dylib("tcl-a")

    assert dylib_a is not dylib_b
    assert dylib_a is dylib_a_again
    assert loaded == ["/fake/tcl-a/libtk.dylib", "/fake/tcl-b/libtk.dylib"]


def test_interp_threads_cleaned_when_tk_root_destroyed(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    require_tk_thread(frame)
    interp = id(tk_root.tk)
    assert interp in _parent._interp_threads

    tk_root.destroy()

    assert interp not in _parent._interp_threads
    assert interp not in _parent._interp_refcounts


def test_tk_window_drawable_offsets_scale_with_pointer_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_parent, "sizeof", lambda _type: 8)
    assert _parent._tk_window_drawable_offsets() == (40, 48, 32)

    monkeypatch.setattr(_parent, "sizeof", lambda _type: 4)
    assert _parent._tk_window_drawable_offsets() == (20, 24, 16)


def test_mac_drawable_from_tk_window_probes_offsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ctypes import c_void_p

    wid = 0x0000_00AB
    full = 0xFFFF_FF00_0000_00AB
    calls: list[int] = []

    def fake_from_address(addr: int) -> c_void_p:
        calls.append(addr)
        offset = addr - 1000
        value = full if offset == 24 else 0
        return c_void_p(value)

    monkeypatch.setattr(_parent, "sizeof", lambda _type: 4)
    monkeypatch.setattr(_parent.c_void_p, "from_address", fake_from_address)

    assert _parent._mac_drawable_from_tk_window(1000, wid) == full
    assert calls == [1020, 1024]
