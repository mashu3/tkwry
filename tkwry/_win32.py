"""Windows-only helpers for native WebView child HWND stacking."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

HWND_TOP = wintypes.HWND(0)
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _child_hwnds(parent_hwnd: int) -> list[int]:
    found: list[int] = []

    @EnumChildProc
    def _callback(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        found.append(int(hwnd))
        return True

    user32.EnumChildWindows(wintypes.HWND(parent_hwnd), _callback, 0)
    return found


def raise_frame_webview(frame_hwnd: int) -> None:
    """Raise WebView2 container HWNDs so they match Tk frame stacking."""
    for hwnd in _child_hwnds(frame_hwnd):
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            HWND_TOP,
            0,
            0,
            0,
            0,
            SWP_FLAGS,
        )
