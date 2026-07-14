"""Windows-only helpers for native WebView child HWND stacking and WebView2."""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from tkwry.exceptions import WebViewCreationError

# Evergreen WebView2 Runtime client id (Microsoft Edge Update).
_WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

WEBVIEW2_MISSING_MESSAGE = (
    "Microsoft Edge WebView2 Runtime is not installed. "
    "tkwry requires WebView2 on Windows — there is no fallback engine. "
    "Install from "
    "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
)

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_FLAGS = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE


def _user32() -> Any:
    return ctypes.windll.user32


def _child_hwnds(parent_hwnd: int) -> list[int]:
    from ctypes import wintypes

    found: list[int] = []
    user32 = _user32()
    EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumChildProc
    def _callback(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        found.append(int(hwnd))
        return True

    user32.EnumChildWindows(wintypes.HWND(parent_hwnd), _callback, 0)
    return found


def raise_frame_webview(frame_hwnd: int) -> None:
    """Raise WebView2 container HWNDs so they match Tk frame stacking."""
    from ctypes import wintypes

    user32 = _user32()
    hwnd_top = wintypes.HWND(0)
    for hwnd in _child_hwnds(frame_hwnd):
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            hwnd_top,
            0,
            0,
            0,
            0,
            _SWP_FLAGS,
        )


def _registry_has_webview2_pv() -> bool:
    """True when EdgeUpdate reports a non-empty WebView2 Runtime ``pv``."""
    if sys.platform != "win32":
        return False
    import winreg

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"),
    )
    for root, base in roots:
        path = f"{base}\\{_WEBVIEW2_CLIENT_GUID}"
        try:
            with winreg.OpenKey(root, path) as key:
                pv, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if isinstance(pv, str) and pv.strip() and pv.strip() != "0.0.0.0":
            return True
    return False


def is_webview2_runtime_available() -> bool:
    """Best-effort check for the Evergreen WebView2 Runtime (registry ``pv``)."""
    return _registry_has_webview2_pv()


def looks_like_webview2_missing(exc: BaseException | str) -> bool:
    """Heuristic for missing Runtime (not every ``WebView2 error:`` from wry)."""
    text = str(exc).lower()
    if "0x80070002" in text or "0x8007007e" in text:
        return True
    if "webview2" not in text and "edge" not in text:
        return False
    needles = (
        "file not found",
        "the system cannot find the file specified",
        "failed to find the webview2 runtime",
        "could not find edge",
    )
    return any(n in text for n in needles)


def webview2_missing_error(
    cause: BaseException | None = None,
) -> WebViewCreationError:
    """Hard-fail exception for a missing WebView2 Runtime (no soft warn)."""
    err = WebViewCreationError(WEBVIEW2_MISSING_MESSAGE)
    if cause is not None:
        err.__cause__ = cause
    return err
