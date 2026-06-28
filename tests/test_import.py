"""Smoke tests for package import and public API surface."""

from __future__ import annotations

import re
from pathlib import Path

from tkwry._core import WebView as NativeWebView

import tkwry
from tkwry import WebView

WEBVIEW_METHODS = (
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
    "pack",
    "grid",
    "place",
)

WEBVIEW_PROPERTIES = ("url", "native", "destroyed")


def _cargo_version() -> str:
    cargo = Path(__file__).resolve().parents[1] / "Cargo.toml"
    match = re.search(
        r'^version = "([^"]+)"',
        cargo.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None
    return match.group(1)


def test_version_matches_cargo_toml() -> None:
    assert tkwry.__version__ == _cargo_version()


def test_public_api() -> None:
    assert WebView is not None
    assert NativeWebView is not None
    assert tkwry.PageLoadEvent is not None
    assert tkwry.DragDropEvent is not None
    assert tkwry.NewWindowResponse is not None


def test_webview_exposes_documented_methods() -> None:
    for name in WEBVIEW_METHODS:
        assert callable(getattr(WebView, name, None)), name
    for name in WEBVIEW_PROPERTIES:
        assert isinstance(getattr(WebView, name, None), property), name


def test_py_typed_marker_exists() -> None:
    marker = Path(__file__).resolve().parents[1] / "tkwry" / "py.typed"
    assert marker.is_file()
