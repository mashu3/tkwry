"""JavaScript viewport measurement helpers."""

from __future__ import annotations

import json

from support.tk import wait_until
from tkwry import WebView

VIEWPORT_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
    "<body style='margin:0;padding:0'><p>viewport</p></body></html>"
)
VIEWPORT_TOLERANCE = 8


def frame_client_size(frame) -> tuple[int, int]:
    frame.update_idletasks()
    return (max(frame.winfo_width(), 1), max(frame.winfo_height(), 1))


def viewport_matches_frame(
    viewport: tuple[int, int] | None,
    frame,
    *,
    tolerance: int = VIEWPORT_TOLERANCE,
) -> bool:
    if viewport is None:
        return False
    expected = frame_client_size(frame)
    return (
        abs(viewport[0] - expected[0]) <= tolerance
        and abs(viewport[1] - expected[1]) <= tolerance
    )


def read_viewport_via_callback(
    web: WebView, root, *, steps: int = 200
) -> tuple[int, int] | None:
    """Return viewport size via ``eval_js_with_callback``."""
    results: list[tuple[int, int]] = []

    def callback(raw: str) -> None:
        try:
            data = json.loads(raw)
            results.append((int(data["w"]), int(data["h"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass

    script = (
        "JSON.stringify({w: Math.round(window.innerWidth), "
        "h: Math.round(window.innerHeight)})"
    )
    per_try = max(steps // 10, 20)
    for _ in range(10):
        web.eval_js_with_callback(script, callback)
        if wait_until(root, lambda: len(results) > 0, steps=per_try):
            return results[-1]
    return None


def read_viewport(web: WebView, root, *, steps: int = 200) -> tuple[int, int] | None:
    """Return ``(innerWidth, innerHeight)`` from the loaded document via JS IPC."""
    results: list[tuple[int, int]] = []
    previous_handler = web._ipc_handler

    def capture(message: str) -> None:
        try:
            data = json.loads(message)
            if isinstance(data, dict) and "w" in data and "h" in data:
                results.append((int(data["w"]), int(data["h"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
        if previous_handler is not None:
            previous_handler(message)

    web.set_ipc_handler(capture)
    query = (
        "window.ipc.postMessage(JSON.stringify({"
        "w: Math.round(window.innerWidth), "
        "h: Math.round(window.innerHeight)"
        "}))"
    )

    per_try = max(steps // 10, 20)
    for _ in range(10):
        web.eval_js(query)
        if wait_until(root, lambda: len(results) > 0, steps=per_try):
            return results[-1]
    return None
