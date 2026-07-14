"""Notebook tab map/unmap visibility (critical on macOS shared NSView)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from support.tk import pump, wait_until

from tkwry import WebView


class _VisibilitySpy:
    """Proxy native WebView so ``set_visible`` calls are recorded."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[bool] = []
        self.visible: bool | None = None

    def set_visible(self, visible: bool) -> None:
        flag = bool(visible)
        self.calls.append(flag)
        self.visible = flag
        self._inner.set_visible(flag)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _spy_visibility(web: WebView) -> _VisibilitySpy:
    native = web.native
    assert native is not None
    spy = _VisibilitySpy(native)
    web._webview = spy  # type: ignore[assignment]
    return spy


def test_notebook_hides_inactive_tab_webview(tk_root) -> None:
    """Inactive Notebook tabs must ``set_visible(False)`` (macOS shares one NSView)."""
    tk_root.geometry("520x400")
    notebook = ttk.Notebook(tk_root)
    notebook.pack(fill="both", expand=True)

    tab_a = tk.Frame(notebook, bg="#111")
    tab_b = tk.Frame(notebook, bg="#222")
    notebook.add(tab_a, text="A")
    notebook.add(tab_b, text="B")

    web_a = WebView(tab_a, html="<p>tab-a</p>")
    assert wait_until(tk_root, lambda: web_a.ready, steps=200)
    spy_a = _spy_visibility(web_a)

    notebook.select(tab_b)
    pump(tk_root, steps=40)
    web_b = WebView(tab_b, html="<p>tab-b</p>")
    assert wait_until(tk_root, lambda: web_b.ready, steps=200)
    spy_b = _spy_visibility(web_b)

    # Selecting B unmaps A; both should still report ready (layout-based).
    assert tab_a.winfo_viewable() == 0
    assert tab_b.winfo_viewable() == 1
    assert web_a.ready is True
    assert web_b.ready is True
    assert web_a._frame_should_show() is False
    assert web_b._frame_should_show() is True

    spy_a.calls.clear()
    spy_b.calls.clear()
    assert web_a._sync_bounds() is False
    assert web_b._sync_bounds() is True
    assert spy_a.visible is False
    assert spy_b.visible is True

    notebook.select(tab_a)
    assert wait_until(
        tk_root,
        lambda: tab_a.winfo_viewable() == 1 and tab_b.winfo_viewable() == 0,
        steps=80,
    )
    spy_a.calls.clear()
    spy_b.calls.clear()
    assert web_a._frame_should_show() is True
    assert web_b._frame_should_show() is False
    assert web_a._sync_bounds() is True
    assert web_b._sync_bounds() is False
    assert spy_a.visible is True
    assert spy_b.visible is False

    web_a.destroy()
    web_b.destroy()
    notebook.destroy()


def test_notebook_unmap_auto_syncs_visibility(tk_root) -> None:
    """``<Unmap>`` / ``<Map>`` from Notebook select should drive visibility sync."""
    tk_root.geometry("520x400")
    notebook = ttk.Notebook(tk_root)
    notebook.pack(fill="both", expand=True)

    tab_a = tk.Frame(notebook)
    tab_b = tk.Frame(notebook)
    notebook.add(tab_a, text="A")
    notebook.add(tab_b, text="B")

    web_a = WebView(tab_a, html="<p>a</p>")
    assert wait_until(tk_root, lambda: web_a.ready, steps=200)
    spy_a = _spy_visibility(web_a)

    notebook.select(tab_b)
    assert wait_until(
        tk_root,
        lambda: spy_a.visible is False,
        steps=120,
    ), f"expected tab A hidden after select B, calls={spy_a.calls!r}"

    notebook.select(tab_a)
    assert wait_until(
        tk_root,
        lambda: spy_a.visible is True,
        steps=120,
    ), f"expected tab A shown after re-select, calls={spy_a.calls!r}"

    web_a.destroy()
    notebook.destroy()
