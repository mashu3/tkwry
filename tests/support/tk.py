"""Tk event-loop helpers and frame factories."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

import pytest

from tkwry import WebView

skip_linux_layout = pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI: Tk layout timing unreliable",
)

skip_linux_ci = pytest.mark.skipif(
    sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
    reason="WebKitGTK headless CI: best-effort on Linux in v0.0.x",
)


def is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def pump(root, *, steps: int = 80, delay_ms: int = 50) -> None:
    """Drive the Tk event loop for up to *steps* iterations."""
    for _ in range(steps):
        root.update_idletasks()
        root.update()
        if sys.platform == "linux":
            from tkwry._core import pump_events

            pump_events()
        root.after(delay_ms)
        root.update()


def wait_until(root, predicate: Callable[[], bool], *, steps: int = 100) -> bool:
    """Return True once *predicate* is truthy, else False after *steps*."""
    for _ in range(steps):
        if predicate():
            return True
        pump(root, steps=1, delay_ms=30)
    return predicate()


def wait_ready(root, web: WebView, *, pump_steps: int = 40) -> None:
    """Block until the native webview exists and pump follow-up events."""
    assert wait_until(root, lambda: web.native is not None)
    pump(root, steps=pump_steps)


def host_frame(root):
    """Pre-packed frame with a stable 400×280 geometry."""
    import tkinter as tk

    root.update_idletasks()
    frame = tk.Frame(root, width=400, height=280, bg="#222")
    frame.pack_propagate(False)
    frame.pack(fill="both", expand=True)
    root.update_idletasks()
    return frame


def bare_frame(root):
    """Unpacked host frame (for lifecycle / layout timing tests)."""
    import tkinter as tk

    return tk.Frame(root)


def layout_bare_frame(
    frame,
    *,
    width: int = 400,
    height: int = 300,
) -> None:
    """Pack an unpacked frame with stable geometry for layout-ready checks."""
    root = frame.winfo_toplevel()
    frame.pack_propagate(False)
    frame.configure(width=width, height=height)
    frame.pack(fill="both", expand=True)
    root.update_idletasks()
