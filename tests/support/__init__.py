"""Shared test utilities for tkwry."""

from support.layout import attach_bounds_recorder, bounds_close, expected_bounds
from support.tk import (
    bare_frame,
    host_frame,
    pump,
    wait_ready,
    wait_until,
)
from support.viewport import (
    VIEWPORT_HTML,
    VIEWPORT_TOLERANCE,
    read_viewport,
    read_viewport_via_callback,
    viewport_matches_frame,
)

__all__ = [
    "VIEWPORT_HTML",
    "VIEWPORT_TOLERANCE",
    "attach_bounds_recorder",
    "bare_frame",
    "bounds_close",
    "expected_bounds",
    "host_frame",
    "pump",
    "read_viewport",
    "read_viewport_via_callback",
    "viewport_matches_frame",
    "wait_ready",
    "wait_until",
]
