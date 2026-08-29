"""Tkinter WebView widget backed by wry."""

import sys

from tkwry._app import DEFAULT_CSP
from tkwry._origin import open_in_browser, unique_download_path
from tkwry._version import __version__
from tkwry.exceptions import (
    RpcCancelledError,
    RpcSerializationError,
    RpcTimeoutError,
    TkwrySecurityWarning,
    WebViewCreationError,
    WebViewDestroyedError,
    WebViewNavigationError,
    WebViewNotReadyError,
    WebViewTimeoutError,
)
from tkwry.ipc import rpc_cancel_event, rpc_cancelled
from tkwry.window import configure_window

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
    from tkwry._core import (
        Cookie,
        DragDropEvent,
        NewWindowResponse,
        PageLoadEvent,
        PermissionKind,
        PermissionResponse,
    )
    from tkwry.session import WebSession
    from tkwry.webview import (
        BridgeAllow,
        BridgeOrigins,
        CreationFailedHandler,
        DownloadCompleteHandler,
        DownloadHandler,
        DragDropHandler,
        EvalCallback,
        EvalErrorHandler,
        IpcHandler,
        NavigationHandler,
        NewWindowHandler,
        PageLoadHandler,
        PermissionHandler,
        TitleChangedHandler,
        WebView,
        WebViewPhase,
    )
except ImportError as exc:
    _reraise_linux_core_build_hint(exc)

if sys.platform == "darwin":
    from tkwry._macos import install_automatic_window_tabbing_disable

    install_automatic_window_tabbing_disable()

__all__ = [
    "DEFAULT_CSP",
    "BridgeAllow",
    "BridgeOrigins",
    "Cookie",
    "CreationFailedHandler",
    "DownloadCompleteHandler",
    "DownloadHandler",
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
    "PermissionHandler",
    "PermissionKind",
    "PermissionResponse",
    "TitleChangedHandler",
    "RpcCancelledError",
    "RpcSerializationError",
    "RpcTimeoutError",
    "TkwrySecurityWarning",
    "configure_window",
    "open_in_browser",
    "unique_download_path",
    "rpc_cancel_event",
    "rpc_cancelled",
    "WebSession",
    "WebView",
    "WebViewCreationError",
    "WebViewDestroyedError",
    "WebViewNavigationError",
    "WebViewNotReadyError",
    "WebViewTimeoutError",
    "WebViewPhase",
    "__version__",
]
