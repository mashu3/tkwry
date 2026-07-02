"""Tkinter WebView widget backed by wry."""

import sys

from tkwry._core import DragDropEvent, NewWindowResponse, PageLoadEvent
from tkwry._version import __version__
from tkwry.exceptions import WebViewDestroyedError, WebViewNotReadyError

try:
    from tkwry.webview import WebView
except ImportError as exc:
    if sys.platform.startswith("linux") and "_core" in str(exc):
        raise ImportError(
            "tkwry publishes pre-built wheels for Windows and macOS only. "
            "On Linux, install WebKitGTK development packages and build from "
            "source (see README)."
        ) from exc
    raise

__all__ = [
    "DragDropEvent",
    "NewWindowResponse",
    "PageLoadEvent",
    "WebView",
    "WebViewDestroyedError",
    "WebViewNotReadyError",
    "__version__",
]
