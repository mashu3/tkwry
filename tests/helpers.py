"""Shared helpers for Tk / WebView integration tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable


def is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def pump(root, *, steps: int = 80, delay_ms: int = 50) -> None:
    """Drive the Tk event loop for up to *steps* iterations."""
    gtk_pump = None
    if sys.platform == "linux":
        from tkwry._core import pump_events as gtk_pump

    for _ in range(steps):
        root.update_idletasks()
        root.update()
        if gtk_pump is not None:
            gtk_pump()
        root.after(delay_ms)
        root.update()


def wait_until(root, predicate: Callable[[], bool], *, steps: int = 100) -> bool:
    """Return True once *predicate* is truthy, else False after *steps*."""
    for _ in range(steps):
        if predicate():
            return True
        pump(root, steps=1, delay_ms=30)
    return predicate()
