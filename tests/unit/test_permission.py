"""Unit coverage for create-time permission_handler sync hook."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import PermissionKind, PermissionResponse, WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_permission_enum_members() -> None:
    assert PermissionKind.Camera != PermissionKind.Microphone
    assert PermissionResponse.Allow != PermissionResponse.Deny
    assert PermissionResponse.Default == PermissionResponse.Default


def test_native_permission_runs_handler_on_tk_thread(tk_root) -> None:
    frame = tk.Frame(tk_root)
    seen: list[tuple[int, PermissionKind]] = []

    def handler(kind: PermissionKind) -> PermissionResponse:
        seen.append((threading.get_ident(), kind))
        return PermissionResponse.Allow

    web = WebView(frame, html="<p>p</p>", permission_handler=handler)
    tk_ident = threading.get_ident()
    assert web._native_permission(PermissionKind.Microphone) is PermissionResponse.Allow
    assert seen == [(tk_ident, PermissionKind.Microphone)]
    web.destroy()
    frame.destroy()


def test_permission_handler_bad_return_denies(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        html="<p>p</p>",
        permission_handler=lambda _k: "nope",  # type: ignore[arg-type, return-value]
    )
    assert web._native_permission(PermissionKind.Camera) is PermissionResponse.Deny
    web.destroy()
    frame.destroy()


def test_permission_handler_exception_denies(tk_root) -> None:
    frame = tk.Frame(tk_root)

    def boom(_kind: PermissionKind) -> PermissionResponse:
        raise RuntimeError("boom")

    web = WebView(frame, html="<p>p</p>", permission_handler=boom)
    assert web._native_permission(PermissionKind.Other) is PermissionResponse.Deny
    web.destroy()
    frame.destroy()


def test_try_create_passes_on_permission(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)

    def handler(_kind: PermissionKind) -> PermissionResponse:
        return PermissionResponse.Default

    web = WebView(
        frame, html="<p>p</p>", width=200, height=150, permission_handler=handler
    )
    captured: dict[str, object] = {}

    def fake_native(*_args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        native = MagicMock()
        native.is_alive.return_value = False
        return native

    monkeypatch.setattr("tkwry.webview.NativeWebView", fake_native)
    monkeypatch.setattr(web, "_sync_bounds", lambda: True)
    monkeypatch.setattr(web, "_maybe_fire_ready", lambda: None)
    monkeypatch.setattr(web, "_schedule_initial_load", lambda: None)
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    monkeypatch.setattr(web, "_needs_event_poll", lambda: False)
    if sys.platform == "darwin":
        monkeypatch.setattr("tkwry.webview._ensure_mac_wakeup_pipe", lambda *_a: None)
        monkeypatch.setattr("tkwry.webview._ensure_mac_pump", lambda *_a: None)

    web._try_create()
    cb = captured.get("on_permission")
    assert cb is not None
    assert getattr(cb, "__self__", None) is web
    assert getattr(cb, "__func__", None) is WebView._native_permission
    web.destroy()
    frame.destroy()
