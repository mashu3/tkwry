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
