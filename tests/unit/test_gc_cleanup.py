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


def test_del_prints_traceback_when_destroy_fails(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    teardown_calls: list[bool] = []

    def boom() -> None:
        raise RuntimeError("destroy failed in __del__")

    def track_teardown() -> None:
        teardown_calls.append(True)

    monkeypatch.setattr(web, "destroy", boom, raising=False)
    monkeypatch.setattr(web, "_teardown_native_if_alive", track_teardown, raising=False)
    web._unbind_frame_events()
    web.__del__()

    err = capsys.readouterr().err
    assert "destroy failed in __del__" in err
    assert "RuntimeError" in err
    assert teardown_calls == [True]


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


def test_schedule_destroy_from_off_thread_queues_wakeup(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off-thread scheduling queues destroy and wakes the Tk thread (no real thread)."""
    import threading

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
    toplevel = frame.winfo_toplevel()
    if sys.platform == "darwin":
        setattr(toplevel, "_tkwry_mac_wake_read_fd", 1)
        setattr(toplevel, "_tkwry_mac_wake_write_fd", 2)
    else:
        setattr(toplevel, "_tkwry_wake_write_fd", 2)
    write_calls: list[bytes] = []
    fallback: list[bool] = []
    simulate_off_thread = [False]
    real_get_ident = threading.get_ident

    monkeypatch.setattr(os, "write", lambda _fd, data: write_calls.append(data))
    monkeypatch.setattr(
        web,
        "_teardown_native_if_alive",
        lambda: fallback.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        "tkwry.webview.threading.get_ident",
        lambda: web._tk_thread_id + 1 if simulate_off_thread[0] else real_get_ident(),
    )

    simulate_off_thread[0] = True
    web._schedule_destroy_on_tk_thread()
    simulate_off_thread[0] = False

    assert fallback == []
    assert write_calls == [b"\x01"]
    pending = getattr(toplevel, "_tkwry_pending_destroy_webviews", [])
    assert len(pending) == 1
    assert pending[0]() is web

    from tkwry.webview import _drain_pending_destroy_webviews

    _drain_pending_destroy_webviews(toplevel)
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


def test_atexit_drain_runs_destroy_on_tk_thread(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weakref

    from tkwry import webview as webview_mod

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
    toplevel = frame.winfo_toplevel()
    setattr(toplevel, "_tkwry_pending_destroy_webviews", [weakref.ref(web)])
    previous = list(webview_mod._atexit_destroy_toplevels)
    webview_mod._atexit_destroy_toplevels[:] = [weakref.ref(toplevel)]
    try:
        webview_mod._atexit_drain_pending_destroys()
    finally:
        webview_mod._atexit_destroy_toplevels[:] = previous

    assert destroyed == [True]
    assert web.destroyed is True


def test_atexit_leftover_uses_teardown_not_bare_force(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-Tk atexit leftovers must use terminal teardown, not bare force."""
    import weakref

    from tkwry import webview as webview_mod

    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    web._register_pending_eval(lambda _r: None, None)
    epoch_before = web._eval_epoch

    teardown_calls: list[bool] = []
    force_calls: list[bool] = []

    def track_teardown() -> None:
        teardown_calls.append(True)
        WebView._teardown_native_if_alive(web)

    monkeypatch.setattr(
        webview_mod.threading,
        "get_ident",
        lambda: web._tk_thread_id + 1,
    )
    monkeypatch.setattr(web, "_teardown_native_if_alive", track_teardown, raising=False)
    monkeypatch.setattr(
        web,
        "_force_native_teardown",
        lambda: force_calls.append(True),
        raising=False,
    )
    # Skip the Tk update loop so leftovers hit _run_pending_webview_destroy.
    monkeypatch.setattr(
        tk_root,
        "update",
        lambda: (_ for _ in ()).throw(tk.TclError("closed")),
        raising=False,
    )

    toplevel = frame.winfo_toplevel()
    setattr(toplevel, "_tkwry_pending_destroy_webviews", [weakref.ref(web)])
    previous = list(webview_mod._atexit_destroy_toplevels)
    webview_mod._atexit_destroy_toplevels[:] = [weakref.ref(toplevel)]
    try:
        webview_mod._atexit_drain_pending_destroys()
    finally:
        webview_mod._atexit_destroy_toplevels[:] = previous

    assert teardown_calls == [True]
    assert force_calls == []
    assert web._destroyed is True
    assert web._eval_epoch == epoch_before + 1
    assert not web._pending_eval_tokens


def test_run_pending_webview_destroy_on_tk_thread(tk_root) -> None:
    from tkwry.webview import _run_pending_webview_destroy

    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    web._unbind_frame_events()

    _run_pending_webview_destroy(web)

    assert web.destroyed is True


def test_run_pending_webview_destroy_off_thread_uses_teardown(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry import webview as webview_mod
    from tkwry.webview import _run_pending_webview_destroy

    frame = tk.Frame(tk_root)
    frame.pack()
    web = WebView(frame, width=400, height=300)
    teardown_calls: list[bool] = []
    destroy_calls: list[bool] = []

    monkeypatch.setattr(
        webview_mod.threading,
        "get_ident",
        lambda: web._tk_thread_id + 1,
    )
    monkeypatch.setattr(
        web,
        "_teardown_native_if_alive",
        lambda: teardown_calls.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        web,
        "destroy",
        lambda: destroy_calls.append(True),
        raising=False,
    )

    _run_pending_webview_destroy(web)

    assert teardown_calls == [True]
    assert destroy_calls == []


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
