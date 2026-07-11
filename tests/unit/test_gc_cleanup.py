"""Tests for frame-host weakref cleanup and GC-time destroy."""

from __future__ import annotations

import gc
import os
import sys
import tkinter as tk

import pytest

from tkwry import WebView
from tkwry.webview import _frame_webview_refs


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr("tkwry.webview.WebView._try_create", lambda self: None)


def test_frame_host_weakref_removed_when_webview_gc(tk_root) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    key = id(frame)
    assert key in _frame_webview_refs

    web._unbind_frame_events()
    web._cancel_deferred_callbacks()
    del web
    gc.collect()

    assert key not in _frame_webview_refs


def test_del_calls_destroy_on_tk_thread(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    destroyed: list[bool] = []
    original_destroy = web.destroy

    def track_destroy() -> None:
        destroyed.append(True)
        original_destroy()

    monkeypatch.setattr(web, "destroy", track_destroy, raising=False)
    web._unbind_frame_events()
    web.__del__()

    assert destroyed == [True]


def test_schedule_destroy_on_tk_thread_runs_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    destroyed: list[bool] = []
    original_destroy = web.destroy

    def track_destroy() -> None:
        destroyed.append(True)
        original_destroy()

    monkeypatch.setattr(web, "destroy", track_destroy, raising=False)
    web._unbind_frame_events()

    web._schedule_destroy_on_tk_thread()
    assert destroyed == []

    for _ in range(20):
        tk_root.update_idletasks()
        tk_root.update()
        if destroyed:
            break

    assert destroyed == [True]


def test_schedule_destroy_falls_back_when_after_unavailable(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    teardown_calls: list[bool] = []

    def track_teardown() -> None:
        teardown_calls.append(True)

    monkeypatch.setattr(web, "_teardown_native_if_alive", track_teardown, raising=False)

    def broken_after(_delay: int, _func) -> str:
        raise tk.TclError("application has been destroyed")

    monkeypatch.setattr(frame, "after", broken_after, raising=False)
    web._schedule_destroy_on_tk_thread()

    assert teardown_calls == [True]


def test_teardown_native_if_alive_drops_native_without_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)

    class FakeNative:
        """Native stand-in without ``destroy``; teardown drops the reference only."""

    web._webview = FakeNative()  # type: ignore[assignment]

    web._teardown_native_if_alive()

    assert web._destroyed is True
    assert web._webview is None


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS uses a separate pipe")
def test_destroy_closes_wakeup_pipe_when_last_user(tk_root) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    web._ensure_tk_wakeup_pipe()

    read_fd = tk_root._tkwry_wake_read_fd
    write_fd = tk_root._tkwry_wake_write_fd
    assert read_fd is not None
    assert write_fd is not None

    web.destroy()

    assert not hasattr(tk_root, "_tkwry_wake_read_fd")
    assert not hasattr(tk_root, "_tkwry_wake_write_fd")
    with pytest.raises(OSError):
        os.fstat(read_fd)
    with pytest.raises(OSError):
        os.fstat(write_fd)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS uses a separate pipe")
def test_destroy_keeps_wakeup_pipe_while_other_users_remain(tk_root) -> None:
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    frame_a.pack()
    frame_b.pack()
    web_a = WebView(frame_a, width=200, height=200)
    web_b = WebView(frame_b, width=200, height=200)
    web_a._ensure_tk_wakeup_pipe()
    web_b._ensure_tk_wakeup_pipe()

    read_fd = tk_root._tkwry_wake_read_fd
    web_a.destroy()

    assert tk_root._tkwry_wake_read_fd == read_fd
    assert tk_root._tkwry_wake_pipe_users == 1

    web_b.destroy()
    assert not hasattr(tk_root, "_tkwry_wake_read_fd")
