"""Resolve a Tk widget to a native parent handle for child webviews."""

from __future__ import annotations

import os
import sys
import threading
from ctypes import CDLL, c_void_p
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

# Tcl interpreter id -> owning thread id (set only from the Tk thread).
_interp_threads: dict[int, int] = {}

_TK_THREAD_ATTR = "_tkwry_tk_thread"
_THREAD_ERROR = (
    "tkwry must be called from the thread that created the Tk "
    "application (the thread that runs the Tk event loop)"
)


@dataclass(frozen=True)
class EmbedParent:
    """Native parent handle and how to position a child webview inside it.

    On macOS, Tk child frames do not get their own ``NSView`` (``macWin->view``
    is NULL); ``GetRootControl`` returns the toplevel content view for all
    descendants.  WebViews are therefore siblings positioned with offsets, not
    nested views.  Callers must hide webviews when the host frame is unmapped.
    """

    handle: int
    root_relative: bool = False


def check_tk_thread_id(owner: int) -> None:
    """Raise ``RuntimeError`` if the current thread is not *owner*."""
    if threading.get_ident() != owner:
        raise RuntimeError(_THREAD_ERROR)


def require_tk_thread(widget: tk.Misc) -> None:
    """Raise ``RuntimeError`` if called off the thread that owns *widget*.

    Tkinter is not thread-safe. The owning thread is recorded on first use and
    cached on the widget as a plain Python attribute so later checks never touch
    Tcl from a foreign thread (which can abort the process on Linux).
    """
    ident = threading.get_ident()
    # Safe from any thread: plain Python attribute, no Tcl access.
    owner = getattr(widget, _TK_THREAD_ATTR, None)
    if owner is not None:
        check_tk_thread_id(owner)
        return

    # First bind must run on the Tk thread (accessing widget.tk is only safe there).
    interp = id(widget.tk)
    owner = _interp_threads.get(interp)
    if owner is None:
        owner = ident
        _interp_threads[interp] = owner
    setattr(widget, _TK_THREAD_ATTR, owner)
    check_tk_thread_id(owner)


def _mac_libtk_path(tcl_lib: str) -> str:
    """Locate libtk next to Tcl (Homebrew, python.org framework, etc.)."""
    tcl_parent = os.path.dirname(tcl_lib)
    search_dirs: list[str] = [tcl_parent]
    framework_lib = os.path.join(os.path.dirname(tcl_parent), "lib")
    if framework_lib not in search_dirs:
        search_dirs.append(framework_lib)
    if os.path.isdir(tcl_parent):
        for entry in os.listdir(tcl_parent):
            if entry.startswith("tk"):
                path = os.path.join(tcl_parent, entry)
                if os.path.isdir(path):
                    search_dirs.append(path)

    combined: list[str] = []
    libtk_only: list[str] = []
    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".dylib"):
                continue
            lower = name.lower()
            full = os.path.join(directory, name)
            if "tk" in lower and "tcl" in lower:
                combined.append(full)
            elif lower.startswith("libtk"):
                libtk_only.append(full)

    if combined:
        return sorted(combined)[0]
    if libtk_only:
        return sorted(libtk_only)[0]
    raise RuntimeError(
        f"libtk dylib not found near tcl library {tcl_lib!r} (searched {search_dirs})"
    )


@lru_cache(maxsize=1)
def _mac_nsview_lookup(tcl_lib: str):
    """Return ``TkMacOSXGetRootControl`` for the Tk build next to *tcl_lib*."""
    dylib = CDLL(_mac_libtk_path(tcl_lib))
    fn = dylib.TkMacOSXGetRootControl
    fn.restype = c_void_p
    fn.argtypes = (c_void_p,)
    return fn


def tk_embed_parent(widget: tk.Misc) -> EmbedParent:
    """Return the native parent handle and positioning mode for *widget*."""
    require_tk_thread(widget)
    widget.update_idletasks()
    wid = widget.winfo_id()

    if sys.platform == "win32":
        return EmbedParent(wid)

    if sys.platform == "darwin":
        tcl_lib = widget.tk.call("info", "library")
        lookup = _mac_nsview_lookup(tcl_lib)
        nsview = lookup(c_void_p(wid))
        if not nsview:
            raise RuntimeError("TkMacOSXGetRootControl returned NULL")
        handle = int(nsview)
        top = widget.winfo_toplevel()
        top_ns = lookup(c_void_p(top.winfo_id()))
        root_relative = bool(top_ns) and handle == int(top_ns)
        return EmbedParent(handle, root_relative=root_relative)

    # Linux (X11): winfo_id is the X11 window ID.
    return EmbedParent(wid)


def tk_parent_handle(widget: tk.Misc) -> int:
    """Return the native handle to embed a child webview into *widget*."""
    return tk_embed_parent(widget).handle


def tk_embed_origin(widget: tk.Misc, *, root_relative: bool) -> tuple[int, int]:
    """Return the (x, y) origin for ``set_bounds`` inside the embed parent."""
    require_tk_thread(widget)
    if not root_relative:
        return (0, 0)
    toplevel = widget.winfo_toplevel()
    return (
        widget.winfo_rootx() - toplevel.winfo_rootx(),
        widget.winfo_rooty() - toplevel.winfo_rooty(),
    )
