"""Resolve a Tk widget to a native parent handle for child webviews."""

from __future__ import annotations

import os
import sys
import threading
import weakref
from ctypes import CDLL, Structure, byref, c_char_p, c_short, c_void_p, sizeof
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

# Tcl interpreter id / widget id -> owning thread id (bound only on the Tk thread).
_interp_threads: dict[int, int] = {}
_widget_threads: dict[int, int] = {}
_interp_refcounts: dict[int, int] = {}
_interp_root_hooks: set[int] = set()

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


def _main_tk_root(widget: tk.Misc) -> tk.Misc:
    """Return the application root ``Tk`` widget (not a ``Toplevel``)."""
    current: tk.Misc = widget
    while current.master not in (None, ""):
        current = current.master
    return current


def _clear_interp_thread(interp: int) -> None:
    _interp_threads.pop(interp, None)
    _interp_refcounts.pop(interp, None)
    _interp_root_hooks.discard(interp)


def _ensure_interp_root_destroy_hook(widget: tk.Misc, interp: int) -> None:
    if interp in _interp_root_hooks:
        return
    root = _main_tk_root(widget)
    _interp_root_hooks.add(interp)

    def _on_root_destroy(event: object) -> None:
        if getattr(event, "widget", None) is not root:
            return
        _clear_interp_thread(interp)

    root.bind("<Destroy>", _on_root_destroy, add="+")


def _release_widget_thread(key: int, interp: int) -> None:
    _widget_threads.pop(key, None)
    if interp not in _interp_refcounts:
        return
    remaining = _interp_refcounts[interp] - 1
    if remaining <= 0:
        _clear_interp_thread(interp)
    else:
        _interp_refcounts[interp] = remaining


def _bind_tk_thread(widget: tk.Misc, key: int) -> int:
    """Record the owning thread for *widget* (must run on the Tk thread)."""
    interp = id(widget.tk)
    owner = _interp_threads.get(interp)
    if owner is None:
        owner = threading.get_ident()
        _interp_threads[interp] = owner
        _ensure_interp_root_destroy_hook(widget, interp)
    _widget_threads[key] = owner
    _interp_refcounts[interp] = _interp_refcounts.get(interp, 0) + 1
    weakref.finalize(widget, _release_widget_thread, key, interp)
    return owner


def require_tk_thread(widget: tk.Misc) -> None:
    """Raise ``RuntimeError`` if called off the thread that owns *widget*.

    Tkinter is not thread-safe. The owning thread is recorded on first use.
    Later checks use only ``id(widget)`` and integer comparison so foreign
    threads never touch the widget or Tcl (either can abort on Linux).
    """
    # id() is safe from any thread; do not getattr/setattr the widget off-thread.
    key = id(widget)
    owner = _widget_threads.get(key)
    if owner is None:
        owner = _bind_tk_thread(widget, key)
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

    # Fallback: macOS system Tk.framework binary.  On Big Sur+ the on-disk
    # file may be a broken symlink but dlopen resolves it via the dyld
    # shared cache.
    fw_idx = tcl_lib.find("Tcl.framework")
    if fw_idx >= 0:
        candidate = os.path.join(tcl_lib[:fw_idx], "Tk.framework", "Tk")
        if os.path.exists(candidate) or os.path.islink(candidate):
            return candidate

    raise RuntimeError(
        f"libtk dylib not found near tcl library {tcl_lib!r} (searched {search_dirs})"
    )


def _mac_tk_version(widget: tk.Misc) -> tuple[int, ...]:
    """Return the Tk major.minor version as a tuple."""
    ver = widget.tk.call("info", "patchlevel")
    return tuple(int(x) for x in str(ver).split(".")[:2])


# Offset of the ``window`` (Drawable) field inside the TkWindow struct.
# Layout through ``depth`` is five pointer-sized slots on common Tk builds:
#   Display*, TkDisplay*, screenNum (+pad on 64-bit), Visual*, depth (+pad).
def _tk_window_drawable_offsets() -> tuple[int, ...]:
    ptr_size = sizeof(c_void_p)
    primary = 5 * ptr_size
    candidates = [primary]
    if ptr_size >= 8:
        candidates.extend((primary + ptr_size, primary - ptr_size))
    else:
        candidates.extend((primary + 4, primary - 4))
    return tuple(offset for offset in candidates if offset > 0)


def _tk_window_drawable_offset_candidates() -> tuple[int, ...]:
    """Return drawable-field offsets to probe, preferred order first."""
    ptr_size = sizeof(c_void_p)
    seen: set[int] = set()
    ordered: list[int] = []
    for offset in _tk_window_drawable_offsets():
        if offset not in seen:
            seen.add(offset)
            ordered.append(offset)
    # Non-standard Tk builds may place ``window`` elsewhere in the header.
    for offset in range(0, 32 * ptr_size + 1, ptr_size):
        if offset > 0 and offset not in seen:
            seen.add(offset)
            ordered.append(offset)
    return tuple(ordered)


def _mac_winfo_id_pointer_candidates(wid: int) -> tuple[int, ...]:
    """Reconstruct full pointers from Tk 8.5's truncated ``winfo id`` values."""
    seen: set[int] = set()
    ordered: list[int] = []

    def add(value: int) -> None:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)

    add(wid)
    low32 = wid & 0xFFFFFFFF
    add(low32)
    if sizeof(c_void_p) >= 8:
        if low32 & 0x80000000:
            add(low32 | 0xFFFFFFFF00000000)
        high_tag = (low32 & 0xFFFFFFFF) | 0x0000000100000000
        add(high_tag)
    return tuple(ordered)


def _mac_drawable_lookup_candidates(wid: int, lookup) -> int | None:
    for candidate in _mac_winfo_id_pointer_candidates(wid):
        if lookup(c_void_p(candidate)):
            return candidate
    return None


_mac_drawable_offset_cache: dict[tuple[str, tuple[int, ...]], int] = {}


class _MacQuickDrawRect(Structure):
    _fields_ = [
        ("top", c_short),
        ("left", c_short),
        ("bottom", c_short),
        ("right", c_short),
    ]


def _mac_tk_interp_ptr(widget: tk.Misc) -> c_void_p:
    return c_void_p(widget.tk.interpaddr())


def _mac_tk_window_ptr(widget: tk.Misc, dylib: CDLL) -> int:
    """Return the ``TkWindow*`` for *widget*, or ``0`` when lookup fails."""
    interp = _mac_tk_interp_ptr(widget)
    main_win_fn = dylib.Tk_MainWindow
    main_win_fn.restype = c_void_p
    main_win_fn.argtypes = (c_void_p,)
    main_win = main_win_fn(interp)
    if not main_win:
        return 0

    name_to_win = dylib.Tk_NameToWindow
    name_to_win.restype = c_void_p
    name_to_win.argtypes = (c_void_p, c_char_p, c_void_p)
    tk_win = name_to_win(interp, str(widget).encode(), c_void_p(main_win))
    return int(tk_win or 0)


def _mac_ensure_tkwindow_real(tk_win: int, dylib: CDLL) -> None:
    if not tk_win:
        return
    make_real = dylib.TkMacOSXMakeRealWindowExist
    make_real.argtypes = (c_void_p,)
    make_real(c_void_p(tk_win))


def _mac_nsview_ids_for_wid(wid: int, lookup) -> set[int]:
    ids: set[int] = set()
    for candidate in _mac_winfo_id_pointer_candidates(wid):
        nsview = lookup(c_void_p(candidate))
        if nsview:
            ids.add(int(nsview))
    return ids


def _mac_read_drawable_at(tk_win: int, offset: int) -> int:
    return c_void_p.from_address(tk_win + offset).value or 0


def _mac_drawable_from_tk_window(
    tk_win: int, wid: int, dylib: CDLL, *, cache_key: tuple[str, tuple[int, ...]] | None
) -> int:
    """Read the Drawable field, probing offsets validated via native Tk."""
    lookup = _mac_nsview_lookup(dylib)
    low32 = wid & 0xFFFFFFFF
    wid_ns_ids = _mac_nsview_ids_for_wid(wid, lookup)
    tried: list[int] = []
    candidates = _tk_window_drawable_offset_candidates()
    if cache_key is not None:
        cached = _mac_drawable_offset_cache.get(cache_key)
        if cached is not None:
            candidates = (
                cached,
                *(offset for offset in candidates if offset != cached),
            )

    def _accept(offset: int, full: int) -> int:
        if cache_key is not None:
            _mac_drawable_offset_cache[cache_key] = offset
        return full

    for offset in candidates:
        tried.append(offset)
        full = _mac_read_drawable_at(tk_win, offset)
        if not full:
            continue
        if (full & 0xFFFFFFFF) != low32:
            continue
        if lookup(c_void_p(full)):
            return _accept(offset, full)

    if wid_ns_ids:
        for offset in candidates:
            full = _mac_read_drawable_at(tk_win, offset)
            if not full:
                continue
            nsview = lookup(c_void_p(full))
            if nsview and int(nsview) in wid_ns_ids:
                return _accept(offset, full)

    for offset in candidates:
        full = _mac_read_drawable_at(tk_win, offset)
        if not full:
            continue
        if lookup(c_void_p(full)):
            return _accept(offset, full)

    direct = _mac_drawable_lookup_candidates(wid, lookup)
    if direct is not None:
        return direct
    raise RuntimeError(
        "Drawable sanity check failed for TkWindow struct "
        f"(tried offsets {tried!r}, winfo_id returned {wid:#x})"
    )


def _mac_drawable(widget: tk.Misc, dylib: CDLL) -> int:
    """Return the full 64-bit Drawable pointer for *widget*.

    Tk 8.5's ``winfo id`` truncates pointers to 32 bits on 64-bit systems.
    This bypasses the Tcl integer conversion by reading the ``window`` field
    directly from the C ``TkWindow`` struct.
    """
    wid = widget.winfo_id()
    lookup = _mac_nsview_lookup(dylib)
    direct = _mac_drawable_lookup_candidates(wid, lookup)
    if direct is not None:
        return direct
    if _mac_tk_version(widget) >= (8, 6) and lookup(c_void_p(wid)):
        return wid

    tk_win = _mac_tk_window_ptr(widget, dylib)
    if not tk_win:
        raise RuntimeError(f"Tk_NameToWindow failed for {widget!r}")

    tcl_lib = widget.tk.call("info", "library")
    cache_key = (tcl_lib, _mac_tk_version(widget))
    return _mac_drawable_from_tk_window(tk_win, wid, dylib, cache_key=cache_key)


def _mac_root_relative(*, handle: int, top_ns: int) -> bool:
    """Return whether bounds should be positioned relative to the toplevel."""
    # When the toplevel lookup fails we still need root-relative offsets for
    # child frames that share the toplevel NSView.
    return not top_ns or handle == top_ns


def _mac_widget_ready_for_native_bounds(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_ismapped()) and widget.winfo_width() > 0
    except tk.TclError:
        return False


def _mac_win_bounds(widget: tk.Misc, dylib: CDLL) -> tuple[int, int, int, int] | None:
    """Return ``(x, y, width, height)`` in the host toplevel coordinate space."""
    if not _mac_widget_ready_for_native_bounds(widget):
        return None
    tk_win = _mac_tk_window_ptr(widget, dylib)
    if not tk_win:
        return None

    bounds_fn = dylib.TkMacOSXWinBounds
    bounds_fn.argtypes = (c_void_p, c_void_p)
    rect = _MacQuickDrawRect()

    def _read() -> tuple[int, int, int, int]:
        bounds_fn(c_void_p(tk_win), byref(rect))
        width = int(rect.right) - int(rect.left)
        height = int(rect.bottom) - int(rect.top)
        return int(rect.left), int(rect.top), width, height

    x, y, width, height = _read()
    if width <= 0 or height <= 0:
        _mac_ensure_tkwindow_real(tk_win, dylib)
        x, y, width, height = _read()
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _mac_embed_origin(widget: tk.Misc) -> tuple[int, int] | None:
    """Return root-relative bounds origin using Tk's native macOS layout state."""
    tcl_lib = widget.tk.call("info", "library")
    dylib = _mac_tk_dylib(tcl_lib)
    bounds = _mac_win_bounds(widget, dylib)
    if bounds is None:
        return None
    return bounds[0], bounds[1]


_mac_tk_dylib_cache: dict[str, CDLL] = {}


def _mac_tk_dylib(tcl_lib: str) -> CDLL:
    """Load and return the Tk shared library next to *tcl_lib*."""
    dylib = _mac_tk_dylib_cache.get(tcl_lib)
    if dylib is None:
        dylib = CDLL(_mac_libtk_path(tcl_lib))
        _mac_tk_dylib_cache[tcl_lib] = dylib
    return dylib


def _mac_nsview_lookup(dylib: CDLL):
    """Return ``TkMacOSXGetRootControl`` from *dylib*."""
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
        dylib = _mac_tk_dylib(tcl_lib)
        lookup = _mac_nsview_lookup(dylib)
        drawable = _mac_drawable(widget, dylib)
        nsview = lookup(c_void_p(drawable))
        if not nsview:
            raise RuntimeError("TkMacOSXGetRootControl returned NULL")
        handle = int(nsview)
        top = widget.winfo_toplevel()
        top_drawable = _mac_drawable(top, dylib)
        top_ns = lookup(c_void_p(top_drawable))
        root_relative = _mac_root_relative(handle=handle, top_ns=int(top_ns or 0))
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
    if sys.platform == "darwin":
        widget.update_idletasks()
        origin = _mac_embed_origin(widget)
        if origin is not None:
            return origin
    toplevel = widget.winfo_toplevel()
    return (
        widget.winfo_rootx() - toplevel.winfo_rootx(),
        widget.winfo_rooty() - toplevel.winfo_rooty(),
    )


def tk_embed_bounds(
    widget: tk.Misc, *, root_relative: bool
) -> tuple[int, int, int, int]:
    """Return ``(x, y, width, height)`` for ``set_bounds`` in embed-parent space."""
    require_tk_thread(widget)
    widget.update_idletasks()
    if sys.platform == "darwin" and root_relative:
        tcl_lib = widget.tk.call("info", "library")
        dylib = _mac_tk_dylib(tcl_lib)
        bounds = _mac_win_bounds(widget, dylib)
        if bounds is not None:
            return bounds
    x, y = tk_embed_origin(widget, root_relative=root_relative)
    return x, y, max(widget.winfo_width(), 1), max(widget.winfo_height(), 1)
