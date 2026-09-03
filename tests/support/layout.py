"""Native bounds measurement helpers."""

from __future__ import annotations

import sys

from tkwry import WebView
from tkwry._parent import tk_embed_bounds, tk_embed_origin, tk_embed_parent

BOUNDS_TOLERANCE = 4


def expected_bounds(frame) -> tuple[float, float, float, float]:
    frame.update_idletasks()
    embed = tk_embed_parent(frame)
    if sys.platform == "darwin" and embed.root_relative:
        x, y, width, height = tk_embed_bounds(frame, root_relative=True)
        return (float(x), float(y), float(width), float(height))
    x, y = tk_embed_origin(frame, root_relative=embed.root_relative)
    width = max(frame.winfo_width(), 1)
    height = max(frame.winfo_height(), 1)
    return (x, y, width, height)


def bounds_close(
    records: list[tuple[float, float, float, float]],
    expected: tuple[float, float, float, float],
    *,
    tolerance: int = BOUNDS_TOLERANCE,
) -> bool:
    if not records:
        return False
    actual = records[-1]
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def attach_bounds_recorder(
    web: WebView,
) -> list[tuple[float, float, float, float]]:
    """Record geometry each time ``_sync_bounds`` runs."""
    records: list[tuple[float, float, float, float]] = []
    original = web._sync_bounds

    def record() -> None:
        if original():
            web._frame.update_idletasks()
            records.append(expected_bounds(web._frame))

    web._sync_bounds = record  # type: ignore[method-assign]
    return records
