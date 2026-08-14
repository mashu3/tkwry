"""Post-``destroy()`` public API: ``WebViewDestroyedError``, no silent no-ops."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

import pytest
from unit.test_api import WEBVIEW_METHODS, WEBVIEW_PROPERTIES

from tkwry import NewWindowResponse, WebView, WebViewDestroyedError, WebViewPhase


def _ping() -> str:
    return "pong"


# Every public method in WEBVIEW_METHODS is either listed here (must raise)
# or in POST_DESTROY_ALLOWED (explicit non-raise).
POST_DESTROY_ACTIONS: dict[str, Callable[[WebView], object]] = {
    "bind": lambda w: w.bind("<<WebViewReady>>", lambda _e: None),
    "load_url": lambda w: w.load_url("https://example.com"),
    "load_html": lambda w: w.load_html("<p>x</p>"),
    "reload": lambda w: w.reload(),
    "go_back": lambda w: w.go_back(),
    "go_forward": lambda w: w.go_forward(),
    "can_go_back": lambda w: w.can_go_back(),
    "can_go_forward": lambda w: w.can_go_forward(),
    "print": lambda w: w.print(),
    "eval_js": lambda w: w.eval_js("1"),
    "eval_js_with_callback": lambda w: w.eval_js_with_callback("1", lambda _r: None),
    "emit": lambda w: w.emit("x"),
    "focus": lambda w: w.focus(),
    "focus_parent": lambda w: w.focus_parent(),
    "set_background_color": lambda w: w.set_background_color(0, 0, 0),
    "set_user_agent": lambda w: w.set_user_agent("tkwry-test"),
    "set_initialization_script": lambda w: w.set_initialization_script("void 0"),
    "open_devtools": lambda w: w.open_devtools(),
    "close_devtools": lambda w: w.close_devtools(),
    "is_devtools_open": lambda w: w.is_devtools_open(),
    "set_ipc_handler": lambda w: w.set_ipc_handler(lambda _m: None),
    "set_bridge_origins": lambda w: w.set_bridge_origins(["https://example.com"]),
    "set_bridge_allow": lambda w: w.set_bridge_allow(lambda _u: True),
    "expose": lambda w: w.expose(_ping),
    "unexpose": lambda w: w.unexpose("ping"),
    "watch_app": lambda w: w.watch_app(),
    "set_on_navigation": lambda w: w.set_on_navigation(lambda _u: True),
    "set_on_page_load": lambda w: w.set_on_page_load(lambda *_a: None),
    "set_on_title_changed": lambda w: w.set_on_title_changed(lambda _t: None),
    "set_on_new_window": lambda w: w.set_on_new_window(
        lambda _u: NewWindowResponse.Deny
    ),
    "set_drag_drop_handler": lambda w: w.set_drag_drop_handler(lambda *_a: None),
    "set_on_download": lambda w: w.set_on_download(lambda *_a: True),
    "set_on_download_complete": lambda w: w.set_on_download_complete(lambda *_a: None),
    "sync_bounds": lambda w: w.sync_bounds(),
    "pack": lambda w: w.pack(),
    "grid": lambda w: w.grid(),
    "place": lambda w: w.place(),
    "when_ready": lambda w: w.when_ready(lambda: None),
    "when_failed": lambda w: w.when_failed(lambda _e: None),
    "wait_until_ready": lambda w: w.wait_until_ready(timeout=0.05),
}

POST_DESTROY_ALLOWED = frozenset({"destroy", "take_queue_drop_counts"})

POST_DESTROY_READABLE_PROPERTIES = frozenset(
    {
        "destroyed",
        "untrusted",
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
    }
)

POST_DESTROY_RAISE_PROPERTIES = frozenset({"url", "native"})


def test_post_destroy_tables_match_public_surface() -> None:
    assert set(POST_DESTROY_ACTIONS) | POST_DESTROY_ALLOWED == set(WEBVIEW_METHODS)
    assert POST_DESTROY_READABLE_PROPERTIES | POST_DESTROY_RAISE_PROPERTIES == set(
        WEBVIEW_PROPERTIES
    )


@pytest.mark.parametrize("name", sorted(POST_DESTROY_ACTIONS))
def test_public_method_raises_after_destroy(tk_root, name: str) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>destroy-api</p>")
    web.destroy()
    with pytest.raises(WebViewDestroyedError, match=name):
        POST_DESTROY_ACTIONS[name](web)
    frame.destroy()


def test_destroy_is_idempotent_and_drop_counts_readable(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>destroy-api</p>")
    web.destroy()
    web.destroy()
    assert web.destroyed is True
    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0, 0)
    frame.destroy()


@pytest.mark.parametrize("name", sorted(POST_DESTROY_RAISE_PROPERTIES))
def test_property_raises_after_destroy(tk_root, name: str) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>destroy-api</p>")
    web.destroy()
    with pytest.raises(WebViewDestroyedError, match=name):
        getattr(web, name)
    frame.destroy()


@pytest.mark.parametrize("name", sorted(POST_DESTROY_READABLE_PROPERTIES))
def test_snapshot_property_readable_after_destroy(tk_root, name: str) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>destroy-api</p>")
    web.destroy()
    value = getattr(web, name)
    if name == "destroyed":
        assert value is True
    elif name == "phase":
        assert value in (WebViewPhase.DESTROYED, WebViewPhase.TEARING_DOWN)
    elif name == "ready":
        assert value is False
    frame.destroy()
