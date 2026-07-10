"""Tkinter WebView widget backed by wry."""

import sys

from tkwry._core import DragDropEvent, NewWindowResponse, PageLoadEvent
from tkwry._version import __version__
from tkwry.exceptions import (
    WebViewCreationError,
    WebViewDestroyedError,
    WebViewNotReadyError,
)

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
    if (
        sys.platform.startswith("linux")
        and sys.exc_info()[1] is not None
        and "_core" in str(sys.exc_info()[1])
    ):
        raise ImportError(
            "tkwry publishes pre-built wheels for Windows and macOS only. "
            "On Linux, install WebKitGTK development packages and build from "
            "source (see README)."
        ) from sys.exc_info()[1]
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
    "WebViewCreationError",
    "WebViewDestroyedError",
    "WebViewNotReadyError",
    "__version__",
]
