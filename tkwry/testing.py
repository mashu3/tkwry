"""Test helpers for driving Tk / WebView without ad-hoc polling loops.

These are small wrappers around the same patterns used by the integration
suite. Prefer them in app tests over reinventing ``wait_until``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from tkwry import WebView


def pump(root, *, steps: int = 80, delay_ms: int = 50) -> None:
    """Drive the Tk event loop for up to *steps* iterations."""
    for _ in range(steps):
        root.update_idletasks()
        root.update()
        if sys.platform == "linux":
            from tkwry._core import ensure_gtk_init, pump_events

            ensure_gtk_init()
            pump_events()
        root.after(delay_ms)
        root.update()


def wait_until(
    root,
    predicate: Callable[[], bool],
    *,
    steps: int = 100,
    delay_ms: int = 30,
) -> bool:
    """Return True once *predicate* is truthy, else False after *steps*."""
    for _ in range(steps):
        if predicate():
            return True
        pump(root, steps=1, delay_ms=delay_ms)
    return bool(predicate())


def wait_ready(
    root,
    web: WebView,
    *,
    timeout: float = 10.0,
    pump_steps: int = 40,
) -> None:
    """Block until :meth:`~tkwry.WebView.wait_until_ready` and pump follow-up events."""
    assert web.wait_until_ready(timeout=timeout), "WebView did not become ready"
    pump(root, steps=pump_steps)


def wait_eval(
    root,
    web: WebView,
    script: str,
    *,
    steps: int = 200,
) -> str | None:
    """Evaluate *script* with a callback and wait for the first result string."""
    results: list[str] = []

    def _capture(value: str) -> None:
        results.append(value)

    web.eval_js_with_callback(script, _capture)
    if not wait_until(root, lambda: bool(results), steps=steps):
        return None
    return results[0]


def wait_title(
    root,
    web: WebView,
    expected_substr: str,
    *,
    steps: int = 400,
) -> bool:
    """Poll ``document.title`` until it contains *expected_substr*."""
    titles: list[str] = []

    def _ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any(expected_substr in str(t) for t in titles)

    return wait_until(root, _ready, steps=steps)
