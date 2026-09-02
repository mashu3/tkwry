"""Context menu API — JS bridge + Tk Menu."""

from __future__ import annotations

import json
import sys
import tkinter as tk

import pytest

from tkwry import ContextMenuEvent, PageLoadEvent, WebView
from tkwry.context_menu import (
    CONTEXT_MENU_DISABLE_JS,
    CONTEXT_MENU_JS,
    merge_context_menu_script,
    normalize_context_menu_items,
    parse_context_menu_event,
)


def test_parse_context_menu_event_ok() -> None:
    event = parse_context_menu_event(
        json.dumps(
            {
                "__tkwry": "contextmenu",
                "x": 10,
                "y": 20,
                "page_x": 1,
                "page_y": 2,
                "link_url": "https://example.com/",
                "selected_text": "hi",
            }
        )
    )
    assert event == ContextMenuEvent(
        x=10,
        y=20,
        page_x=1,
        page_y=2,
        link_url="https://example.com/",
        selected_text="hi",
    )


def test_parse_context_menu_event_rejects_other() -> None:
    assert parse_context_menu_event('{"__tkwry":"rpc","id":"r1"}') is None
    assert parse_context_menu_event("not-json") is None


def test_normalize_context_menu_items() -> None:
    def go() -> None:
        return None

    items = normalize_context_menu_items([("Back", go), (None, None), ("Quit", go)])
    assert items is not None
    assert len(items) == 3
    assert items[1] == (None, None)


def test_normalize_context_menu_items_rejects_bad() -> None:
    with pytest.raises(TypeError):
        normalize_context_menu_items("Back")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        normalize_context_menu_items([])
    with pytest.raises(TypeError):
        normalize_context_menu_items([("Back", None)])


def test_merge_context_menu_script() -> None:
    assert merge_context_menu_script(None, context_menu_enabled=False) is None
    only = merge_context_menu_script(None, context_menu_enabled=True)
    assert only == CONTEXT_MENU_JS
    merged = merge_context_menu_script("void 0;", context_menu_enabled=True)
    assert merged is not None
    assert merged.startswith("void 0;")
    assert CONTEXT_MENU_JS in merged


def test_set_context_menu_handler_delivers(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    seen: list[ContextMenuEvent] = []
    web.set_context_menu_handler(seen.append)
    web._deliver_context_menu_event(
        ContextMenuEvent(x=5, y=6, link_url="https://a.example/")
    )
    assert len(seen) == 1
    assert seen[0].x == 5
    assert seen[0].link_url == "https://a.example/"
    web.destroy()
    frame.destroy()


def test_handler_takes_priority_over_items(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    clicks: list[str] = []
    web.set_context_menu([("Back", lambda: clicks.append("item"))])
    web.set_context_menu_handler(lambda _e: clicks.append("handler"))
    web._deliver_context_menu_event(ContextMenuEvent(x=1, y=1))
    assert clicks == ["handler"]
    web.destroy()
    frame.destroy()


def test_context_menu_ctor_matches_setter(tk_root) -> None:
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    items = [("Ping", lambda: None)]
    web_a = WebView(
        frame_a,
        html="<p>a</p>",
        default_context_menus=False,
        context_menu=items,
    )
    web_b = WebView(frame_b, html="<p>b</p>", default_context_menus=False)
    web_b.set_context_menu(items)
    assert web_a._context_menu_items is not None
    assert web_b._context_menu_items is not None
    assert len(web_a._context_menu_items) == len(web_b._context_menu_items)
    web_a.destroy()
    web_b.destroy()
    frame_a.destroy()
    frame_b.destroy()


def test_ipc_listening_when_context_menu_set(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    assert web._ipc_listening_wanted() is False
    web.set_context_menu([("X", lambda: None)])
    assert web._ipc_listening_wanted() is True
    assert web._page_load_listening_wanted() is True
    assert web._needs_event_poll() is True
    script = web._effective_initialization_script()
    assert script is None or "__tkwryContextMenu" not in script
    web.destroy()
    frame.destroy()


def test_context_menu_bridge_reinjected_on_page_load_started(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    injected: list[str] = []

    class _Native:
        def drain_page_load_events(self):
            return [(PageLoadEvent.Started, "https://example.com/next")]

        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            injected.append(script)

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web._webview = _Native()  # type: ignore[assignment]
    web.set_context_menu([("Copy", lambda: None)])
    web._context_menu_bridge_injected = True
    injected.clear()

    web._deliver_page_load_events()

    assert injected == [CONTEXT_MENU_JS]
    web.destroy()
    frame.destroy()


def test_clearing_context_menu_removes_bridge(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    evals: list[str] = []

    class _Native:
        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            evals.append(script)

        def destroy(self) -> None:
            pass

    web._webview = _Native()  # type: ignore[assignment]
    web.set_context_menu([("Copy", lambda: None)])
    web._context_menu_bridge_injected = True
    evals.clear()

    web.set_context_menu(None)

    assert evals == [CONTEXT_MENU_DISABLE_JS]
    assert web._context_menu_bridge_injected is False
    assert web._page_load_listening_wanted() is True
    assert web._context_menu_active() is False
    web.destroy()
    frame.destroy()


def test_clearing_context_menu_handler_removes_bridge_when_no_items(
    tk_root,
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    evals: list[str] = []

    class _Native:
        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            evals.append(script)

        def destroy(self) -> None:
            pass

    web._webview = _Native()  # type: ignore[assignment]
    web.set_context_menu_handler(lambda _e: None)
    web._context_menu_bridge_injected = True
    evals.clear()

    web.set_context_menu_handler(None)

    assert evals == [CONTEXT_MENU_DISABLE_JS]
    assert web._context_menu_bridge_injected is False
    web.destroy()
    frame.destroy()


def test_context_menu_disabled_on_started_when_inactive(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    evals: list[str] = []

    class _Native:
        def drain_page_load_events(self):
            return [(PageLoadEvent.Started, "https://example.com/next")]

        def set_ipc_listening(self, enabled: bool) -> None:
            pass

        def set_page_load_listening(self, enabled: bool) -> None:
            pass

        def eval_js(self, script: str) -> None:
            evals.append(script)

        def destroy(self) -> None:
            pass

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web._webview = _Native()  # type: ignore[assignment]
    web.set_context_menu([("Copy", lambda: None)])
    web.set_context_menu(None)
    evals.clear()

    web._deliver_page_load_events()

    assert evals == [CONTEXT_MENU_DISABLE_JS]
    web.destroy()
    frame.destroy()


def test_deliver_via_ipc_message_path(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    seen: list[ContextMenuEvent] = []
    web.set_context_menu_handler(seen.append)
    payload = json.dumps(
        {
            "__tkwry": "contextmenu",
            "x": 9,
            "y": 8,
            "page_x": 0,
            "page_y": 0,
            "link_url": None,
            "selected_text": None,
        }
    )
    # Simulate post-parse delivery (native drain mocked via direct call).
    event = parse_context_menu_event(payload)
    assert event is not None
    web._deliver_context_menu_event(event)
    assert seen[0].x == 9
    web.destroy()
    frame.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only contract")
def test_windows_requires_default_context_menus_false_after_create(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=True)
    web._webview = object()  # pretend native exists
    with pytest.raises(ValueError, match="default_context_menus=False"):
        web.set_context_menu([("X", lambda: None)])
    web._webview = None
    web.destroy()
    frame.destroy()


def test_windows_forces_false_before_create(tk_root) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only force path")
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        html="<p>x</p>",
        default_context_menus=True,
        context_menu=[("X", lambda: None)],
    )
    assert web.default_context_menus is False
    web.destroy()
    frame.destroy()
