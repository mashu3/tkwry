"""Additional macOS helper coverage (no native WebView / Tk update required)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS helpers")

from tkwry import _macos  # noqa: E402


def test_toplevel_alive_handles_tcl_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _macos._toplevel_alive(tk_root) is True
    monkeypatch.setattr(
        tk_root,
        "winfo_exists",
        lambda: (_ for _ in ()).throw(__import__("tkinter").TclError("gone")),
    )
    assert _macos._toplevel_alive(tk_root) is False


def test_mac_event_widget_from_path_and_dead(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root, name="covframe")
    event = SimpleNamespace(widget=str(frame))
    monkeypatch.setattr(tk, "_default_root", tk_root, raising=False)
    assert _macos._mac_event_widget(event) is frame
    assert _macos._mac_event_toplevel(event) is tk_root

    dead = SimpleNamespace(widget=".missing")
    assert _macos._mac_event_widget(dead) is None
    assert _macos._mac_event_widget(SimpleNamespace(widget=None)) is None


def test_widget_key_capability_helpers(tk_root) -> None:
    import tkinter as tk

    entry = tk.Entry(tk_root)
    label = tk.Label(tk_root)
    assert _macos._widget_takefocus_enabled(entry) is True
    assert _macos._widget_has_text_input_capability(entry) is True
    assert _macos._widget_has_text_input_capability(label) is False
    assert _macos._widget_accepts_tk_keys(entry) is True
    assert _macos._widget_accepts_tk_keys(label) is False


def test_mac_pipe_readable_and_pump(tk_root) -> None:
    read_fd, write_fd = os.pipe()
    try:
        setattr(tk_root, "_tkwry_mac_wake_read_fd", read_fd)
        assert _macos._mac_pipe_readable(tk_root) is False
        os.write(write_fd, b"\x01")
        assert _macos._mac_pipe_readable(tk_root) is True
        _macos._mac_pump_wakeup_pipe(tk_root)
        assert _macos._mac_pipe_readable(tk_root) is False
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_mac_service_wakeup_drains_hooks(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    pumped: list[str] = []
    monkeypatch.setattr(_macos, "_mac_pipe_readable", lambda _t: True)
    monkeypatch.setattr(
        _macos, "_mac_pump_wakeup_pipe", lambda _t: pumped.append("pipe")
    )
    monkeypatch.setattr(
        _macos, "_drain_mac_tk_unfocus", lambda _t: pumped.append("unfocus") or True
    )
    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [])
    monkeypatch.setattr(
        _macos, "_sync_mac_web_input_cache", lambda _t: pumped.append("cache")
    )
    monkeypatch.setattr(
        "tkwry._host._drain_pending_destroy_webviews",
        lambda _t: pumped.append("pending"),
        raising=False,
    )
    monkeypatch.setattr(tk_root, "update_idletasks", lambda: None)
    assert _macos._mac_service_wakeup(tk_root) is True
    assert "pipe" in pumped and "unfocus" in pumped and "pending" in pumped


def test_mac_webviews_filters_destroyed(tk_root) -> None:
    import weakref

    live = MagicMock()
    live.destroyed = False
    live.native = object()
    dead = MagicMock()
    dead.destroyed = True
    dead.native = object()
    setattr(tk_root, "_tkwry_mac_webviews", [weakref.ref(live), weakref.ref(dead)])
    assert _macos._mac_webviews(tk_root) == [live]


def test_prepare_and_watch_devtools_bounds(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = MagicMock()
    web.destroyed = False
    web.ready = True
    web.native = MagicMock()
    web._frame = frame
    web.is_devtools_open.return_value = True
    scheduled: list[tuple] = []

    def capture_after(toplevel, delay, callback, *args):
        scheduled.append((toplevel, delay, callback, args))

    monkeypatch.setattr(_macos, "_mac_after", capture_after)
    layouts: list[object] = []
    monkeypatch.setattr(
        _macos,
        "sync_mac_webview_layout",
        lambda top, *, devtools_web=None: layouts.append((top, devtools_web)),
    )
    _macos.prepare_mac_devtools_open(web)  # type: ignore[arg-type]
    assert layouts and layouts[0][1] is web
    _macos.watch_mac_devtools_bounds(web)  # type: ignore[arg-type]
    assert scheduled
    web.destroyed = True
    _macos._mac_devtools_bounds_watch(web, tick=0)


def test_mac_bind_root_and_unbind(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    assert _macos._mac_bind_root(frame) is tk_root
    unbound: list[tuple] = []

    def capture(bind_root, toplevel, sequence, funcid):
        unbound.append((bind_root, sequence, funcid))

    monkeypatch.setattr(_macos, "_unbind_mac_global", capture)
    monkeypatch.setattr(tk_root, "bind_all", lambda *_a, **_k: "fid")
    monkeypatch.setattr(_macos, "_tag_mac_text_widgets", lambda _r: None)
    monkeypatch.setattr(_macos, "_prepend_mac_key_guard", lambda _w: None)
    _macos._ensure_mac_key_guard(tk_root)
    _macos._teardown_mac_key_guard(tk_root)
    assert unbound


def test_register_unregister_macos_webview(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = MagicMock()
    web._frame = tk_root
    web._destroyed = False
    monkeypatch.setattr(_macos, "_ensure_mac_key_guard", lambda _t: None)
    monkeypatch.setattr(_macos, "_ensure_mac_pump", lambda _t: None)
    monkeypatch.setattr(
        _macos, "install_automatic_window_tabbing_disable", lambda: None
    )
    monkeypatch.setattr(_macos, "_ensure_mac_window_tabbing_disabled", lambda _t: None)
    monkeypatch.setattr(_macos, "_teardown_mac_key_guard", lambda _t: None)
    monkeypatch.setattr(_macos, "_teardown_mac_wakeup_pipe", lambda _t: None)
    _macos._register_macos_webview(web)  # type: ignore[arg-type]
    assert any(ref() is web for ref in getattr(tk_root, "_tkwry_mac_webviews", []))
    _macos._unregister_macos_webview(web)  # type: ignore[arg-type]


def test_sync_mac_webview_layout_paths(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = MagicMock()
    ready.destroyed = False
    ready.ready = True
    ready.native = MagicMock()
    ready.is_devtools_open.return_value = True
    boom = MagicMock()
    boom.destroyed = False
    boom.ready = True
    boom.sync_bounds.side_effect = RuntimeError("sync fail")
    boom.native = object()
    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [ready, boom])
    _macos.sync_mac_webview_layout(tk_root, devtools_web=ready)
    ready.sync_bounds.assert_called()
    ready.native.raise_to_front.assert_called_once()

    dead = MagicMock()
    dead.destroyed = True
    dead.ready = True
    _macos.sync_mac_webview_layout(tk_root, devtools_web=dead)


def test_prepare_devtools_swallows_tcl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkinter as tk

    web = MagicMock()
    web._frame = MagicMock()
    web._frame.winfo_toplevel.side_effect = tk.TclError("gone")
    _macos.prepare_mac_devtools_open(web)  # type: ignore[arg-type]


def test_mac_event_widget_edge_cases(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    monkeypatch.setattr(tk, "_default_root", None, raising=False)
    assert _macos._mac_event_widget(SimpleNamespace(widget=".x")) is None

    class _NoToplevel:
        def winfo_exists(self) -> bool:
            return True

    assert _macos._mac_event_widget(SimpleNamespace(widget=_NoToplevel())) is None

    class _Dead:
        def winfo_exists(self) -> bool:
            raise tk.TclError("dead")

        def winfo_toplevel(self):
            return tk_root

    assert _macos._mac_event_widget(SimpleNamespace(widget=_Dead())) is None


def test_widget_helpers_tcl_error_branches() -> None:
    import tkinter as tk

    broken = MagicMock()
    broken.cget.side_effect = tk.TclError("no")
    assert _macos._widget_takefocus_enabled(broken) is True

    broken.winfo_class.side_effect = tk.TclError("gone")
    assert _macos._widget_accepts_tk_keys(broken) is False

    class _FakeText:
        def insert(self, *_a):
            return None

        def get(self, *_a):
            return ""

        def cget(self, name: str) -> str:
            return "1"

        def winfo_class(self) -> str:
            return "Custom"

    assert _macos._widget_has_text_input_capability(_FakeText()) is True  # type: ignore[arg-type]


def test_mac_after_skips_dead_and_tcl_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr(_macos, "_toplevel_alive", lambda _t: False)
    _macos._mac_after(tk_root, 0, lambda: None)
    monkeypatch.setattr(_macos, "_toplevel_alive", lambda _t: True)
    monkeypatch.setattr(
        tk_root, "after", lambda *_a, **_k: (_ for _ in ()).throw(tk.TclError("x"))
    )
    _macos._mac_after(tk_root, 0, lambda: None)


def test_mac_pump_tick_idle_and_active_delays(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[int] = []
    monkeypatch.setattr(
        _macos,
        "_mac_after",
        lambda top, delay, cb, *a: delays.append(delay),
    )
    monkeypatch.setattr(_macos, "_mac_service_wakeup", lambda _t: False)
    monkeypatch.setattr(_macos, "_peel_stale_tcl_editable_focus", lambda _t: None)
    monkeypatch.setattr(_macos, "_mac_unfocus_pending", lambda _t: False)
    monkeypatch.setattr(_macos, "_mac_pipe_readable", lambda _t: False)
    monkeypatch.setattr(
        _macos,
        "_mac_webviews",
        lambda _t: [SimpleNamespace(destroyed=False, native=object())],
    )

    monkeypatch.setattr(_macos, "_mac_web_input_active", lambda _t: False)
    _macos._mac_pump_tick(tk_root)
    assert delays[-1] == _macos._MAC_PUMP_IDLE_DELAY_MS

    monkeypatch.setattr(_macos, "_mac_web_input_active", lambda _t: True)
    _macos._mac_pump_tick(tk_root)
    assert delays[-1] == _macos._MAC_PUMP_ACTIVE_DELAY_MS

    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [])
    setattr(tk_root, "_tkwry_mac_pump_active", True)
    _macos._mac_pump_tick(tk_root)
    assert getattr(tk_root, "_tkwry_mac_pump_active") is False


def test_event_handlers_and_key_guard(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = MagicMock()
    entry.winfo_toplevel.return_value = tk_root
    entry.winfo_exists.return_value = True
    entry.winfo_class.return_value = "Entry"
    entry.cget.return_value = "1"
    entry.insert = MagicMock()
    entry.get = MagicMock(return_value="")
    setattr(
        tk_root,
        "_tkwry_mac_webviews",
        [SimpleNamespace(destroyed=False, native=object())],
    )
    monkeypatch.setattr(_macos, "_mac_event_widget", lambda _e: entry)
    monkeypatch.setattr(_macos, "_mac_event_toplevel", lambda _e: tk_root)
    monkeypatch.setattr(_macos, "_widget_accepts_tk_keys", lambda _w: True)

    tagged: list[object] = []
    monkeypatch.setattr(_macos, "_tag_mac_text_widgets", lambda w: tagged.append(w))
    _macos._mac_widget_mapped(SimpleNamespace(widget=entry))  # type: ignore[arg-type]
    assert tagged == [entry]

    released: list[object] = []
    monkeypatch.setattr(
        _macos,
        "_release_web_input_for_tk_traversal",
        lambda t: released.append(t),
    )
    monkeypatch.setattr(_macos, "_mac_service_wakeup", lambda _t: False)
    monkeypatch.setattr(_macos, "_ensure_mac_pump", lambda _t: None)
    monkeypatch.setattr(_macos, "_mac_after", lambda *_a, **_k: None)
    _macos._mac_input_wakeup(SimpleNamespace(widget=entry))  # type: ignore[arg-type]
    assert released

    prepended: list[object] = []
    monkeypatch.setattr(_macos, "_prepend_mac_key_guard", lambda w: prepended.append(w))
    _macos._mac_focus_in_handler(SimpleNamespace(widget=entry))  # type: ignore[arg-type]
    assert prepended == [entry]

    monkeypatch.setattr(_macos, "_mac_web_input_active", lambda _t: True)
    monkeypatch.setattr(_macos, "_mac_unfocus_pending", lambda _t: True)
    monkeypatch.setattr(_macos, "_release_tk_keyboard_focus", lambda _t: None)
    assert (
        _macos._mac_web_key_guard(SimpleNamespace(widget=entry, keysym="a")) == "break"
    )
    assert (
        _macos._mac_web_key_guard(SimpleNamespace(widget=entry, keysym="Escape"))
        is None
    )
    assert _macos._mac_tab_traversal_handler(SimpleNamespace(widget=entry)) is None


def test_release_web_input_and_refocus(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    native = MagicMock()
    web = SimpleNamespace(destroyed=False, native=native, focus_parent=MagicMock())
    boom = SimpleNamespace(
        destroyed=False,
        native=MagicMock(),
        focus_parent=MagicMock(side_effect=RuntimeError("x")),
    )
    none_native = SimpleNamespace(destroyed=False, native=None)
    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [web, boom, none_native])
    monkeypatch.setattr(_macos, "_mac_service_wakeup", lambda _t: False)
    _macos._release_web_input_for_tk_traversal(tk_root)
    web.focus_parent.assert_called_once()

    widget = MagicMock()
    widget.winfo_class.return_value = "Entry"
    widget.cget.return_value = "1"
    widget.insert = MagicMock()
    widget.get = MagicMock(return_value="")
    monkeypatch.setattr(_macos, "_widget_accepts_tk_keys", lambda _w: True)
    _macos._refocus_tk_widget(widget)
    widget.focus_set.assert_called_once()
    widget.focus_set.side_effect = tk.TclError("x")
    _macos._refocus_tk_widget(widget)


def test_teardown_wakeup_pipe_oserror(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    setattr(tk_root, "_tkwry_mac_wake_read_fd", 99901)
    setattr(tk_root, "_tkwry_mac_wake_write_fd", 99902)
    monkeypatch.setattr(
        _macos.os, "close", lambda _fd: (_ for _ in ()).throw(OSError("x"))
    )
    _macos._teardown_mac_wakeup_pipe(tk_root)
    assert not hasattr(tk_root, "_tkwry_mac_wake_read_fd")


def test_prepend_key_guard_and_tag_text(tk_root) -> None:
    import tkinter as tk

    entry = tk.Entry(tk_root)
    _macos._prepend_mac_key_guard(entry)
    tags = entry.bindtags()
    assert tags[0] == _macos._MAC_KEY_GUARD_TAG
    _macos._prepend_mac_key_guard(entry)  # idempotent
    _macos._tag_mac_text_widgets(entry)


def test_schedule_tabbing_disable_retries(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_after(top, delay, cb, *a):
        calls.append(delay)
        # Do not recurse into real retry chain.

    monkeypatch.setattr(_macos, "_mac_after", fake_after)
    monkeypatch.setattr(tk_root, "update_idletasks", lambda: None)
    monkeypatch.setattr(
        "tkwry._core.disable_macos_window_tabbing",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    monkeypatch.setattr("tkwry._parent.tk_parent_handle", lambda _t: 1)
    monkeypatch.setattr(_macos, "_TABBING_DISABLE_MAX_ATTEMPTS", 2)
    setattr(tk_root, "_tkwry_mac_tabbing_scheduled", True)
    _macos._schedule_mac_window_tabbing_disable(tk_root, attempt=0)
    assert calls  # scheduled retry
    assert getattr(tk_root, "_tkwry_mac_tabbing_scheduled") is True

    # Exhaust retries.
    _macos._schedule_mac_window_tabbing_disable(tk_root, attempt=1)
    assert getattr(tk_root, "_tkwry_mac_tabbing_scheduled") is False

    setattr(tk_root, "_tkwry_mac_tabbing_scheduled", True)
    monkeypatch.setattr(_macos, "_toplevel_alive", lambda _t: False)
    _macos._schedule_mac_window_tabbing_disable(tk_root, attempt=0)
    assert getattr(tk_root, "_tkwry_mac_tabbing_scheduled") is False


def test_mac_toplevel_mapped_and_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensured: list[object] = []
    monkeypatch.setattr(_macos, "_mac_event_widget", lambda _e: tk_root)
    monkeypatch.setattr(_macos, "_mac_event_toplevel", lambda _e: tk_root)
    monkeypatch.setattr(
        _macos,
        "_ensure_mac_window_tabbing_disabled",
        lambda t: ensured.append(t),
    )
    _macos._mac_toplevel_mapped(SimpleNamespace(widget=tk_root))  # type: ignore[arg-type]
    assert ensured == [tk_root]

    torn: list[object] = []
    monkeypatch.setattr(_macos, "_teardown_macos_toplevel", lambda t: torn.append(t))
    _macos._mac_toplevel_destroy(SimpleNamespace(widget=tk_root))  # type: ignore[arg-type]
    assert torn == [tk_root]

    monkeypatch.setattr(_macos, "_teardown_mac_wakeup_pipe", lambda _t: None)
    monkeypatch.setattr(_macos, "_teardown_mac_key_guard", lambda _t: None)
    _macos._teardown_macos_toplevel(tk_root)
    _macos._teardown_macos_toplevel(tk_root)  # idempotent


def test_release_tk_keyboard_focus_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    toplevel = MagicMock()
    focused = MagicMock()
    focused.winfo_class.return_value = "Entry"
    focused.__str__ = lambda self: ".entry"  # type: ignore[method-assign]
    toplevel.focus_get.return_value = focused
    monkeypatch.setattr(_macos, "_widget_accepts_tk_keys", lambda w: w is focused)
    # After focus moves to toplevel, treat as cleared.
    cleared = {"n": 0}

    def focus_get():
        cleared["n"] += 1
        return focused if cleared["n"] < 2 else None

    toplevel.focus_get.side_effect = focus_get
    toplevel.tk.call = MagicMock(side_effect=tk.TclError("x"))
    toplevel.focus_set = MagicMock()
    toplevel.focus_force = MagicMock()
    _macos._release_tk_keyboard_focus(toplevel)


def test_mac_event_toplevel_none_and_tcl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_macos, "_mac_event_widget", lambda _e: None)
    assert _macos._mac_event_toplevel(SimpleNamespace()) is None

    widget = MagicMock()
    widget.winfo_toplevel.side_effect = __import__("tkinter").TclError("gone")
    monkeypatch.setattr(_macos, "_mac_event_widget", lambda _e: widget)
    assert _macos._mac_event_toplevel(SimpleNamespace()) is None


def test_widget_accepts_tk_keys_tcl_and_suffix(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    label = tk.Label(tk_root)
    monkeypatch.setattr(
        label,
        "winfo_class",
        lambda: (_ for _ in ()).throw(tk.TclError("x")),
    )
    assert _macos._widget_accepts_tk_keys(label) is False

    custom = MagicMock()
    custom.winfo_class.return_value = "CustomEntry"
    custom.cget.return_value = "1"
    custom.insert = lambda *_a: None
    custom.get = lambda *_a: ""
    assert _macos._widget_accepts_tk_keys(custom) is True


def test_sync_mac_webview_layout_skips_and_devtools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toplevel = MagicMock()
    dead = MagicMock()
    dead.ready = False
    dead.destroyed = False
    alive = MagicMock()
    alive.ready = True
    alive.destroyed = False
    alive.sync_bounds.side_effect = [None, RuntimeError("sync")]
    alive.native = MagicMock()
    alive.is_devtools_open.side_effect = RuntimeError("dev")

    monkeypatch.setattr(_macos, "_mac_webviews", lambda _t: [dead, alive])
    _macos.sync_mac_webview_layout(toplevel)
    alive.sync_bounds.assert_called()

    alive.sync_bounds.side_effect = None
    alive.is_devtools_open.side_effect = None
    alive.is_devtools_open.return_value = True
    _macos.sync_mac_webview_layout(toplevel, devtools_web=alive)
    alive.native.raise_to_front.assert_called()

    destroyed = MagicMock()
    destroyed.destroyed = True
    destroyed.ready = True
    _macos.sync_mac_webview_layout(toplevel, devtools_web=destroyed)


def test_prepare_mac_devtools_open_tcl_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    web = MagicMock()
    web._frame.winfo_toplevel.side_effect = tk.TclError("x")
    _macos.prepare_mac_devtools_open(web)


def test_mac_devtools_bounds_watch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    web = MagicMock()
    web.destroyed = True
    _macos._mac_devtools_bounds_watch(web)

    web.destroyed = False
    web._frame.winfo_toplevel.side_effect = tk.TclError("x")
    _macos._mac_devtools_bounds_watch(web)

    web._frame.winfo_toplevel.side_effect = None
    toplevel = MagicMock()
    web._frame.winfo_toplevel.return_value = toplevel
    scheduled: list[tuple] = []
    toplevel.after = lambda ms, fn: scheduled.append((ms, fn))
    monkeypatch.setattr(_macos, "sync_mac_webview_layout", lambda *_a, **_k: None)
    web.is_devtools_open.return_value = True
    _macos._mac_devtools_bounds_watch(web, tick=0)
    assert scheduled


def test_mac_webviews_prunes_dead_weakrefs() -> None:
    import weakref

    toplevel = MagicMock()
    alive = MagicMock()
    alive.destroyed = False
    alive.native = object()
    dead = MagicMock()
    dead.destroyed = True
    dead.native = object()
    ephemeral = MagicMock()
    dead_ref = weakref.ref(ephemeral)
    del ephemeral
    toplevel._tkwry_mac_webviews = [alive, dead, dead_ref]
    result = _macos._mac_webviews(toplevel)
    assert alive in result
    assert dead not in result


def test_mac_bind_root_fallback(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    assert _macos._mac_bind_root(tk_root) is tk_root
    frame = tk.Frame(tk_root)
    assert _macos._mac_bind_root(frame) is tk_root

    orphan = MagicMock()
    orphan._root.side_effect = AttributeError("x")
    orphan.winfo_class.side_effect = tk.TclError("x")
    orphan.master = None
    assert _macos._mac_bind_root(orphan) is orphan


def test_unbind_mac_global_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    _macos._unbind_mac_global(MagicMock(), MagicMock(), "<Key>", None)

    bind_root = MagicMock()
    toplevel = MagicMock()
    bind_root._unbind.side_effect = tk.TclError("a")
    toplevel._unbind.side_effect = tk.TclError("b")
    toplevel._root.side_effect = AttributeError("c")
    _macos._unbind_mac_global(bind_root, toplevel, "<Key>", "funcid")

    fallback = MagicMock()
    fallback._unbind.side_effect = tk.TclError("d")
    toplevel._root.side_effect = None
    toplevel._root.return_value = fallback
    _macos._unbind_mac_global(bind_root, toplevel, "<Key>", "funcid2")
