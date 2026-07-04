"""Tests for Tk parent-handle resolution (no WebView required)."""

from __future__ import annotations

import sys
import threading

import pytest

from tkwry._parent import (
    EmbedParent,
    require_tk_thread,
    tk_embed_origin,
    tk_embed_parent,
)


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
    _parent._mac_tk_dylib_key = None
    _parent._mac_tk_dylib_handle = None

    frame = tk.Frame(tk_root, width=200, height=150)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()

    embed = tk_embed_parent(frame)
    assert embed.handle != 0
    assert created == []
