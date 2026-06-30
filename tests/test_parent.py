"""Tests for Tk parent-handle resolution (no WebView required)."""

from __future__ import annotations

from tkwry._parent import EmbedParent, tk_embed_origin, tk_embed_parent


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
