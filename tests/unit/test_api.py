"""Package import, public API surface, and enum exports."""

from __future__ import annotations

import re
from pathlib import Path

import tkwry
from tkwry import (
    DragDropEvent,
    DragDropHandler,
    EvalCallback,
    IpcHandler,
    NavigationHandler,
    NewWindowHandler,
    NewWindowResponse,
    PageLoadEvent,
    PageLoadHandler,
    TitleChangedHandler,
    WebView,
)
from tkwry._core import WebView as NativeWebView

PUBLIC_TYPE_ALIASES = (
    DragDropHandler,
    EvalCallback,
    IpcHandler,
    NavigationHandler,
    NewWindowHandler,
    PageLoadHandler,
    TitleChangedHandler,
)

WEBVIEW_METHODS = (
    "bind",
    "destroy",
    "load_url",
    "load_html",
    "reload",
    "eval_js",
    "eval_js_with_callback",
    "focus",
    "focus_parent",
    "set_background_color",
    "set_user_agent",
    "set_initialization_script",
    "open_devtools",
    "close_devtools",
    "is_devtools_open",
    "set_ipc_handler",
    "set_on_navigation",
    "set_on_page_load",
    "set_on_title_changed",
    "set_on_new_window",
    "set_drag_drop_handler",
    "sync_bounds",
    "take_queue_drop_counts",
    "pack",
    "grid",
    "place",
    "when_ready",
    "wait_until_ready",
)

WEBVIEW_PROPERTIES = ("url", "native", "destroyed", "ready")


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
    assert PageLoadEvent is not None
    assert DragDropEvent is not None
    assert NewWindowResponse is not None
    assert tkwry.WebViewNotReadyError is not None
    assert tkwry.WebViewCreationError is not None
    assert tkwry.WebViewDestroyedError is not None
    for alias in PUBLIC_TYPE_ALIASES:
        assert alias is not None


def test_webview_repr_states(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com")
    text = repr(web)
    assert "WebView" in text
    assert "pending" in text
    assert "https://example.com" in text
    assert str(frame) in text

    web.destroy()
    assert "destroyed" in repr(web)
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


def test_page_load_event_members() -> None:
    assert PageLoadEvent.Started != PageLoadEvent.Finished
    assert PageLoadEvent.Started == PageLoadEvent.Started


def test_drag_drop_event_members() -> None:
    assert DragDropEvent.Enter != DragDropEvent.Drop
    assert DragDropEvent.Leave != DragDropEvent.Over


def test_new_window_response_members() -> None:
    assert NewWindowResponse.Allow != NewWindowResponse.Deny
