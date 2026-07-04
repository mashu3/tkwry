"""macOS integration helpers for keyboard-focus tests."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
import tkinter as tk
from collections.abc import Callable

if sys.platform != "darwin":
    raise ImportError("macos_input_helpers is macOS-only")

_cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))

kCGHIDEventTap = 0
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventKeyDown = 10
kCGEventKeyUp = 11
kCGMouseButtonLeft = 0
kVK_ANSI_A = 0


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


_cg.CGEventCreateMouseEvent.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    _CGPoint,
    ctypes.c_uint32,
]
_cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
_cg.CGEventCreateKeyboardEvent.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint16,
    ctypes.c_bool,
]
_cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
_cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_cg.CGEventPost.restype = None

_cgevent_probe: bool | None = None


def wry_point(root: tk.Misc, widget: tk.Misc) -> tuple[float, float]:
    """Map widget center to wry top-left coords (``set_bounds`` space)."""
    widget.update_idletasks()
    x = widget.winfo_rootx() - root.winfo_rootx() + widget.winfo_width() / 2
    y = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height() / 2
    return x, y


def center(widget: tk.Misc) -> tuple[int, int]:
    widget.update_idletasks()
    x = widget.winfo_rootx() + widget.winfo_width() // 2
    y = widget.winfo_rooty() + widget.winfo_height() // 2
    return x, y


def activate_window(root: tk.Misc) -> None:
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    root.attributes("-topmost", False)
    root.focus_force()
    root.update()


def post_screen_click(x: int, y: int) -> None:
    point = _CGPoint(float(x), float(y))
    down = _cg.CGEventCreateMouseEvent(
        None, kCGEventLeftMouseDown, point, kCGMouseButtonLeft
    )
    if not down:
        raise RuntimeError("CGEventCreateMouseEvent (down) failed")
    _cg.CGEventPost(kCGHIDEventTap, down)
    up = _cg.CGEventCreateMouseEvent(
        None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft
    )
    if not up:
        raise RuntimeError("CGEventCreateMouseEvent (up) failed")
    _cg.CGEventPost(kCGHIDEventTap, up)


def post_key_a() -> None:
    down = _cg.CGEventCreateKeyboardEvent(None, kVK_ANSI_A, True)
    if not down:
        raise RuntimeError("CGEventCreateKeyboardEvent (down) failed")
    _cg.CGEventPost(kCGHIDEventTap, down)
    up = _cg.CGEventCreateKeyboardEvent(None, kVK_ANSI_A, False)
    if not up:
        raise RuntimeError("CGEventCreateKeyboardEvent (up) failed")
    _cg.CGEventPost(kCGHIDEventTap, up)


def cgevent_clicks_reach_tk(root: tk.Misc, widget: tk.Misc) -> bool:
    global _cgevent_probe
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return False
    if _cgevent_probe is not None:
        return _cgevent_probe
    activate_window(root)
    root.focus_force()
    root.update()
    x, y = center(widget)
    post_screen_click(x, y)
    pump(root, seconds=0.25)
    _cgevent_probe = root.focus_get() is widget
    return _cgevent_probe


def wait_until(
    root: tk.Misc,
    predicate: Callable[[], bool],
    *,
    timeout: float = 3.0,
    interval: float = 0.02,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        if predicate():
            return True
        time.sleep(interval)
    return False


def pump(root: tk.Misc, *, seconds: float = 0.3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        time.sleep(0.01)


def wait_tcl_focus_leaves(
    root: tk.Misc,
    entry: tk.Misc,
    *,
    timeout: float = 0.25,
) -> float:
    """Return seconds until Tcl focus leaves *entry*; *timeout* if it never does."""
    from tkwry._macos import _mac_service_wakeup

    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        _mac_service_wakeup(root)
        root.update_idletasks()
        root.update()
        if root.focus_get() is not entry:
            return time.monotonic() - start
        time.sleep(0.005)
    return timeout


def rapid_entry_keypresses(entry: tk.Misc, root: tk.Misc, count: int) -> None:
    for _ in range(count):
        entry.event_generate("<KeyPress-a>")
        root.update_idletasks()


def type_a_on_entry(root: tk.Misc, entry: tk.Misc) -> None:
    if cgevent_clicks_reach_tk(root, entry):
        post_key_a()
        pump(root, seconds=0.25)
    else:
        entry.event_generate("<KeyPress-a>")
        root.update()


def click_entry(root: tk.Misc, entry: tk.Misc) -> None:
    if cgevent_clicks_reach_tk(root, entry):
        x, y = center(entry)
        post_screen_click(x, y)
        pump(root, seconds=0.25)
    else:
        entry.event_generate(
            "<Button-1>",
            x=entry.winfo_width() // 2,
            y=entry.winfo_height() // 2,
        )
        root.update()
