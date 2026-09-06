"""Extra coverage for :mod:`tkwry._host` wakeup / destroy drain paths."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tkwry import _host


@pytest.fixture(autouse=True)
def _reset_atexit_tracking() -> None:
    _host._atexit_destroy_drain_registered = False
    _host._atexit_destroy_toplevels.clear()
    yield
    _host._atexit_destroy_drain_registered = False
    _host._atexit_destroy_toplevels.clear()


def test_frame_host_claim_clears_dead_weakref(tk_root) -> None:
    frame = tk.Frame(tk_root)
    dead = MagicMock()
    dead.destroyed = False
    # Simulate a collected WebView: weakref returns None.
    import weakref

    class _Gone:
        pass

    gone = _Gone()
    ref = weakref.ref(gone)
    del gone
    assert ref() is None
    _host._frame_webview_refs[id(frame)] = ref  # type: ignore[assignment]

    live = MagicMock()
    live.destroyed = False
    _host._claim_frame_host(frame, live)  # type: ignore[arg-type]
    assert _host._frame_webview_refs[id(frame)]() is live
    _host._release_frame_host(frame, live)  # type: ignore[arg-type]


def test_wakeup_read_fd_readable_and_drain(tk_root) -> None:
    read_fd, write_fd = os.pipe()
    try:
        assert _host._wakeup_read_fd_readable(read_fd) is False
        os.write(write_fd, b"\x01\x02")
        assert _host._wakeup_read_fd_readable(read_fd) is True
        assert _host._drain_wakeup_read_fd(read_fd) is True
        assert _host._drain_wakeup_read_fd(read_fd) is False
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_pump_shared_and_service_wakeup(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x01")
        setattr(tk_root, "_tkwry_wake_read_fd", read_fd)
        drained: list[object] = []
        monkeypatch.setattr(
            _host,
            "_drain_toplevel_sync_hooks",
            lambda top: drained.append(top),
        )
        assert _host._pump_shared_wake_read_fd(tk_root) is True
        assert _host._wakeup_pipe_readable(tk_root) is False
        _host._service_toplevel_wakeup(tk_root)
        assert drained == [tk_root]
    finally:
        os.close(read_fd)
        os.close(write_fd)
        for attr in ("_tkwry_wake_read_fd",):
            if hasattr(tk_root, attr):
                delattr(tk_root, attr)


def test_toplevel_wakeup_fd_helpers_darwin_vs_other(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    setattr(tk_root, "_tkwry_mac_wake_read_fd", 11)
    setattr(tk_root, "_tkwry_mac_wake_write_fd", 12)
    setattr(tk_root, "_tkwry_wake_read_fd", 21)
    setattr(tk_root, "_tkwry_wake_write_fd", 22)
    monkeypatch.setattr(_host.sys, "platform", "darwin")
    assert _host._toplevel_wakeup_read_fd(tk_root) == 11
    assert _host._toplevel_wakeup_write_fd(tk_root) == 12
    monkeypatch.setattr(_host.sys, "platform", "linux")
    assert _host._toplevel_wakeup_read_fd(tk_root) == 21
    assert _host._toplevel_wakeup_write_fd(tk_root) == 22


def test_run_pending_webview_destroy_on_tk_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MagicMock()
    web._destroyed = False
    web._tk_thread_id = threading.get_ident()
    _host._run_pending_webview_destroy(web)  # type: ignore[arg-type]
    web._cancel_deferred_callbacks.assert_called_once()
    web.destroy.assert_called_once()


def test_run_pending_webview_destroy_falls_back_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web = MagicMock()
    web._destroyed = False
    web._tk_thread_id = threading.get_ident()
    web.destroy.side_effect = RuntimeError("boom")
    _host._run_pending_webview_destroy(web)  # type: ignore[arg-type]
    web._teardown_native_if_alive.assert_called_once()


def test_run_pending_webview_destroy_off_thread() -> None:
    web = MagicMock()
    web._destroyed = False
    web._tk_thread_id = threading.get_ident() + 1
    _host._run_pending_webview_destroy(web)  # type: ignore[arg-type]
    web._teardown_native_if_alive.assert_called_once()
    web.destroy.assert_not_called()


def test_drain_pending_destroy_and_sync_hooks(tk_root) -> None:
    web = MagicMock()
    web._destroyed = False
    web._tk_thread_id = threading.get_ident()

    def _destroy() -> None:
        web._destroyed = True

    web.destroy.side_effect = _destroy
    setattr(tk_root, "_tkwry_pending_destroy_webviews", [web])
    setattr(tk_root, "_tkwry_sync_hook_webviews", [])
    _host._drain_pending_destroy_webviews(tk_root)
    web.destroy.assert_called()
    assert not hasattr(tk_root, "_tkwry_pending_destroy_webviews")

    live = MagicMock()
    live._destroyed = False
    import weakref

    setattr(tk_root, "_tkwry_sync_hook_webviews", [weakref.ref(live)])
    _host._drain_toplevel_sync_hooks(tk_root)
    live._drain_sync_hooks.assert_called_once()
    live._wake_async_events.assert_called_once()


def test_ensure_wakeup_after_poll_and_tick(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[tuple] = []

    def capture_after(delay, callback, *args):
        scheduled.append((delay, callback, args))
        return "aid"

    monkeypatch.setattr(tk_root, "after", capture_after)
    read_fd, write_fd = os.pipe()
    try:
        setattr(tk_root, "_tkwry_wake_read_fd", read_fd)
        setattr(tk_root, "_tkwry_wake_pipe_users", 1)
        os.write(write_fd, b"\x01")
        hooks: list[object] = []
        monkeypatch.setattr(
            _host, "_drain_toplevel_sync_hooks", lambda top: hooks.append(top)
        )
        _host._ensure_wakeup_after_poll(tk_root)
        assert getattr(tk_root, "_tkwry_wake_after_poll") is True
        # Second call is idempotent.
        _host._ensure_wakeup_after_poll(tk_root)
        assert len(scheduled) == 1
        delay, callback, args = scheduled[0]
        assert delay == 0
        callback(*args)
        assert hooks == [tk_root]
        assert any(item[1] is _host._wakeup_after_poll_tick for item in scheduled[1:])
    finally:
        os.close(read_fd)
        os.close(write_fd)
        _host._stop_wakeup_after_poll(tk_root)


def test_wakeup_after_poll_tick_stops_without_users(tk_root) -> None:
    setattr(tk_root, "_tkwry_wake_read_fd", 1)
    setattr(tk_root, "_tkwry_wake_pipe_users", 0)
    setattr(tk_root, "_tkwry_wake_after_poll", True)
    setattr(tk_root, "_tkwry_wake_fileevent", True)
    _host._wakeup_after_poll_tick(tk_root)
    assert getattr(tk_root, "_tkwry_wake_after_poll") is False


def test_ensure_tk_wakeup_fileevent_uses_after_poll_without_handler(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_host.sys, "platform", "linux")
    read_fd, write_fd = os.pipe()
    try:
        setattr(tk_root, "_tkwry_wake_read_fd", read_fd)
        setattr(tk_root, "_tkwry_wake_pipe_users", 1)
        # Tk on some platforms lacks createfilehandler.
        if hasattr(tk_root, "createfilehandler"):
            monkeypatch.setattr(tk_root, "createfilehandler", None, raising=False)
        armed: list[object] = []
        monkeypatch.setattr(
            _host,
            "_ensure_wakeup_after_poll",
            lambda top: armed.append(top),
        )
        _host._ensure_tk_wakeup_fileevent(tk_root)
        assert armed == [tk_root]
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_track_atexit_and_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(_host.atexit, "register", lambda fn: registered.append(fn))
    # Avoid real Tk update()/update_idletasks() during atexit drain (can segfault
    # under pytest when nested with other Tk activity).
    toplevel = MagicMock()
    toplevel.update_idletasks = MagicMock()
    toplevel.update = MagicMock()
    _host._track_atexit_destroy_toplevel(toplevel)
    _host._track_atexit_destroy_toplevel(toplevel)  # idempotent
    assert len(registered) == 1
    assert len(_host._atexit_destroy_toplevels) == 1

    web = MagicMock()
    web._destroyed = False
    web._tk_thread_id = threading.get_ident()

    def _destroy() -> None:
        web._destroyed = True

    web.destroy.side_effect = _destroy
    setattr(toplevel, "_tkwry_pending_destroy_webviews", [web])
    _host._atexit_drain_pending_destroys()
    web.destroy.assert_called()


def test_release_tk_wakeup_pipe_closes_fds(tk_root) -> None:
    read_fd, write_fd = os.pipe()
    setattr(tk_root, "_tkwry_wake_read_fd", read_fd)
    setattr(tk_root, "_tkwry_wake_write_fd", write_fd)
    setattr(tk_root, "_tkwry_wake_pipe_users", 2)
    setattr(tk_root, "_tkwry_wake_fileevent", True)
    _host._release_tk_wakeup_pipe(tk_root)
    assert getattr(tk_root, "_tkwry_wake_pipe_users") == 1
    _host._release_tk_wakeup_pipe(tk_root)
    assert not hasattr(tk_root, "_tkwry_wake_read_fd")


def test_unregister_sync_hook_webview_non_darwin(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_host.sys, "platform", "linux")
    import weakref

    class _Web:
        def __init__(self) -> None:
            self._frame = SimpleNamespace(winfo_toplevel=lambda: tk_root)

    web = _Web()
    other = _Web()
    setattr(
        tk_root,
        "_tkwry_sync_hook_webviews",
        [weakref.ref(web), weakref.ref(other)],
    )
    _host._unregister_sync_hook_webview(web)  # type: ignore[arg-type]
    refs = getattr(tk_root, "_tkwry_sync_hook_webviews")
    assert len(refs) == 1
    assert refs[0]() is other

    # Last ref removed clears the attribute.
    _host._unregister_sync_hook_webview(other)  # type: ignore[arg-type]
    assert not hasattr(tk_root, "_tkwry_sync_hook_webviews")

    # TclError on toplevel lookup is ignored.
    bad = _Web()
    bad._frame = MagicMock()
    bad._frame.winfo_toplevel.side_effect = __import__("tkinter").TclError("x")
    _host._unregister_sync_hook_webview(bad)  # type: ignore[arg-type]


def test_host_edge_branches(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import weakref

    # Closed / invalid fd → readable False via OSError.
    assert _host._wakeup_read_fd_readable(-1) is False

    # Drain OSError is swallowed.
    readable_calls = {"n": 0}

    def _readable(_fd: int) -> bool:
        readable_calls["n"] += 1
        return readable_calls["n"] == 1

    monkeypatch.setattr(_host, "_wakeup_read_fd_readable", _readable)
    monkeypatch.setattr(
        _host.os, "read", lambda *_a: (_ for _ in ()).throw(OSError("x"))
    )
    assert _host._drain_wakeup_read_fd(1) is False

    # Pump helpers with missing fds.
    assert _host._pump_shared_wake_read_fd(tk_root) is False
    assert _host._wakeup_pipe_readable(tk_root) is False
    _host._pump_toplevel_wakeup_pipe(tk_root)

    # Already-destroyed pending destroy is a no-op.
    gone = MagicMock()
    gone._destroyed = True
    _host._run_pending_webview_destroy(gone)  # type: ignore[arg-type]
    gone.destroy.assert_not_called()

    # Teardown failures are swallowed.
    boom = MagicMock()
    boom._destroyed = False
    boom._tk_thread_id = threading.get_ident() + 1
    boom._teardown_native_if_alive.side_effect = RuntimeError("teardown")
    _host._run_pending_webview_destroy(boom)  # type: ignore[arg-type]

    on_thread = MagicMock()
    on_thread._destroyed = False
    on_thread._tk_thread_id = threading.get_ident()
    on_thread.destroy.side_effect = RuntimeError("destroy")
    on_thread._teardown_native_if_alive.side_effect = RuntimeError("teardown2")
    _host._run_pending_webview_destroy(on_thread)  # type: ignore[arg-type]

    # Sync hooks: dead weakrefs + empty survivors clear attr.
    setattr(tk_root, "_tkwry_sync_hook_webviews", [weakref.ref(MagicMock())])

    # Force dead refs by not keeping MagicMock alive — use a real gone object.
    class _Tmp:
        pass

    tmp = _Tmp()
    setattr(tk_root, "_tkwry_sync_hook_webviews", [weakref.ref(tmp)])
    del tmp
    _host._drain_toplevel_sync_hooks(tk_root)
    assert not hasattr(tk_root, "_tkwry_sync_hook_webviews")

    # Pending destroy: skip destroyed, keep off-thread, keep failed destroy.
    dead = MagicMock()
    dead._destroyed = True
    off = MagicMock()
    off._destroyed = False
    off._tk_thread_id = threading.get_ident() + 99
    stuck = MagicMock()
    stuck._destroyed = False
    stuck._tk_thread_id = threading.get_ident()
    stuck.destroy.side_effect = lambda: None  # does not flip _destroyed
    setattr(tk_root, "_tkwry_pending_destroy_webviews", [dead, off, stuck])
    _host._drain_pending_destroy_webviews(tk_root)
    pending = getattr(tk_root, "_tkwry_pending_destroy_webviews")
    assert off in pending and stuck in pending and dead not in pending


def test_atexit_drain_leftovers_and_tcl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weakref

    # Dead weakref is dropped.
    class _Gone:
        pass

    gone = _Gone()
    _host._atexit_destroy_toplevels.append(weakref.ref(gone))
    del gone

    toplevel = MagicMock()
    toplevel.update_idletasks.side_effect = __import__("tkinter").TclError("dead")
    leftover = MagicMock()
    leftover._destroyed = False
    leftover._tk_thread_id = threading.get_ident()

    def _destroy() -> None:
        leftover._destroyed = True

    leftover.destroy.side_effect = _destroy
    # After TclError break, leftovers still present → force destroy path.
    setattr(toplevel, "_tkwry_pending_destroy_webviews", [leftover])
    _host._atexit_destroy_toplevels.append(weakref.ref(toplevel))
    _host._atexit_drain_pending_destroys()
    leftover.destroy.assert_called()


def test_wakeup_poll_error_paths(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    # No read fd → stop.
    setattr(tk_root, "_tkwry_wake_after_poll", True)
    _host._wakeup_after_poll_tick(tk_root)
    assert getattr(tk_root, "_tkwry_wake_after_poll") is False

    # winfo_exists false / TclError.
    setattr(tk_root, "_tkwry_wake_read_fd", 1)
    setattr(tk_root, "_tkwry_wake_pipe_users", 1)
    setattr(tk_root, "_tkwry_wake_after_poll", True)
    monkeypatch.setattr(tk_root, "winfo_exists", lambda: False)
    _host._wakeup_after_poll_tick(tk_root)
    assert getattr(tk_root, "_tkwry_wake_after_poll") is False

    setattr(tk_root, "_tkwry_wake_read_fd", 1)
    setattr(tk_root, "_tkwry_wake_pipe_users", 1)
    setattr(tk_root, "_tkwry_wake_after_poll", True)
    monkeypatch.setattr(
        tk_root,
        "winfo_exists",
        lambda: (_ for _ in ()).throw(tk.TclError("x")),
    )
    _host._wakeup_after_poll_tick(tk_root)
    assert getattr(tk_root, "_tkwry_wake_after_poll") is False

    # after() TclError while ensuring / ticking.
    monkeypatch.setattr(tk_root, "winfo_exists", lambda: True)
    monkeypatch.setattr(
        tk_root, "after", lambda *_a, **_k: (_ for _ in ()).throw(tk.TclError("x"))
    )
    monkeypatch.setattr(_host, "_drain_wakeup_read_fd", lambda _fd: False)
    setattr(tk_root, "_tkwry_wake_read_fd", 1)
    setattr(tk_root, "_tkwry_wake_pipe_users", 1)
    setattr(tk_root, "_tkwry_wake_after_poll", True)
    _host._wakeup_after_poll_tick(tk_root)

    setattr(tk_root, "_tkwry_wake_after_poll", False)
    _host._ensure_wakeup_after_poll(tk_root)
    assert getattr(tk_root, "_tkwry_wake_after_poll") is False


def test_ensure_fileevent_and_release_error_paths(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr(_host.sys, "platform", "darwin")
    _host._ensure_tk_wakeup_fileevent(tk_root)  # early return on darwin

    monkeypatch.setattr(_host.sys, "platform", "linux")
    _host._ensure_tk_wakeup_fileevent(tk_root)  # no read fd

    read_fd, write_fd = os.pipe()
    try:
        setattr(tk_root, "_tkwry_wake_read_fd", read_fd)
        # createfilehandler raises → after-poll fallback.
        monkeypatch.setattr(
            tk_root,
            "createfilehandler",
            lambda *_a, **_k: (_ for _ in ()).throw(tk.TclError("x")),
            raising=False,
        )
        armed: list[object] = []
        monkeypatch.setattr(
            _host, "_ensure_wakeup_after_poll", lambda top: armed.append(top)
        )
        if hasattr(tk_root, "_tkwry_wake_fileevent"):
            delattr(tk_root, "_tkwry_wake_fileevent")
        _host._ensure_tk_wakeup_fileevent(tk_root)
        assert armed == [tk_root]

        # Successful createfilehandler path.
        armed.clear()
        if hasattr(tk_root, "_tkwry_wake_fileevent"):
            delattr(tk_root, "_tkwry_wake_fileevent")
        monkeypatch.setattr(
            tk_root, "createfilehandler", lambda *_a, **_k: None, raising=False
        )
        _host._ensure_tk_wakeup_fileevent(tk_root)
        assert getattr(tk_root, "_tkwry_wake_fileevent") is True

        # Release with deletefilehandler / close errors.
        setattr(tk_root, "_tkwry_wake_write_fd", write_fd)
        setattr(tk_root, "_tkwry_wake_pipe_users", 1)
        setattr(tk_root, "_tkwry_wake_fileevent", True)
        monkeypatch.setattr(
            tk_root,
            "deletefilehandler",
            lambda *_a, **_k: (_ for _ in ()).throw(tk.TclError("x")),
            raising=False,
        )
        monkeypatch.setattr(
            _host.os, "close", lambda _fd: (_ for _ in ()).throw(OSError("x"))
        )
        _host._release_tk_wakeup_pipe(tk_root)
        assert not hasattr(tk_root, "_tkwry_wake_read_fd")
    finally:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    # users is None → no-op
    _host._release_tk_wakeup_pipe(tk_root)


def test_register_sync_hook_webview(tk_root) -> None:
    web = MagicMock()
    _host._register_sync_hook_webview(tk_root, web)  # type: ignore[arg-type]
    refs = getattr(tk_root, "_tkwry_sync_hook_webviews")
    assert refs and refs[0]() is web
