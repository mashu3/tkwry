"""Unit tests for macOS focus helpers (no native WebView required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tkwry import _macos


def test_mac_bind_root_falls_back_to_tk_ancestor(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    assert _macos._mac_bind_root(frame) is tk_root


def test_teardown_unbinds_using_stored_bind_root(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    unbound: list[tuple[object, tk.Misc, str, str | None]] = []

    def capture_unbind(
        bind_root: tk.Misc,
        toplevel: tk.Misc,
        sequence: str,
        funcid: str | None,
    ) -> None:
        unbound.append((bind_root, toplevel, sequence, funcid))

    import tkinter as tk

    monkeypatch.setattr(tk_root, "bind_all", lambda *_a, **_k: "bind-id")
    monkeypatch.setattr(_macos, "_unbind_mac_global", capture_unbind)
    monkeypatch.setattr(
        _macos,
        "_tag_mac_text_widgets",
        lambda _root: None,
        raising=False,
    )
    monkeypatch.setattr(
        _macos,
        "_prepend_mac_key_guard",
        lambda _widget: None,
        raising=False,
    )

    _macos._ensure_mac_key_guard(tk_root)
    assert tk_root._tkwry_mac_bind_root is tk_root

    _macos._teardown_mac_key_guard(tk_root)

    assert unbound == [
        (tk_root, tk_root, "<Button-1>", "bind-id"),
        (tk_root, tk_root, "<Map>", "bind-id"),
        (tk_root, tk_root, "<FocusIn>", "bind-id"),
    ]
    assert not getattr(tk_root, "_tkwry_mac_key_guard", False)


def test_teardown_unbinds_via_toplevel_when_bind_root_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_root = MagicMock()
    bind_root.bind_all.return_value = "bind-id"
    toplevel = MagicMock()
    toplevel._tkwry_mac_key_guard = False
    unbound: list[tuple[object, str]] = []

    bind_root._unbind = lambda what, funcid: unbound.append((what, funcid))
    toplevel._unbind = lambda what, funcid: unbound.append((what, funcid))
    toplevel._root.side_effect = AttributeError("no _root")
    monkeypatch.setattr(
        _macos,
        "_tag_mac_text_widgets",
        lambda _root: None,
        raising=False,
    )
    monkeypatch.setattr(
        _macos,
        "_prepend_mac_key_guard",
        lambda _widget: None,
        raising=False,
    )
    monkeypatch.setattr(_macos, "_mac_bind_root", lambda _widget: bind_root)

    _macos._ensure_mac_key_guard(toplevel)
    _macos._teardown_mac_key_guard(toplevel)

    assert unbound == [
        (("bind", "all", "<Button-1>"), "bind-id"),
        (("bind", "all", "<Map>"), "bind-id"),
        (("bind", "all", "<FocusIn>"), "bind-id"),
    ]


def test_mac_web_input_active_reads_native_state(tk_root) -> None:
    active_native = MagicMock()
    active_native.mac_web_input_active.return_value = True
    inactive_native = MagicMock()
    inactive_native.mac_web_input_active.return_value = False

    active_web = SimpleNamespace(destroyed=False, native=active_native)
    inactive_web = SimpleNamespace(destroyed=False, native=inactive_native)
    tk_root._tkwry_mac_webviews = [active_web, inactive_web]
    tk_root._tkwry_mac_web_input_active = False

    assert _macos._mac_web_input_active(tk_root) is True
    inactive_native.mac_web_input_active.return_value = True
    assert _macos._mac_web_input_active(tk_root) is True

    inactive_native.mac_web_input_active.return_value = False
    active_native.mac_web_input_active.return_value = False
    assert _macos._mac_web_input_active(tk_root) is False
    assert tk_root._tkwry_mac_web_input_active is False


def test_set_mac_webviews_input_active_syncs_cache_from_natives(tk_root) -> None:
    native = MagicMock()
    native.mac_web_input_active.return_value = True
    web = SimpleNamespace(destroyed=False, native=native)
    tk_root._tkwry_mac_webviews = [web]
    tk_root._tkwry_mac_web_input_active = False

    _macos._set_mac_webviews_input_active(tk_root, web)

    native.set_mac_web_input_active.assert_called_once_with(True)
    assert tk_root._tkwry_mac_web_input_active is True


def test_widget_accepts_tk_keys_includes_listbox_and_treeview(tk_root) -> None:
    import tkinter as tk
    from tkinter import ttk

    assert _macos._widget_accepts_tk_keys(tk.Listbox(tk_root)) is True
    assert _macos._widget_accepts_tk_keys(ttk.Treeview(tk_root)) is True
    assert _macos._widget_accepts_tk_keys(tk.Button(tk_root)) is False


def test_unregister_macos_webview_uses_stored_toplevel_when_frame_gone(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    teardown_calls: list[tk.Misc] = []

    def capture_teardown(toplevel: tk.Misc) -> None:
        teardown_calls.append(toplevel)

    monkeypatch.setattr(_macos, "_teardown_macos_toplevel", capture_teardown)

    frame = tk.Frame(tk_root)
    web = SimpleNamespace(
        _frame=frame,
        destroyed=False,
        native=MagicMock(),
        _macos_toplevel=tk_root,
    )
    tk_root._tkwry_mac_webviews = [web]  # type: ignore[list-item]

    def boom() -> tk.Misc:
        raise tk.TclError("bad window path")

    frame.winfo_toplevel = boom  # type: ignore[method-assign]

    _macos._unregister_macos_webview(web)  # type: ignore[arg-type]

    assert teardown_calls == [tk_root]
    assert not hasattr(web, "_macos_toplevel")


def test_mac_pump_tick_avoids_zero_delay_when_unfocus_pending(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[int] = []
    web = SimpleNamespace(destroyed=False, native=MagicMock())
    tk_root._tkwry_mac_webviews = [web]  # type: ignore[list-item]
    monkeypatch.setattr(_macos, "_mac_service_wakeup", lambda _t: None)
    monkeypatch.setattr(_macos, "_mac_unfocus_pending", lambda _t: True)
    monkeypatch.setattr(_macos, "_mac_pipe_readable", lambda _t: False)
    monkeypatch.setattr(
        tk_root,
        "after",
        lambda delay, _func, *_args: scheduled.append(delay) or "after-id",
    )

    _macos._mac_pump_tick(tk_root)

    assert scheduled == [1]


def test_mac_pump_tick_stops_when_toplevel_destroyed(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[int] = []
    web = SimpleNamespace(destroyed=False, native=MagicMock())
    tk_root._tkwry_mac_webviews = [web]  # type: ignore[list-item]
    tk_root._tkwry_mac_pump_active = True
    monkeypatch.setattr(_macos, "_mac_service_wakeup", lambda _t: None)
    monkeypatch.setattr(_macos, "_mac_unfocus_pending", lambda _t: False)
    monkeypatch.setattr(_macos, "_mac_pipe_readable", lambda _t: False)
    monkeypatch.setattr(_macos, "_mac_web_input_active", lambda _t: False)
    monkeypatch.setattr(
        tk_root,
        "after",
        lambda delay, _func, *_args: scheduled.append(delay) or "after-id",
    )
    monkeypatch.setattr(tk_root, "winfo_exists", lambda: False)

    _macos._mac_pump_tick(tk_root)

    assert scheduled == []


def test_widget_accepts_tk_keys_detects_insert_get_widgets(tk_root) -> None:
    import tkinter as tk

    class CustomEntry(tk.Frame):
        def insert(self, _index, text: str) -> None:
            self._text = text

        def get(self) -> str:
            return getattr(self, "_text", "")

    widget = CustomEntry(tk_root)
    widget.configure(takefocus=1)
    assert _macos._widget_accepts_tk_keys(widget) is True


def test_toplevel_destroy_closes_wakeup_pipe(tk_root) -> None:
    import os

    _macos._ensure_mac_wakeup_pipe(tk_root, MagicMock())
    read_fd = tk_root._tkwry_mac_wake_read_fd
    write_fd = tk_root._tkwry_mac_wake_write_fd
    tk_root._tkwry_mac_webviews = []
    tk_root._tkwry_mac_key_guard = False

    _macos._mac_toplevel_destroy(SimpleNamespace(widget=tk_root))

    assert not hasattr(tk_root, "_tkwry_mac_wake_read_fd")
    assert not hasattr(tk_root, "_tkwry_mac_wake_write_fd")
    with pytest.raises(OSError):
        os.read(read_fd, 1)
    with pytest.raises(OSError):
        os.write(write_fd, b"x")


def test_mac_toplevel_destroy_ignores_child_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    teardown_calls: list[tk.Misc] = []
    monkeypatch.setattr(
        _macos,
        "_teardown_macos_toplevel",
        lambda toplevel, **kwargs: teardown_calls.append(toplevel),
    )

    frame = tk.Frame(tk_root)
    _macos._mac_toplevel_destroy(SimpleNamespace(widget=frame))  # type: ignore[arg-type]

    assert teardown_calls == []


def test_unregister_tears_down_when_toplevel_already_destroyed(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    teardown_calls: list[tk.Misc] = []
    monkeypatch.setattr(
        _macos,
        "_teardown_macos_toplevel",
        lambda toplevel: teardown_calls.append(toplevel),
    )

    _macos._ensure_mac_wakeup_pipe(tk_root, MagicMock())
    web = SimpleNamespace(
        destroyed=False,
        native=MagicMock(),
        _macos_toplevel=tk_root,
    )
    tk_root._tkwry_mac_webviews = [web]  # type: ignore[list-item]
    monkeypatch.setattr(tk_root, "winfo_exists", lambda: False)

    _macos._unregister_macos_webview(web)  # type: ignore[arg-type]

    assert teardown_calls == [tk_root]


def test_focus_in_tags_widget_and_releases_tk_focus_while_web_active(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    released: list[bool] = []
    tagged: list[tk.Misc] = []
    monkeypatch.setattr(
        _macos,
        "_release_tk_keyboard_focus",
        lambda _t: released.append(True),
    )
    monkeypatch.setattr(
        _macos,
        "_prepend_mac_key_guard",
        lambda widget: tagged.append(widget),
    )
    monkeypatch.setattr(_macos, "_mac_web_input_active", lambda _t: True)
    monkeypatch.setattr(_macos, "_mac_after", lambda *_a, **_k: None)

    entry = tk.Entry(tk_root)
    tk_root._tkwry_mac_webviews = [SimpleNamespace()]  # type: ignore[list-item]
    event = SimpleNamespace(widget=entry)

    _macos._mac_focus_in_handler(event)  # type: ignore[arg-type]

    assert tagged == [entry]
    assert released == [True]
