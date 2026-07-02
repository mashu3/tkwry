"""Package import, public API surface, and enum exports."""

from __future__ import annotations

import re
from pathlib import Path

from tkwry._core import WebView as NativeWebView

import tkwry
from tkwry import (
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
    WebView,
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
    "set_background_color",
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
    assert tkwry.WebViewDestroyedError is not None


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
