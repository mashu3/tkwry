"""Tkinter WebView widget backed by wry."""

import sys

from tkwry._version import __version__
from tkwry.exceptions import (
    WebViewCreationError,
    WebViewDestroyedError,
    WebViewNotReadyError,
)

_LINUX_CORE_BUILD_HINT = (
    "tkwry publishes pre-built wheels for Windows and macOS only. "
    "On Linux, install WebKitGTK development packages and build from "
    "source (see README)."
)


def _is_missing_core_extension(exc: BaseException) -> bool:
    """Return whether *exc* indicates the native ``tkwry._core`` extension is absent."""
    if isinstance(exc, ModuleNotFoundError):
        return exc.name == "tkwry._core"
    if isinstance(exc, ImportError):
        if getattr(exc, "name", None) == "tkwry._core":
            return True
        if exc.__cause__ is not None:
            return _is_missing_core_extension(exc.__cause__)
    return False


def _reraise_linux_core_build_hint(exc: BaseException) -> None:
    if sys.platform.startswith("linux") and _is_missing_core_extension(exc):
        raise ImportError(_LINUX_CORE_BUILD_HINT) from exc
    raise exc


try:
    from tkwry._core import DragDropEvent, NewWindowResponse, PageLoadEvent
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
except ImportError as exc:
    _reraise_linux_core_build_hint(exc)

if sys.platform == "darwin":
    from tkwry._core import disable_macos_automatic_window_tabbing

    disable_macos_automatic_window_tabbing()

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
