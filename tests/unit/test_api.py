"""Package import, public API surface, and enum exports."""

from __future__ import annotations

import re
from pathlib import Path

import tkwry
from tkwry import (
    BridgeAllow,
    BridgeOrigins,
    Cookie,
    CreationFailedHandler,
    DownloadCompleteHandler,
    DownloadHandler,
    DragDropEvent,
    DragDropHandler,
    EvalCallback,
    EvalErrorHandler,
    IpcHandler,
    NavigationHandler,
    NewWindowHandler,
    NewWindowResponse,
    PageLoadEvent,
    PageLoadHandler,
    PermissionHandler,
    PermissionKind,
    PermissionResponse,
    TitleChangedHandler,
    WebSession,
    WebView,
    WebViewPhase,
)
from tkwry._core import WebView as NativeWebView

PUBLIC_TYPE_ALIASES = (
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
)

WEBVIEW_METHODS = (
    "bind",
    "destroy",
    "load_url",
    "load_html",
    "reload",
    "go_back",
    "go_forward",
    "can_go_back",
    "can_go_forward",
    "print",
    "set_zoom",
    "reset_zoom",
    "cookies",
    "cookies_for_url",
    "set_cookie",
    "delete_cookie",
    "clear_all_browsing_data",
    "eval_js",
    "eval_js_with_callback",
    "emit",
    "focus",
    "focus_parent",
    "set_background_color",
    "set_user_agent",
    "set_initialization_script",
    "open_devtools",
    "close_devtools",
    "is_devtools_open",
    "set_ipc_handler",
    "set_bridge_origins",
    "set_bridge_allow",
    "expose",
    "unexpose",
    "watch_app",
    "set_on_navigation",
    "set_on_page_load",
    "set_on_title_changed",
    "set_on_new_window",
    "set_drag_drop_handler",
    "set_on_download",
    "set_on_download_complete",
    "sync_bounds",
    "take_queue_drop_counts",
    "take_queue_drop_stats",
    "pack",
    "grid",
    "place",
    "when_ready",
    "when_failed",
    "wait_until_ready",
)

WEBVIEW_PROPERTIES = (
    "url",
    "native",
    "destroyed",
    "untrusted",
    "clipboard",
    "navigation_allow",
    "open_external",
    "download_allow",
    "csp",
    "coop",
    "corp",
    "bridge_origins",
    "bridge_allow",
    "ready",
    "phase",
    "creation_failed",
    "creation_error",
    "last_eval_error",
    "last_navigation_error",
    "last_download",
)


def _cargo_version() -> str:
    cargo = Path(__file__).resolve().parents[2] / "Cargo.toml"
    match = re.search(
        r'^version = "([^"]+)"',
        cargo.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None
    return match.group(1)


def test_version_matches_cargo_toml() -> None:
    assert tkwry.__version__ == _cargo_version()


def test_public_exports() -> None:
    assert WebView is not None
    assert NativeWebView is not None
    assert WebSession is not None
    assert Cookie is not None
    assert PageLoadEvent is not None
    assert DragDropEvent is not None
    assert NewWindowResponse is not None
    assert PermissionKind is not None
    assert PermissionResponse is not None
    assert WebViewPhase is not None
    assert tkwry.QueueDropCounts is not None
    assert tkwry.InFlightDownload is not None
    assert tkwry.WebViewNotReadyError is not None
    assert tkwry.WebViewCreationError is not None
    assert tkwry.WebViewDestroyedError is not None
    assert tkwry.WebViewTimeoutError is not None
    assert tkwry.WebViewNavigationError is not None
    assert tkwry.RpcTimeoutError is not None
    assert tkwry.RpcCancelledError is not None
    assert tkwry.RpcSerializationError is not None
    assert tkwry.TkwrySecurityWarning is not None
    assert tkwry.rpc_cancelled is not None
    assert tkwry.rpc_cancel_event is not None
    assert tkwry.open_in_browser is not None
    assert tkwry.unique_download_path is not None
    assert tkwry.configure_window is not None
    assert tkwry.DEFAULT_CSP is not None
    for alias in PUBLIC_TYPE_ALIASES:
        assert alias is not None


def test_webview_repr_states(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com")
    text = repr(web)
    assert "WebView" in text
    assert "phase=pre_create" in text
    assert "https://example.com" in text
    assert str(frame) in text

    web.destroy()
    assert "phase=destroyed" in repr(web)
    frame.destroy()


def test_webview_rejects_other_thread(tk_root) -> None:
    import threading
    import tkinter as tk

    from tkwry._parent import check_tk_thread_id

    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com")
    # Check the stored thread id only — do not call WebView methods from a
    # worker while holding the widget in that thread's frame (Linux abort).
    owner = web._tk_thread_id
    errors: list[str] = []

    def worker() -> None:
        try:
            check_tk_thread_id(owner)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert "thread" in errors[0].lower()
    assert owner == threading.get_ident()

    web.destroy()
    frame.destroy()


def test_webview_exposes_documented_members() -> None:
    for name in WEBVIEW_METHODS:
        assert callable(getattr(WebView, name, None)), name
    for name in WEBVIEW_PROPERTIES:
        assert isinstance(getattr(WebView, name, None), property), name


def test_py_typed_marker_exists() -> None:
    marker = Path(__file__).resolve().parents[2] / "tkwry" / "py.typed"
    assert marker.is_file()


def test_installed_package_ships_typing_and_rpc_bootstrap() -> None:
    """Wheel / editable install must include PEP 561 marker, stubs, JS bridge."""
    import tkwry
    from tkwry.ipc import RPC_BOOTSTRAP_JS

    root = Path(tkwry.__file__).resolve().parent
    assert (root / "py.typed").is_file()
    assert (root / "_core.pyi").is_file()
    assert "window.tkwry.call" in RPC_BOOTSTRAP_JS
    assert "window.tkwry.stream" in RPC_BOOTSTRAP_JS
    assert "window.tkwry.on" in RPC_BOOTSTRAP_JS


def test_page_load_event_members() -> None:
    assert PageLoadEvent.Started != PageLoadEvent.Finished
    assert PageLoadEvent.Started == PageLoadEvent.Started


def test_drag_drop_event_members() -> None:
    assert DragDropEvent.Enter != DragDropEvent.Drop
    assert DragDropEvent.Leave != DragDropEvent.Over


def test_new_window_response_members() -> None:
    assert NewWindowResponse.Allow != NewWindowResponse.Deny
