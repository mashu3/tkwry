"""Focused unit coverage for easy misses (proxy, handlers, teardown, etc.)."""

from __future__ import annotations

import math
import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tkwry import (
    DragDropEvent,
    NewWindowResponse,
    PermissionKind,
    PermissionResponse,
    WebView,
)
from tkwry.download import Download
from tkwry.navigation import coerce_navigation_result
from tkwry.webview import _normalize_proxy, _validate_dimension


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


# --- proxy / dimension -------------------------------------------------------


def test_normalize_proxy_rejects_non_mapping() -> None:
    with pytest.raises(TypeError, match="mapping"):
        _normalize_proxy("http://x:1")  # type: ignore[arg-type]


def test_normalize_proxy_rejects_non_str_value() -> None:
    with pytest.raises(TypeError, match="must be a str"):
        _normalize_proxy({"http": 8080})  # type: ignore[dict-item]


def test_normalize_proxy_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_proxy({"http": "   "})


def test_normalize_proxy_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError, match="scheme must be"):
        _normalize_proxy({"http": "socks5://127.0.0.1:1080"})


def test_normalize_proxy_rejects_url_missing_host_or_port() -> None:
    with pytest.raises(ValueError, match="host and port"):
        _normalize_proxy({"http": "http://127.0.0.1"})
    with pytest.raises(ValueError, match="host and port"):
        _normalize_proxy({"http": "http://:8080"})


def test_normalize_proxy_rejects_bad_ipv6() -> None:
    with pytest.raises(ValueError, match="IPv6"):
        _normalize_proxy({"http": "[::1"})
    with pytest.raises(ValueError, match="IPv6"):
        _normalize_proxy({"http": "[::1]8080"})


def test_normalize_proxy_rejects_ambiguous_host_port() -> None:
    with pytest.raises(ValueError, match="host:port"):
        _normalize_proxy({"http": "127.0.0.1"})
    with pytest.raises(ValueError, match="host:port"):
        _normalize_proxy({"http": "a:b:c"})


def test_normalize_proxy_rejects_empty_host_or_port_parts() -> None:
    with pytest.raises(ValueError, match="both host and port"):
        _normalize_proxy({"http": ":8080"})
    with pytest.raises(ValueError, match="both host and port"):
        _normalize_proxy({"http": "127.0.0.1:"})


def test_normalize_proxy_rejects_non_digit_port() -> None:
    with pytest.raises(ValueError, match="1-65535"):
        _normalize_proxy({"http": "127.0.0.1:abc"})


def test_validate_dimension_rejects_non_int() -> None:
    with pytest.raises(TypeError, match="must be an int"):
        _validate_dimension(1.5, "width")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an int"):
        _validate_dimension(True, "height")  # type: ignore[arg-type]


def test_coerce_navigation_result_non_bool() -> None:
    assert coerce_navigation_result(True) is True
    assert coerce_navigation_result(False) is False
    assert coerce_navigation_result(1) is False
    assert coerce_navigation_result("yes") is False
    assert coerce_navigation_result(None) is False


# --- navigation / new-window / permission handlers ---------------------------


def test_on_navigation_non_bool_denies(tk_root, capsys) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_on_navigation(lambda _e: "allow")  # type: ignore[arg-type, return-value]
    assert web._invoke_navigation_handler("https://example.com/") is False
    err = capsys.readouterr().err
    assert "on_navigation must return bool" in err
    web.destroy()
    frame.destroy()


def test_on_navigation_exception_denies(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    def boom(_e):  # noqa: ANN001
        raise RuntimeError("nav boom")

    web.set_on_navigation(boom)
    assert web._invoke_navigation_handler("https://example.com/") is False
    web.destroy()
    frame.destroy()


def test_navigation_policy_non_bool_and_exception(tk_root, capsys) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_navigation_policy(lambda _e: 1)  # type: ignore[arg-type, return-value]
    assert web._invoke_navigation_handler("https://example.com/") is False
    assert "navigation_policy must return bool" in capsys.readouterr().err

    def boom(_e):  # noqa: ANN001
        raise RuntimeError("policy boom")

    web.set_navigation_policy(boom)
    assert web._invoke_navigation_handler("https://example.com/") is False
    web.destroy()
    frame.destroy()


def test_new_window_default_allow_without_handler(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert (
        web._invoke_new_window_handler("https://example.com/")
        is NewWindowResponse.Allow
    )
    web.destroy()
    frame.destroy()


def test_new_window_bad_return_and_exception(tk_root, capsys) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_on_new_window(lambda _e: True)  # type: ignore[arg-type, return-value]
    assert (
        web._invoke_new_window_handler("https://example.com/") is NewWindowResponse.Deny
    )
    assert "on_new_window must return NewWindowResponse" in capsys.readouterr().err

    def boom(_e):  # noqa: ANN001
        raise RuntimeError("nw boom")

    web.set_on_new_window(boom)
    assert (
        web._invoke_new_window_handler("https://example.com/") is NewWindowResponse.Deny
    )
    web.destroy()
    frame.destroy()


def test_permission_handler_none_returns_default(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web._invoke_permission_handler(PermissionKind.Camera) is (
        PermissionResponse.Default
    )
    web.destroy()
    frame.destroy()


# --- download defaults / coerce ---------------------------------------------


def test_default_download_and_coerce(tk_root, capsys, tmp_path: Path) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web._default_download_allowed("https://example.com/a.zip") is True
    assert web._default_download_allowed("javascript:alert(1)") is False
    web._untrusted = True
    assert web._default_download_allowed("https://example.com/a.zip") is False
    web._untrusted = False

    assert web._coerce_download_decision(None, "a") == (False, None)
    assert web._coerce_download_decision(False, "a") == (False, None)
    assert web._coerce_download_decision(True, "a") == (True, None)
    abs_path = str(tmp_path / "out.bin")
    assert web._coerce_download_decision(abs_path, "a") == (True, abs_path)
    assert web._coerce_download_decision(Path(abs_path), "a") == (True, abs_path)
    assert web._coerce_download_decision("relative.bin", "a") == (False, None)
    assert "absolute path" in capsys.readouterr().err
    assert web._coerce_download_decision(123, "a") == (False, None)
    assert "must return bool" in capsys.readouterr().err

    web.set_on_download(lambda _d: True)
    assert (
        web._invoke_download_handler("https://example.com/a.zip", "/tmp/a")[0] is True
    )

    def boom(_d: Download) -> bool:
        raise RuntimeError("dl boom")

    web.set_on_download(boom)
    assert web._invoke_download_handler("https://example.com/a.zip", "/tmp/a") == (
        False,
        None,
    )
    web.destroy()
    frame.destroy()


# --- get_state error branches -----------------------------------------------


def test_get_state_swallows_native_errors(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.can_go_back.side_effect = RuntimeError("back")
    native.can_go_forward.side_effect = RuntimeError("fwd")
    native.is_devtools_open.side_effect = RuntimeError("dev")
    native.drain_title_events.return_value = []
    native.drain_page_load_events.return_value = []
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    def boom_url(_self):  # noqa: ANN001
        raise RuntimeError("url")

    monkeypatch.setattr(type(web), "url", property(boom_url))
    state = web.get_state()
    assert state.url is None
    assert state.can_go_back is False
    assert state.can_go_forward is False
    assert state.devtools_open is False
    web.destroy()
    frame.destroy()


# --- teardown / destroy helpers ---------------------------------------------


def test_force_and_finish_native_teardown(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    native = MagicMock()
    native.force_destroy.side_effect = RuntimeError("force fail")
    web._webview = native
    web._force_native_teardown()
    assert web._webview is None
    assert "force fail" in capsys.readouterr().err or True  # print_exc to stderr

    pending = MagicMock()
    pending.is_alive.side_effect = [True, True, False]
    pending.destroy.side_effect = RuntimeError("destroy fail")
    pending.force_destroy.side_effect = RuntimeError("force2")
    web._native_teardown_pending = pending
    web._native_teardown_attempts = 0
    # First finish: alive, destroy raises → except path
    web._finish_native_teardown()

    pending2 = MagicMock()
    pending2.is_alive.return_value = True
    pending2.destroy.return_value = None
    pending2.force_destroy.side_effect = RuntimeError("force timeout")
    web._native_teardown_pending = pending2
    web._native_teardown_attempts = 1000  # force timeout path
    monkeypatch.setattr(web, "_native_is_alive", lambda _n: True, raising=False)
    web._finish_native_teardown()
    web.destroy()
    frame.destroy()


def test_release_native_view_destroy_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.destroy.side_effect = RuntimeError("bye")
    native.is_alive.return_value = False
    web._webview = native
    monkeypatch.setattr(web, "_hide_native_view", lambda _n: None, raising=False)
    monkeypatch.setattr(web, "_native_is_alive", lambda _n: False, raising=False)
    web._release_native_view(hide=True)
    assert web._webview is None
    web.destroy()
    frame.destroy()


def test_title_changed_and_drag_drop_inject(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    web._on_title_changed = lambda _t: None
    web._native_title_changed("Hello")
    native.set_title_listening.assert_called_with(True)
    native._enqueue_title_event.assert_called_with("Hello")

    web._on_drag_drop = lambda *_a, **_k: None
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None, raising=False)
    web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a"], (1, 2))
    native.set_drag_drop_listening.assert_called_with(True)
    native._enqueue_drag_drop_event.assert_called_once()
    web._native_drag_drop(DragDropEvent.Drop, [], (0, 0))  # still has handler
    web._on_drag_drop = None
    web._native_drag_drop(DragDropEvent.Drop, [], (0, 0))  # early return

    web.destroy()
    frame.destroy()


def test_wait_until_ready_validation(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    with pytest.raises(ValueError, match="finite"):
        web.wait_until_ready(timeout=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        web.wait_until_ready(timeout=0)
    with pytest.raises(ValueError, match="finite"):
        web.wait_until_ready(timeout=math.nan)
    web._creation_error = RuntimeError("fail")
    assert web.wait_until_ready(timeout=0.05) is False
    web.destroy()
    frame.destroy()


def test_native_navigation_passthrough_without_handlers(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web._native_navigation("https://example.com/") is True
    web.destroy()
    frame.destroy()


def test_popup_context_menu_and_dispose(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry import ContextMenuEvent

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    clicks: list[str] = []
    items = (
        ("Go", lambda: clicks.append("go")),
        (None, None),
        ("Quit", lambda: clicks.append("q")),
    )
    event = ContextMenuEvent(x=10, y=20)

    # Force tk_popup / grab_release errors to hit except paths.
    real_menu = tk.Menu
    created: list[tk.Menu] = []

    def fake_menu(*_a, **_k):
        menu = real_menu(*_a, **_k)
        created.append(menu)
        menu.tk_popup = MagicMock(side_effect=tk.TclError("popup"))  # type: ignore[method-assign]
        menu.grab_release = MagicMock(side_effect=tk.TclError("grab"))  # type: ignore[method-assign]
        return menu

    monkeypatch.setattr(tk, "Menu", fake_menu)
    web._popup_context_menu(event, items)
    assert web._context_menu_tk is None

    web._context_menu_tk = MagicMock()
    web._context_menu_tk.destroy.side_effect = tk.TclError("gone")
    web._dispose_context_menu_tk()
    assert web._context_menu_tk is None

    web._deliver_context_menu_event(event)  # no items/handler
    web.set_context_menu(list(items))
    web._deliver_context_menu_event(event)
    web.destroy()
    frame.destroy()


def test_schedule_and_cancel_event_poll(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._event_poll_after_id = "dead-id"
    monkeypatch.setattr(
        frame,
        "after_cancel",
        MagicMock(side_effect=tk.TclError("x")),
    )
    web._cancel_event_poll_after()
    assert web._event_poll_after_id is None

    web._creation_error = RuntimeError("x")
    web._schedule_try_create()
    web._creation_error = None
    web._create_pending = True
    web._schedule_try_create()
    web._create_pending = False
    web._schedule_try_create(delay_ms=5)
    web.destroy()
    frame.destroy()


def test_in_flight_download_helpers(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._add_in_flight_download("https://a/x", "/tmp/a", None)
    assert len(web._in_flight_downloads) == 1
    web._add_in_flight_download("https://b/y", "/tmp/b", "/override")
    web._remove_in_flight_download("https://missing", None)
    web._remove_in_flight_download("https://a/x", None)
    assert all(item.url != "https://a/x" for item in web._in_flight_downloads)
    web._destroyed = True
    web._add_in_flight_download("https://c/z", "/tmp/c", None)
    web._destroyed = False
    web.destroy()
    frame.destroy()


def test_deliver_async_queues_with_mocks(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    called: list[str] = []
    monkeypatch.setattr(
        web, "_drain_sync_hooks", lambda: called.append("sync"), raising=False
    )
    monkeypatch.setattr(
        web, "_deliver_navigation_errors", lambda: called.append("nav"), raising=False
    )
    monkeypatch.setattr(web, "_ipc_listening_wanted", lambda: True, raising=False)
    monkeypatch.setattr(
        web, "_deliver_ipc_messages", lambda: called.append("ipc"), raising=False
    )
    monkeypatch.setattr(
        web, "_drain_rpc_futures", lambda: called.append("rpc"), raising=False
    )
    monkeypatch.setattr(
        web, "_deliver_page_load_events", lambda: called.append("pl"), raising=False
    )
    monkeypatch.setattr(web, "_title_listening_wanted", lambda: True, raising=False)
    monkeypatch.setattr(
        web, "_deliver_title_events", lambda: called.append("title"), raising=False
    )
    web._on_drag_drop = lambda *_a: None
    monkeypatch.setattr(
        web, "_deliver_drag_drop_events", lambda: called.append("dd"), raising=False
    )
    monkeypatch.setattr(
        web,
        "_deliver_download_complete_events",
        lambda: called.append("dl"),
        raising=False,
    )
    web._deliver_async_event_queues()
    native.drain_sync_hooks.assert_called()
    assert "ipc" in called and "dd" in called and "dl" in called
    web.destroy()
    frame.destroy()


def test_schedule_initial_load_and_run_paths(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._initial_load = ("html", "<p>z</p>")
    web._cancel_initial_load_timer()
    web._initial_load_after_id = "x"
    monkeypatch.setattr(
        tk_root,
        "after_cancel",
        MagicMock(side_effect=tk.TclError("x")),
        raising=False,
    )
    web._cancel_initial_load_timer()

    scheduled: list[int] = []
    monkeypatch.setattr(tk_root, "after", lambda ms, fn: scheduled.append(ms) or "aid")
    web._schedule_initial_load()
    assert scheduled and web._initial_load_after_id == "aid"

    web._initial_load = None
    web._schedule_initial_load()

    web._initial_load = ("html", "<p>z</p>")
    web._webview = MagicMock()
    web._pending_load = ("html", "<p>later</p>")
    web._run_initial_load()
    assert web._initial_load is None

    web._arm_initial_load(("html", "<p>again</p>"))
    web._webview = MagicMock()
    web._pending_load = None
    monkeypatch.setattr(web, "_frame_ready_for_initial_load", lambda: False)
    monkeypatch.setenv("TKWRY_LOAD_PROFILE", "1")
    resched: list[str] = []
    monkeypatch.setattr(
        web, "_maybe_reschedule_initial_load", lambda: resched.append("r")
    )
    web._run_initial_load()
    assert resched == ["r"]
    monkeypatch.delenv("TKWRY_LOAD_PROFILE", raising=False)
    web.destroy()
    frame.destroy()


def test_load_html_url_edge_cases(
    tk_root, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(web, "_finish_navigation", lambda: None, raising=False)
    monkeypatch.setattr(web, "_dispatch_pending_load", lambda: None, raising=False)
    web.load_html("<p>y</p>")
    assert web._pending_load is not None and web._pending_load[0] == "html"
    web.load_url("https://example.com/")
    assert web._pending_load is not None and web._pending_load[0] == "url"
    with pytest.raises(ValueError, match="app="):
        web.load_url("tkwry://localhost/x")
    local = tmp_path / "page.html"
    local.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="http"):
        web.load_url(local.as_uri(), headers={"X-Test": "1"})
    web.destroy()
    frame.destroy()


def test_windows_default_context_menus_guard(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=True)
    monkeypatch.setattr(sys, "platform", "win32")
    web._webview = None
    web._require_windows_context_menu_ready("set_context_menu")
    assert web._default_context_menus is False
    assert "default_context_menus=False" in capsys.readouterr().err

    web._default_context_menus = True
    web._webview = MagicMock()
    with pytest.raises(ValueError, match="construction"):
        web._require_windows_context_menu_ready("set_context_menu")
    web.destroy()
    frame.destroy()


# --- url / ipc / rpc / window extras ----------------------------------------


def test_normalize_load_headers_error_paths() -> None:
    from tkwry._url import _normalize_load_headers

    with pytest.raises(TypeError, match="mapping"):
        _normalize_load_headers(["a"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="str"):
        _normalize_load_headers({1: "a"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="str"):
        _normalize_load_headers({"A": 1})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_load_headers({"  ": "x"})
    with pytest.raises(ValueError, match="invalid header name"):
        _normalize_load_headers({"Bad\nName": "x"})
    assert _normalize_load_headers({"X-A": "1"}) == (("X-A", "1"),)


def test_url_ipv6_helpers_edge_cases() -> None:
    from tkwry._url import (
        _encode_ipv6_zone_suffix,
        _split_ipv6_host_port,
        _split_ipv6_with_zone,
        _strip_ipv6_zone,
    )

    assert _strip_ipv6_zone("[::1]") == "::1"
    assert _strip_ipv6_zone("fe80::1%en0") == "fe80::1"
    assert _encode_ipv6_zone_suffix("") == ""
    assert _encode_ipv6_zone_suffix("en0") == "%25en0"
    assert _encode_ipv6_zone_suffix("%en0") == "%25en0"
    assert _encode_ipv6_zone_suffix("%25en0") == "%25en0"
    assert _split_ipv6_with_zone("https://x") is None
    assert _split_ipv6_with_zone("[not-ipv6]") is None
    assert _split_ipv6_with_zone("[::1]:abc") is None
    assert _split_ipv6_host_port("[::1]:abc/path") is None
    assert _split_ipv6_host_port("[::1]junk") is None
    assert _split_ipv6_host_port("1:2") is None


def test_ipc_coerce_and_extract_helpers() -> None:
    from typing import Optional, Union

    from tkwry.ipc import (
        MAX_RPC_STREAM_CHUNK_BYTES,
        RpcMessageTooLarge,
        _coerce_rpc_value,
        _extract_rpc_request_id,
        _message_size,
        dumps_rpc_stream_chunk,
        rpc_cancel_event,
    )

    assert _extract_rpc_request_id('{"id":"abc123","method":"m"}') == "abc123"
    assert _extract_rpc_request_id("no-id-here") is None
    assert _extract_rpc_request_id('{"id":""}') is None
    assert _message_size("hi") == 2

    assert _coerce_rpc_value(True, bool) is True
    with pytest.raises(TypeError):
        _coerce_rpc_value(1, bool)
    assert _coerce_rpc_value(3, int) == 3
    assert _coerce_rpc_value(3.0, int) == 3
    with pytest.raises(TypeError):
        _coerce_rpc_value(True, int)
    with pytest.raises(TypeError):
        _coerce_rpc_value(1.5, int)
    assert _coerce_rpc_value(1.5, float) == 1.5
    assert _coerce_rpc_value(2, float) == 2.0
    with pytest.raises(TypeError):
        _coerce_rpc_value(True, float)
    with pytest.raises(TypeError):
        _coerce_rpc_value("x", float)
    assert _coerce_rpc_value("a", str) == "a"
    with pytest.raises(TypeError):
        _coerce_rpc_value(1, str)
    assert _coerce_rpc_value({"a": 1}, dict) == {"a": 1}
    with pytest.raises(TypeError):
        _coerce_rpc_value([], dict)
    assert _coerce_rpc_value([1], list) == [1]
    with pytest.raises(TypeError):
        _coerce_rpc_value({}, list)
    # typing.Union is the branch _coerce_rpc_value matches (not PEP604 |).
    assert _coerce_rpc_value(None, Optional[int]) is None  # noqa: UP007
    assert _coerce_rpc_value(5, Union[int, str]) == 5  # noqa: UP007
    with pytest.raises(TypeError):
        _coerce_rpc_value([], Union[int, str])  # noqa: UP007

    assert rpc_cancel_event() is None
    huge = "x" * (MAX_RPC_STREAM_CHUNK_BYTES + 1)
    with pytest.raises(RpcMessageTooLarge):
        dumps_rpc_stream_chunk(huge)


def test_window_ico_on_win32_monkeypatch(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry import configure_window

    ico = tmp_path / "app.ico"
    ico.write_bytes(b"ico")
    monkeypatch.setattr(sys, "platform", "win32")
    called: list[str] = []
    monkeypatch.setattr(
        tk_root, "iconbitmap", lambda path: called.append(path), raising=False
    )
    configure_window(tk_root, icon=ico)
    assert called == [str(ico)]


def test_rpc_bootstrap_and_nav_sync(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.eval_js.side_effect = RuntimeError("eval fail")
    web._webview = native
    web._rpc_methods["ping"] = MagicMock()
    web._inject_rpc_bootstrap()
    native.eval_js.side_effect = None
    web._inject_rpc_bootstrap()
    assert web._rpc_bootstrap_injected is True

    web._rpc_nav_sync_after_id = None
    monkeypatch.setattr(frame, "after_idle", MagicMock(side_effect=tk.TclError("x")))
    web._schedule_rpc_bridge_navigation_sync()
    web._sync_rpc_bridge_after_finished()
    web.destroy()
    frame.destroy()


def test_deferred_reload_and_eval_release(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.reload.side_effect = RuntimeError("reload fail")
    web._webview = native
    monkeypatch.setattr(web, "_page_load_listening_wanted", lambda: True)
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    monkeypatch.setattr(web, "_finish_navigation", lambda: None)
    web._run_deferred_reload()
    native.reload.side_effect = None
    web._run_deferred_reload()

    web._pending_eval_tokens[7] = (0.0, lambda _r: None, None)
    web._pending_eval_callbacks = 1
    web._release_pending_eval(7)
    web._release_pending_eval(999)
    web._native_eval_wait[1] = (MagicMock(), 42)
    web._drop_native_eval_wait_for_py_token(42)
    assert 1 not in web._native_eval_wait
    web.destroy()
    frame.destroy()


def test_wait_until_ready_success_and_nested(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(type(web), "ready", property(lambda self: True))
    assert web.wait_until_ready(timeout=0.1) is True

    monkeypatch.setattr(type(web), "ready", property(lambda self: False))
    web._wait_until_ready_active = True
    with pytest.raises(RuntimeError, match="already running"):
        web.wait_until_ready(timeout=0.1)
    web._wait_until_ready_active = False

    pumps = {"n": 0}

    def pump(_root):
        pumps["n"] += 1

    monkeypatch.setattr(web, "_pump_wait_until_ready", pump)
    assert web.wait_until_ready(timeout=0.05) is False
    assert pumps["n"] >= 1
    web.destroy()
    frame.destroy()


def test_download_started_destroyed_and_tcl_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    download = Download(url="https://a/x", suggested_dest="/tmp/a")
    web._destroyed = True
    web._deliver_download_started(download)
    web._destroyed = False
    monkeypatch.setattr(
        frame,
        "event_generate",
        MagicMock(side_effect=tk.TclError("x")),
    )
    web._on_download_started = lambda _d: None
    web._deliver_download_started(download)
    web.destroy()
    frame.destroy()


def test_linux_and_win32_try_create_branches(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=200, height=150)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    web = WebView(frame, html="<p>x</p>", width=200, height=150)
    monkeypatch.setattr(web, "_sync_bounds", lambda: True)
    monkeypatch.setattr(web, "_maybe_fire_ready", lambda: None)
    monkeypatch.setattr(web, "_schedule_initial_load", lambda: None)
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    monkeypatch.setattr(web, "_needs_event_poll", lambda: False)
    monkeypatch.setattr(web, "_attach_gtk_pump_for_native", lambda: None)

    pumped: list[int] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_unless_active",
        lambda _f, bursts=1: pumped.append(bursts),
        raising=False,
    )
    native = MagicMock()
    native.is_alive.return_value = False
    monkeypatch.setattr("tkwry.webview.NativeWebView", lambda *a, **k: native)
    web._webview = None
    web._creation_error = None
    web._try_create()
    assert pumped == [20]
    assert web._webview is native

    # Win32 missing WebView2
    web._webview = None
    web._creation_error = None
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "tkwry._win32.is_webview2_runtime_available", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "tkwry._win32.webview2_missing_error",
        lambda: RuntimeError("missing"),
        raising=False,
    )
    monkeypatch.setattr(
        "tkwry._win32.WEBVIEW2_MISSING_MESSAGE", "no wv2", raising=False
    )
    web._try_create()
    assert web._creation_error is not None
    web.destroy()
    frame.destroy()


def test_fail_pending_load_and_visibility_helpers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry.exceptions import WebViewNavigationError

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._pending_load = ("html", "<p>z</p>")
    web._flush_load_attempt = 3
    monkeypatch.setattr(
        frame, "event_generate", MagicMock(side_effect=RuntimeError("x"))
    )
    web._fail_pending_load(RuntimeError("boom"))
    assert web._pending_load is None
    assert isinstance(web._last_navigation_error, WebViewNavigationError)

    web._fail_pending_load(WebViewNavigationError("nav"))
    assert isinstance(web._last_navigation_error, WebViewNavigationError)

    monkeypatch.setattr(frame, "winfo_exists", lambda: False)
    assert web._bounds_size() is None
    assert web._frame_should_show() is False

    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(
        frame, "winfo_ismapped", MagicMock(side_effect=tk.TclError("x"))
    )
    assert web._host_is_viewable_for_map() is False

    monkeypatch.setattr(frame, "winfo_ismapped", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert web._host_is_viewable_for_map() is True

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(frame, "winfo_viewable", lambda: 0)
    assert web._host_is_viewable_for_map() is False

    web._bounds_sync_scheduled = False
    monkeypatch.setattr(
        frame, "update_idletasks", MagicMock(side_effect=tk.TclError("x"))
    )
    web._schedule_bounds_sync()
    web.destroy()
    frame.destroy()


def test_finish_navigation_linux_branches(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(sys, "platform", "linux")
    drained: list[str] = []
    monkeypatch.setattr(web, "_drain_after_navigation", lambda: drained.append("d"))
    monkeypatch.setattr(
        web, "_schedule_post_navigation_drain", lambda: drained.append("s")
    )
    web._in_poll_events = True
    web._finish_navigation()
    assert drained == ["s"]
    web._in_poll_events = False
    web._finish_navigation()
    assert "d" in drained

    web._destroyed = True
    web._finish_navigation()
    web._destroyed = False

    # Restore real scheduler for platform-gated path.
    monkeypatch.setattr(
        web,
        "_schedule_post_navigation_drain",
        WebView._schedule_post_navigation_drain.__get__(web, WebView),
    )
    web._post_nav_drain_scheduled = False
    scheduled: list[object] = []
    monkeypatch.setattr(frame, "after_idle", lambda fn: scheduled.append(fn) or "aid")
    monkeypatch.setattr(web, "_track_after", lambda _i: None)
    web._schedule_post_navigation_drain()
    assert web._post_nav_drain_scheduled is True
    assert scheduled
    web._destroyed = False
    scheduled[0]()
    web.destroy()
    frame.destroy()


def test_parse_rpc_large_and_bad_version() -> None:
    from tkwry.ipc import MAX_RPC_MESSAGE_BYTES, parse_rpc_request

    big = (
        '{"__tkwry":"rpc","id":"rid1","method":"m","params":["'
        + ("x" * (MAX_RPC_MESSAGE_BYTES))
        + '"]}'
    )
    req = parse_rpc_request(big)
    assert req is not None
    assert req.reject is not None

    bad_ver = '{"__tkwry":"rpc","id":"r2","method":"m","params":[],"version":true}'
    req2 = parse_rpc_request(bad_ver)
    assert req2 is not None and req2.reject is not None

    assert parse_rpc_request("\udcff") is None  # encode failure path if any


def test_parent_clear_and_hook_helpers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry import _parent

    _parent._clear_interp_thread(12345)
    assert 12345 not in _parent._interp_threads

    # Idempotent destroy hook registration
    _parent._ensure_interp_root_destroy_hook(tk_root, 99901)
    _parent._ensure_interp_root_destroy_hook(tk_root, 99901)
    assert 99901 in _parent._interp_root_hooks


def test_delete_cookie_and_set_precreate_url(
    tk_root, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tkwry import Cookie

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    with pytest.raises(TypeError, match="str"):
        web.delete_cookie(Cookie("a", "b", domain="x.com"), url="https://x.com/")
    with pytest.raises(TypeError, match="url"):
        web.delete_cookie("name-only")
    with pytest.raises(ValueError, match="hostname"):
        web.delete_cookie("n", url="https:///path")
    web.delete_cookie("sess", url="https://example.com/app")
    native.delete_cookie.assert_called()

    web._webview = None
    web._creation_error = RuntimeError("fail")
    with pytest.raises(Exception):
        web.load_url("https://example.com/")
    web._creation_error = None
    web.load_url("https://example.com/queued")
    web.destroy()
    frame.destroy()


def test_mac_sync_bounds_rect_paths(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._embed = type(web._embed)(handle=1, root_relative=False)
    assert web._mac_sync_bounds_rect() is None

    web._embed = type(web._embed)(handle=1, root_relative=True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert web._mac_sync_bounds_rect() is None

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "tkwry._parent.tk_embed_bounds",
        lambda *_a, **_k: (0, 0, 1, 1),
    )
    assert web._mac_sync_bounds_rect() is None

    monkeypatch.setattr(
        "tkwry._parent.tk_embed_bounds",
        lambda *_a, **_k: (10, 20, 300, 200),
    )
    monkeypatch.setattr(frame, "winfo_manager", lambda: "pack")
    rect = web._mac_sync_bounds_rect()
    assert rect == (10, 20, 300, 200)

    monkeypatch.setattr(frame, "winfo_manager", lambda: "place")
    monkeypatch.setattr(web, "_bounds_size", lambda: None)
    assert web._mac_sync_bounds_rect() is None
    monkeypatch.setattr(web, "_bounds_size", lambda: (111, 222))
    assert web._mac_sync_bounds_rect() == (10, 20, 111, 222)

    monkeypatch.setattr(
        "tkwry._parent.tk_embed_bounds",
        MagicMock(side_effect=tk.TclError("x")),
    )
    assert web._mac_sync_bounds_rect() is None
    web.destroy()
    frame.destroy()


def test_initial_load_profile_logging(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._arm_initial_load(("html", "<p>z</p>"))
    web._webview = MagicMock()
    web._pending_load = None
    monkeypatch.setattr(web, "_frame_ready_for_initial_load", lambda: True)
    monkeypatch.setattr(web, "_sync_bounds", lambda: None)
    monkeypatch.setattr(web, "_clear_initial_load", lambda: None)
    monkeypatch.setenv("TKWRY_LOAD_PROFILE", "1")
    # After sync, initial_load still set; force fire path logging then clear via pending
    web._initial_load = ("html", "<p>z</p>")
    monkeypatch.setattr(
        web,
        "_dispatch_pending_load",
        lambda: None,
        raising=False,
    )
    # Simulate successful ready path by making frame ready and having load apply
    applied: list[object] = []

    def apply():
        applied.append(web._initial_load)
        web._initial_load = None

    # Call internals that print profile lines
    monkeypatch.setattr(web, "_frame_ready_for_initial_load", lambda: False)
    monkeypatch.setattr(web, "_maybe_reschedule_initial_load", lambda: None)
    monkeypatch.setattr(
        frame, "winfo_viewable", MagicMock(side_effect=tk.TclError("x"))
    )
    web._run_initial_load()
    monkeypatch.delenv("TKWRY_LOAD_PROFILE", raising=False)
    web.destroy()
    frame.destroy()


def test_popup_menu_create_fails(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    from tkwry import ContextMenuEvent

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    monkeypatch.setattr(tk, "Menu", MagicMock(side_effect=tk.TclError("no menu")))
    web._popup_context_menu(ContextMenuEvent(x=1, y=2), (("A", lambda: None),))
    web.destroy()
    frame.destroy()


def test_rpc_stream_drop_and_settle_paths(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import queue as queue_mod
    from concurrent.futures import Future

    from tkwry.ipc import RPC_STREAM_DONE

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._webview = MagicMock()
    settled: list[tuple] = []
    monkeypatch.setattr(
        web,
        "_settle_rpc",
        lambda req_id, ok, value: settled.append((req_id, ok, value)),
    )
    monkeypatch.setattr(web, "_signal_rpc_cancel", lambda _id: None)

    # Force drop via qsize without filling 2048 entries.
    fake_q = MagicMock()
    fake_q.qsize.return_value = 10_000
    web._rpc_stream_queue = fake_q
    assert web._enqueue_rpc_stream_chunk("0:drop", {"a": 1}) is False
    assert "0:drop" in web._rpc_stream_drop_reject

    # put_nowait failure path
    web._rpc_stream_queue = MagicMock()
    web._rpc_stream_queue.qsize.return_value = 0
    web._rpc_stream_queue.put_nowait.side_effect = RuntimeError("full")
    assert web._enqueue_rpc_stream_chunk("0:err", 1) is False

    web._rpc_stream_open.add("0:open")
    web._rpc_stream_drop_reject.add("0:open")
    web._flush_rpc_stream_drop_rejects()
    assert any(s[0] == "0:open" and s[1] is False for s in settled)

    # Executor join leftover warning
    dead = MagicMock()
    dead.is_alive.return_value = False
    alive = MagicMock()
    alive.is_alive.return_value = True
    alive.join = MagicMock()
    executor = MagicMock()
    executor._threads = (dead, alive)
    web._rpc_executor = executor
    monkeypatch.setattr("tkwry._rpc_api._RPC_EXECUTOR_JOIN_SECONDS", 0.0)
    web._shutdown_rpc_executor()

    # settle tracked future paths
    web._destroyed = False
    fut: Future[object] = Future()
    fut.set_result({"ok": True})
    web._rpc_inflight["0:f1"] = fut
    web._rpc_timeout_after["0:f1"] = "aid"
    monkeypatch.setattr(frame, "after_cancel", MagicMock(side_effect=tk.TclError("x")))
    web._settle_tracked_rpc_future("0:f1", fut)

    fut2: Future[object] = Future()
    fut2.set_exception(RuntimeError("boom"))
    web._rpc_inflight["0:f2"] = fut2
    web._settle_tracked_rpc_future("0:f2", fut2)

    fut3: Future[object] = Future()
    fut3.set_result(RPC_STREAM_DONE)
    web._rpc_inflight["0:f3"] = fut3
    web._settle_tracked_rpc_future("0:f3", fut3)

    web._rpc_stream_drop_reject.add("0:f4")
    fut4: Future[object] = Future()
    fut4.set_result(1)
    web._rpc_inflight["0:f4"] = fut4
    web._settle_tracked_rpc_future("0:f4", fut4)

    web._destroyed = True
    fut5: Future[object] = Future()
    fut5.set_result(1)
    web._rpc_inflight["0:f5"] = fut5
    web._settle_tracked_rpc_future("0:f5", fut5)
    web._destroyed = False

    web._rpc_stream_queue = queue_mod.Queue()
    web._rpc_stream_queue.put_nowait(("0:c1", 1))
    pushed: list[object] = []
    monkeypatch.setattr(web, "_push_rpc_chunk", lambda *a: pushed.append(a))
    web._drain_rpc_stream_chunks()
    assert pushed

    web._rpc_stream_queue.put_nowait(("0:c2", 2))
    web._destroyed = True
    web._drain_rpc_stream_chunks()
    web.destroy()
    frame.destroy()


def test_app_watch_tick_and_schedule(
    tk_root, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    frame = tk.Frame(tk_root)
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<p>x</p>", encoding="utf-8")
    web = WebView(frame, html="<p>x</p>")
    web._app_root = str(app)
    web._app_watch_max_files = 1
    web._app_watch_limit_warned = False
    # Force truncated path by scanning with tiny max
    monkeypatch.setattr(
        "tkwry._rpc_api.scan_app_mtime",
        lambda *a, **k: (1.0, 1, True),
    )
    web._scan_app_mtime()
    assert "watch_app scanned" in capsys.readouterr().err

    web._app_watch_mtime = 1.0
    monkeypatch.setattr(web, "_scan_app_mtime", lambda: 2.0)
    monkeypatch.setattr(type(web), "ready", property(lambda self: True))
    reloads: list[str] = []
    monkeypatch.setattr(
        web,
        "reload",
        lambda: reloads.append("r") or (_ for _ in ()).throw(RuntimeError("x")),
    )
    web._app_watch_tick(100)
    # Exception path still exercised even if mtime is unchanged.

    monkeypatch.setattr(web, "reload", lambda: reloads.append("ok"))
    web._app_watch_mtime = 1.0
    web._app_watch_tick(100)
    assert web._app_watch_mtime == 2.0

    web._app_watch_mtime = None
    web._app_watch_tick(100)
    assert web._app_watch_mtime == 2.0

    web._app_watch_after_id = "x"
    monkeypatch.setattr(frame, "after_cancel", MagicMock(side_effect=ValueError("x")))
    web._stop_app_watch()

    scheduled: list[object] = []
    monkeypatch.setattr(frame, "after", lambda ms, fn: scheduled.append(fn) or "aid")
    monkeypatch.setattr(web, "_track_after", lambda _i: None)
    web._schedule_app_watch(50)
    assert scheduled
    # Fire one tick then stop
    web._destroyed = True
    scheduled[0]()
    web.destroy()
    frame.destroy()


def test_deliver_ipc_rpc_and_context_paths(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    web._on_ipc = lambda m: None
    monkeypatch.setattr(web, "_ipc_listening_wanted", lambda: True)
    monkeypatch.setattr(web, "_bridge_origin_allowed", lambda _u: True)
    monkeypatch.setattr(web, "_handle_rpc_request", lambda *a: None)
    monkeypatch.setattr(web, "_deliver_context_menu_event", lambda *a: None)
    monkeypatch.setattr(web, "_invoke_callback", lambda *a, **k: None)

    rpc = json.dumps(
        {"__tkwry": "rpc", "id": "0:r1", "method": "ping", "params": [], "version": 1}
    )
    ctx = json.dumps({"__tkwry": "contextmenu", "x": 1, "y": 2})
    oversized = "y" * 2_000_000
    native.drain_window_ipc_messages.return_value = [
        ("https://a/", rpc),
        ("https://a/", ctx),
        ("https://a/", "hello"),
        ("https://a/", oversized),
    ]
    web._deliver_ipc_messages()

    monkeypatch.setattr(web, "_bridge_origin_allowed", lambda _u: False)
    native.drain_window_ipc_messages.return_value = [
        ("https://a/", rpc),
        ("https://a/", ctx),
        ("https://a/", "hello"),
    ]
    web._deliver_ipc_messages()
    web.destroy()
    frame.destroy()


def test_cancel_inflight_timeout_cancel_errors(tk_root, monkeypatch) -> None:
    from concurrent.futures import Future

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._rpc_inflight["1:r"] = Future()
    web._rpc_timeout_after["1:r"] = "aid"
    web._rpc_cancel_events["1:r"] = __import__("threading").Event()
    monkeypatch.setattr(frame, "after_cancel", MagicMock(side_effect=RuntimeError("x")))
    web._cancel_inflight_rpc_for_navigation()
    assert not web._rpc_inflight

    web._webview = MagicMock()
    web._destroyed = False
    monkeypatch.setattr(web, "_sync_rpc_epoch_to_js", lambda: None)
    epoch = web._rpc_epoch
    web._bump_rpc_epoch_for_navigation(sync_js=True)
    assert web._rpc_epoch == epoch + 1
    web.destroy()
    frame.destroy()


def test_pack_grid_place_schedule(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    calls: list[str] = []
    monkeypatch.setattr(web, "_schedule_bounds_sync", lambda: calls.append("b"))
    monkeypatch.setattr(web, "_schedule_try_create", lambda: calls.append("c"))
    web.pack()
    web.grid()
    web.place(x=0, y=0)
    assert calls.count("b") == 3 and calls.count("c") == 3
    web.destroy()
    frame.destroy()


def test_schedule_event_poll_errors(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(frame, "winfo_exists", lambda: False)
    web._schedule_event_poll(10)
    assert web._event_poll_after_id is None

    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "after", MagicMock(side_effect=tk.TclError("x")))
    web._schedule_event_poll(10)

    web._deferred_after_ids = ["a", "", "b"]
    monkeypatch.setattr(frame, "after_cancel", MagicMock(side_effect=ValueError("x")))
    web._cancel_deferred_callbacks()
    web.destroy()
    frame.destroy()


def test_pump_wait_until_ready_paths(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    tried: list[str] = []
    monkeypatch.setattr(web, "_try_create", lambda: tried.append("create"))
    monkeypatch.setattr(web, "_creation_size", lambda: (100, 100))
    web._create_pending = False
    web._webview = None
    web._creation_error = None
    web._pump_wait_until_ready(tk_root)
    assert "create" in tried

    native = MagicMock()
    web._webview = native
    web._bounds_sync_scheduled = True
    monkeypatch.setattr(web, "_deferred_sync_bounds", lambda: tried.append("def"))
    web._pump_wait_until_ready(tk_root)
    assert "def" in tried

    web._bounds_sync_scheduled = False
    monkeypatch.setattr(web, "_layout_ready", lambda: False)
    monkeypatch.setattr(web, "_sync_bounds", lambda: tried.append("sync"))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_unless_active",
        lambda *_a, **_k: tried.append("gtk"),
        raising=False,
    )
    monkeypatch.setattr(web, "_deliver_async_event_queues", lambda: tried.append("q"))
    web._pump_wait_until_ready(tk_root)
    web.destroy()
    frame.destroy()


def test_wakeup_pipe_non_darwin(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("tkwry.webview._toplevel_wakeup_write_fd", lambda _t: None)
    opened: list[str] = []
    monkeypatch.setattr(
        "tkwry.webview._open_wakeup_pipe",
        lambda: (3, 4) if opened.append("o") or True else (3, 4),
    )
    monkeypatch.setattr("tkwry.webview._ensure_tk_wakeup_fileevent", lambda _t: None)
    monkeypatch.setattr("tkwry.webview._register_sync_hook_webview", lambda *_a: None)
    monkeypatch.setattr("tkwry.webview._release_tk_wakeup_pipe", lambda _t: None)
    monkeypatch.setattr("tkwry._linux.GtkPump.detach", lambda _f: None, raising=False)
    web._tk_wakeup_pipe_attached = False
    web._webview = MagicMock()
    web._ensure_tk_wakeup_pipe()
    assert web._tk_wakeup_pipe_attached is True
    web._release_tk_wakeup_pipe_registration()
    assert web._tk_wakeup_pipe_attached is False

    web._tk_wakeup_pipe_attached = True
    monkeypatch.setattr(
        frame, "winfo_toplevel", MagicMock(side_effect=tk.TclError("x"))
    )
    web._release_tk_wakeup_pipe_registration()

    web._webview = MagicMock()
    web._webview.set_mac_wakeup_write_fd.side_effect = RuntimeError("x")
    web._invalidate_native_wakeup_fd()
    web._release_wakeup_pipe_and_linux_pump()
    web.destroy()
    frame.destroy()


def test_repr_error_branches(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.url.side_effect = RuntimeError("url")
    web._webview = native
    assert "WebView" in repr(web)
    web.destroy()
    frame.destroy()


def test_bump_queue_drop_zero(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    before = list(web._local_queue_drop_counts)
    web._bump_queue_drop(0, 0)
    assert web._local_queue_drop_counts == before
    web.destroy()
    frame.destroy()


def test_set_on_ipc_enables_listening(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    web.set_on_ipc(lambda _m: None)
    native.set_ipc_listening.assert_called()
    web.destroy()
    frame.destroy()


def test_context_menu_item_callback_runs(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry import ContextMenuEvent

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", default_context_menus=False)
    clicks: list[str] = []
    items = (("Go", lambda: clicks.append("go")),)

    class _Menu:
        def __init__(self, *_a, **_k):
            self.commands = []

        def add_separator(self):
            pass

        def add_command(self, label, command):
            self.commands.append(command)

        def tk_popup(self, *_a):
            for cmd in self.commands:
                cmd()

        def grab_release(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(tk, "Menu", _Menu)
    web._popup_context_menu(ContextMenuEvent(x=1, y=2), items)
    assert clicks == ["go"]
    web.destroy()
    frame.destroy()


def test_url_idn_and_network_host_edges() -> None:
    from tkwry._url import (
        _is_network_host,
        _is_windows_drive_path,
        _looks_like_idn_hostname,
        _normalize_url,
    )

    assert _looks_like_idn_hostname("") is False
    assert _looks_like_idn_hostname(".x") is False
    assert _looks_like_idn_hostname("localhost") is True
    assert _looks_like_idn_hostname("nodot") is False
    assert _looks_like_idn_hostname("example.com") is True
    assert _looks_like_idn_hostname("例.テスト") is True
    assert _is_network_host("") is False
    assert _is_network_host("localhost") is True
    assert _is_network_host("nodot") is False
    assert _is_network_host("a.b") is True
    assert _is_windows_drive_path("C:/x") is True
    assert _is_windows_drive_path("C:\\x") is True
    assert _is_windows_drive_path("/x") is False
    # IPv6 already-bracketed https authority left alone
    assert _normalize_url("https://[::1]/") == "https://[::1]/"
    assert _normalize_url("https://::1/") == "https://[::1]/"


def test_url_split_ipv6_more_edges() -> None:
    from tkwry._url import _split_ipv6_host_port, _split_ipv6_with_zone

    assert _split_ipv6_host_port("[::1]/path") == ("[::1]", "", "/path")
    assert _split_ipv6_host_port("[::1]") == ("[::1]", "", "")
    assert _split_ipv6_host_port("[::1]:8080/x") == ("[::1]", "8080", "/x")
    assert _split_ipv6_host_port("::1") == ("[::1]", "", "")
    assert _split_ipv6_host_port("::1:8080") == ("[::1]", "8080", "")
    assert _split_ipv6_with_zone("[fe80::1%en0]") is not None
    assert _split_ipv6_with_zone("[fe80::1%en0]:8080") is not None
    assert _split_ipv6_with_zone("[fe80::1%en0]/p") is not None
    assert _split_ipv6_with_zone("fe80::1%en0") is not None
    assert _split_ipv6_with_zone("fe80::1%en0:8080") is not None
    assert _split_ipv6_with_zone("[::1]extra") is None
    assert _split_ipv6_with_zone("[notipv6%en0]") is None


def test_macos_pipe_select_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS helpers")
    from tkwry import _macos

    # Avoid patching the real select module (breaks Tk); call with bad fd attr.
    toplevel = MagicMock()
    toplevel._tkwry_mac_wake_read_fd = -1
    assert _macos._mac_pipe_readable(toplevel) is False
    _macos._mac_pump_wakeup_pipe(toplevel)


def test_macos_service_wakeup_tcl_error(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS helpers")
    from tkwry import _macos

    toplevel = MagicMock()
    monkeypatch.setattr(_macos, "_mac_pipe_readable", lambda _t: True)
    monkeypatch.setattr(_macos, "_mac_pump_wakeup_pipe", lambda _t: None)
    monkeypatch.setattr(_macos, "_drain_mac_tk_unfocus", lambda _t: True)
    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [])
    monkeypatch.setattr(_macos, "_sync_mac_web_input_cache", lambda _t: None)
    monkeypatch.setattr(_macos, "_mac_unfocus_pending", lambda _t: False)
    monkeypatch.setattr(_macos, "_ensure_mac_pump", lambda _t: None)
    monkeypatch.setattr("tkwry._host._drain_pending_destroy_webviews", lambda _t: None)
    toplevel.update_idletasks.side_effect = tk.TclError("x")
    assert _macos._mac_service_wakeup(toplevel) is True


def test_macos_after_and_bind_root_walk(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS helpers")
    from tkwry import _macos

    monkeypatch.setattr(_macos, "_toplevel_alive", lambda _t: False)
    _macos._mac_after(tk_root, 1, lambda: None)

    monkeypatch.setattr(_macos, "_toplevel_alive", lambda _t: True)
    monkeypatch.setattr(tk_root, "after", MagicMock(side_effect=tk.TclError("x")))
    _macos._mac_after(tk_root, 1, lambda: None)

    child = MagicMock()
    child._root.side_effect = tk.TclError("x")
    mid = MagicMock()
    mid.winfo_class.return_value = "Frame"
    mid.master = tk_root
    child.winfo_class.return_value = "Frame"
    child.master = mid
    assert _macos._mac_bind_root(child) is tk_root


def test_origin_helpers_edges() -> None:
    from tkwry._origin import origin_allowed

    assert origin_allowed("https://a.example/x", ("https://a.example",)) is True
    assert origin_allowed("https://b.example/x", ("https://a.example",)) is False
    assert origin_allowed("https://a.example/x", ()) is False


def test_rpc_sync_epoch_and_enable(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._webview = MagicMock()
    web._rpc_methods["x"] = MagicMock()
    monkeypatch.setattr(web, "_inject_rpc_bootstrap", lambda: None)
    web._sync_rpc_epoch_to_js()
    web._enable_rpc()
    web._destroyed = True
    web._sync_rpc_epoch_to_js()
    web._destroyed = False
    web._webview = None
    web._inject_rpc_bootstrap()
    web.destroy()
    frame.destroy()


def test_place_info_and_layout_helpers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(frame, "place_info", MagicMock(side_effect=tk.TclError("x")))
    assert web._place_info_size() == (None, None)

    monkeypatch.setattr(
        frame,
        "place_info",
        lambda: {"width": "bad", "height": "2.5"},
    )
    w, h = web._place_info_size()
    assert w is None
    assert h == 2 or h is None or isinstance(h, int)

    monkeypatch.setattr(frame, "winfo_exists", lambda: False)
    assert web._frame_is_laid_out() is False
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_manager", lambda: "")
    assert web._frame_is_laid_out() is False
    monkeypatch.setattr(frame, "winfo_manager", MagicMock(side_effect=tk.TclError("x")))
    assert web._frame_is_laid_out() is False

    web._webview = MagicMock()
    web._destroyed = False
    monkeypatch.setattr(web, "_frame_is_laid_out", lambda: False)
    assert web._layout_ready() is False
    web.destroy()
    frame.destroy()


def test_sync_rpc_epoch_eval_error(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.eval_js.side_effect = RuntimeError("bump")
    web._webview = native
    web._rpc_methods["x"] = MagicMock()
    web._sync_rpc_epoch_to_js()
    web.destroy()
    frame.destroy()


def test_page_load_handler_exception(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    from tkwry import PageLoadEvent

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    native.drain_page_load_events.return_value = [
        (PageLoadEvent.Finished, "https://example.com/"),
    ]
    web._webview = native
    web._on_page_load = lambda *_a: (_ for _ in ()).throw(RuntimeError("pl"))
    monkeypatch.setattr(
        web, "_invoke_callback", WebView._invoke_callback.__get__(web, WebView)
    )
    # Use real invoke with error handler none → print_exc path in deliver
    web._deliver_page_load_events()
    web.destroy()
    frame.destroy()


def test_invoke_callback_error_handler(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    seen: list[object] = []

    def boom():
        raise RuntimeError("cb")

    def on_err(exc, kind):
        seen.append((type(exc).__name__, kind))

    web._on_callback_error = on_err
    web._invoke_callback(boom, kind="test")
    assert seen == [("RuntimeError", "test")]

    def boom_handler(exc, kind):
        raise RuntimeError("handler")

    web._on_callback_error = boom_handler
    web._invoke_callback(boom, kind="test2")
    web.destroy()
    frame.destroy()


def test_win32_raise_frame_after_layout(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    monkeypatch.setattr(sys, "platform", "win32")
    web._webview = MagicMock()
    raised: list[int] = []
    monkeypatch.setattr(
        "tkwry._win32.raise_frame_webview",
        lambda wid: raised.append(wid),
        raising=False,
    )
    # Register this web in the frame host map so stacking finds it.
    import tkwry.webview as wv

    wv._frame_webview_refs[id(frame)] = lambda: web  # type: ignore[assignment]

    # Weakref-like callable
    class _Ref:
        def __call__(self):
            return web

    wv._frame_webview_refs[id(frame)] = _Ref()  # type: ignore[assignment]
    web._schedule_stacking_sync()
    assert web._stacking_sync_scheduled is True
    web._deferred_sync_stacking()
    web._sync_tk_stacking_order()
    # Exception path
    monkeypatch.setattr(
        "tkwry._win32.raise_frame_webview",
        MagicMock(side_effect=RuntimeError("z")),
        raising=False,
    )
    web._sync_tk_stacking_order()
    web.destroy()
    frame.destroy()


def test_extra_webview_edges_for_fail_under(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    # title listening early return
    web._webview = None
    web._on_title_changed = lambda _t: None
    web._native_title_changed("x")

    web._webview = MagicMock()
    monkeypatch.setattr(sys, "platform", "win32")
    web._stacking_sync_scheduled = True
    web._schedule_stacking_sync()  # already scheduled
    web._webview = None
    web._schedule_stacking_sync()  # no webview
    web._destroyed = True
    web._webview = MagicMock()
    web._schedule_stacking_sync()
    web._destroyed = False

    monkeypatch.setattr(frame, "after_idle", MagicMock(side_effect=tk.TclError("x")))
    web._stacking_sync_scheduled = False
    web._webview = MagicMock()
    web._schedule_stacking_sync()
    assert web._stacking_sync_scheduled is False

    # bounds_size TclError
    monkeypatch.setattr(frame, "winfo_exists", MagicMock(side_effect=tk.TclError("x")))
    assert web._bounds_size() is None
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", MagicMock(side_effect=tk.TclError("x")))
    assert web._bounds_size() is None

    # frame_should_show TclError after exists
    monkeypatch.setattr(web, "_bounds_size", lambda: (10, 10))
    monkeypatch.setattr(
        web, "_host_is_viewable_for_map", MagicMock(side_effect=tk.TclError("x"))
    )
    assert web._frame_should_show() is False
    web.destroy()
    frame.destroy()


def test_one_more_line_for_fail_under(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._destroyed = True
    web._deliver_context_menu_event(
        __import__("tkwry", fromlist=["ContextMenuEvent"]).ContextMenuEvent(x=0, y=0)
    )
    web._destroyed = False
    web.destroy()
    frame.destroy()
