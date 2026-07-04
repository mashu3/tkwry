"""Tkinter WebView widget backed by wry."""

import sys

from tkwry._core import DragDropEvent, NewWindowResponse, PageLoadEvent
from tkwry._version import __version__
from tkwry.exceptions import WebViewDestroyedError, WebViewNotReadyError

try:
    from tkwry.webview import (
        DragDropHandler,
        EvalCallback,
        EvalErrorHandler,
        IpcHandler,
        NavigationHandler,
        NewWindowHandler,
        PageLoadHandler,
        TitleChangedHandler,
        WebView,
    )
except ImportError:
    err = sys.exc_info()[1]
    if err is not None and sys.platform.startswith("linux") and "_core" in str(err):
        raise ImportError(
            "tkwry publishes pre-built wheels for Windows and macOS only. "
            "On Linux, install WebKitGTK development packages and build from "
            "source (see README)."
        ) from err
    raise

__all__ = [
    "DragDropEvent",
    "DragDropHandler",
    "EvalCallback",
    "EvalErrorHandler",
    "IpcHandler",
    "NavigationHandler",
    "NewWindowHandler",
    "NewWindowResponse",
    "PageLoadEvent",
    "PageLoadHandler",
    "TitleChangedHandler",
    "WebView",
    "WebViewDestroyedError",
    "WebViewNotReadyError",
    "__version__",
]
