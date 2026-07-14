"""Tests for Linux Gtk/WebKitGTK helpers."""

from __future__ import annotations

import sys

import pytest

from tkwry._linux import _PUMP_ERROR_LIMIT, GtkPump, _gtk_pump_tick


@pytest.fixture(autouse=True)
def _clear_gtk_pumps() -> None:
    GtkPump._by_root_key.clear()
    GtkPump._widget_attachments.clear()
    GtkPump._pending_attach.clear()
    yield
    GtkPump._by_root_key.clear()
    GtkPump._widget_attachments.clear()
    GtkPump._pending_attach.clear()


def test_gtk_pump_tick_skips_when_stopped(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: calls.append(1) or False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    pump.stop()

    _gtk_pump_tick(pump._root_key)

    assert calls == []


def test_gtk_pump_tick_stops_when_root_destroyed(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: calls.append(1) or False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    root_key = pump._root_key
    tk_root.destroy()

    _gtk_pump_tick(root_key)

    assert calls == []
    assert root_key not in GtkPump._by_root_key


def test_gtk_pump_schedules_next_tick_with_root_key_only(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[object] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True

    def capture_after(_delay: int, callback) -> str:
        scheduled.append(callback)
        return "after-id"

    monkeypatch.setattr(pump._root, "after", capture_after)

    _gtk_pump_tick(pump._root_key)

    assert len(scheduled) == 1
    scheduled[0]()
    assert pump._tick_after_id == "after-id"
    pump.stop()
    assert pump._tick_after_id is None


def test_gtk_pump_stop_cancels_pending_tick(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled: list[str] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    pump._tick_after_id = "pending-id"
    monkeypatch.setattr(
        pump._root,
        "after_cancel",
        lambda after_id: cancelled.append(after_id),
    )

    pump.stop()

    assert cancelled == ["pending-id"]
    assert pump._tick_after_id is None


def test_gtk_pump_stale_tick_does_not_drive_reattached_pump(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: calls.append(1) or False,
        raising=False,
    )

    pump1 = GtkPump(tk_root)
    GtkPump._by_root_key[pump1._root_key] = pump1
    pump1._active = True
    cancelled: list[str] = []
    monkeypatch.setattr(pump1._root, "after", lambda *_a, **_k: "tick-id")
    monkeypatch.setattr(
        pump1._root,
        "after_cancel",
        lambda after_id: cancelled.append(after_id),
    )
    pump1._schedule_tick(10)

    pump1.stop()
    assert cancelled == ["tick-id"]

    pump2 = GtkPump(tk_root)
    GtkPump._by_root_key[pump2._root_key] = pump2
    pump2._active = True

    calls.clear()
    _gtk_pump_tick(pump2._root_key)

    assert calls == [1]
    pump2.stop()


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_gtk_pump_attach_detach_stops_when_last_webview_gone(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    GtkPump.attach(frame_a)
    GtkPump.attach(frame_b)
    root_key = id(tk_root)
    pump = GtkPump._by_root_key[root_key]
    assert pump._refcount == 2
    assert pump._active

    GtkPump.detach(frame_a)
    assert root_key in GtkPump._by_root_key
    assert pump._refcount == 1
    assert pump._active

    GtkPump.detach(frame_b)
    assert root_key not in GtkPump._by_root_key
    assert not pump._active


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_gtk_pump_detach_after_frame_destroy_stops_pump(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    frame = tk.Frame(tk_root)
    frame.pack()
    tk_root.update_idletasks()

    GtkPump.attach(frame)
    root_key = id(tk_root)
    pump = GtkPump._by_root_key[root_key]
    assert pump._refcount == 1

    frame.destroy()
    tk_root.update_idletasks()

    GtkPump.detach(frame)

    assert root_key not in GtkPump._by_root_key
    assert not pump._active
    assert id(frame) not in GtkPump._widget_attachments


def test_gtk_pump_stop_does_not_zero_refcount(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda max_iterations=None: False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    pump._refcount = 2

    pump.stop()

    assert pump._refcount == 2
    assert pump._root_key not in GtkPump._by_root_key


def test_gtk_pump_tick_keeps_pumping_after_single_pump_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    scheduled: list[object] = []

    def flaky_pump(**_kwargs: object) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return False

    monkeypatch.setattr("tkwry._linux.pump_gtk_events", flaky_pump, raising=False)

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    monkeypatch.setattr(
        pump._root,
        "after",
        lambda _delay, callback: scheduled.append(callback) or "after-id",
    )

    _gtk_pump_tick(pump._root_key)

    assert calls["n"] == 1
    assert pump._active
    assert pump._consecutive_errors == 1
    assert scheduled
    pump.stop()


def test_gtk_pump_tick_keeps_pumping_after_repeated_pump_errors(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = {"n": 0}
    scheduled: list[int] = []

    def always_fail(**_kwargs: object) -> bool:
        calls["n"] += 1
        raise RuntimeError("gtk broken")

    monkeypatch.setattr("tkwry._linux.pump_gtk_events", always_fail, raising=False)

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    pump._refcount = 1
    root_key = pump._root_key
    monkeypatch.setattr(
        pump._root,
        "after",
        lambda delay, _callback: scheduled.append(delay) or "after-id",
    )

    for _ in range(_PUMP_ERROR_LIMIT):
        GtkPump._by_root_key[root_key]._active = True
        _gtk_pump_tick(root_key)

    assert calls["n"] == _PUMP_ERROR_LIMIT
    assert pump._active
    assert root_key in GtkPump._by_root_key
    assert scheduled
    assert scheduled[-1] >= 10
    assert "retrying in" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_purge_stale_pump_drops_destroyed_root(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    monkeypatch.setattr(pump._root, "winfo_exists", lambda: False)

    GtkPump._purge_stale_pump(pump._root_key)

    assert pump._root_key not in GtkPump._by_root_key
    assert not pump._active


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_attach_schedules_retry_when_attach_raises_tcl_error(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    idle_callbacks: list[object] = []
    monkeypatch.setattr(
        tk_root,
        "after_idle",
        lambda callback: idle_callbacks.append(callback),
    )
    real_increment = GtkPump._increment_root_refcount

    def failing_increment(widget: tk.Misc, root_id: int, count: int) -> None:
        raise tk.TclError("not ready")

    monkeypatch.setattr(
        GtkPump, "_increment_root_refcount", staticmethod(failing_increment)
    )

    GtkPump.attach(frame)

    assert id(frame) in GtkPump._pending_attach
    assert id(frame) not in GtkPump._widget_attachments

    monkeypatch.setattr(
        GtkPump, "_increment_root_refcount", staticmethod(real_increment)
    )
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    idle_callbacks[0]()

    assert id(frame) in GtkPump._widget_attachments
    GtkPump.detach(frame)


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_attach_schedules_retry_when_root_key_unavailable(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    frame = tk.Frame(tk_root)
    attempts = {"n": 0}
    real_resolve = GtkPump._resolve_root_key

    def resolve(widget: tk.Misc) -> int | None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return None
        return real_resolve(widget)

    idle_callbacks: list[object] = []
    monkeypatch.setattr(GtkPump, "_resolve_root_key", staticmethod(resolve))
    monkeypatch.setattr(
        tk_root,
        "after_idle",
        lambda callback: idle_callbacks.append(callback),
    )

    GtkPump.attach(frame)

    assert attempts["n"] == 1
    assert id(frame) in GtkPump._pending_attach
    assert id(frame) not in GtkPump._widget_attachments

    idle_callbacks[0]()

    assert id(frame) in GtkPump._widget_attachments
    assert GtkPump._by_root_key[id(tk_root)]._refcount == 1
    GtkPump.detach(frame)


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_attach_migrates_widget_when_reparented(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    frame = tk.Frame(tk_root)
    old_root_key = 111
    new_root_key = 222
    root_ids = iter([old_root_key, new_root_key, new_root_key])

    monkeypatch.setattr(
        GtkPump,
        "_resolve_root_key",
        staticmethod(lambda _widget: next(root_ids)),
    )

    GtkPump.attach(frame)
    assert GtkPump._widget_attachments[id(frame)] == (old_root_key, 1)
    assert GtkPump._by_root_key[old_root_key]._refcount == 1

    GtkPump.attach(frame)

    assert GtkPump._widget_attachments[id(frame)] == (new_root_key, 1)
    assert old_root_key not in GtkPump._by_root_key
    assert GtkPump._by_root_key[new_root_key]._refcount == 1

    GtkPump.detach(frame)


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_reparent_keeps_pump_alive_for_remaining_widgets(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    old_root_key = 111
    new_root_key = 222
    root_ids = iter([old_root_key, old_root_key, new_root_key])

    monkeypatch.setattr(
        GtkPump,
        "_resolve_root_key",
        staticmethod(lambda _widget: next(root_ids)),
    )

    GtkPump.attach(frame_a)
    GtkPump.attach(frame_b)
    assert GtkPump._by_root_key[old_root_key]._refcount == 2

    GtkPump.attach(frame_a)

    assert GtkPump._widget_attachments[id(frame_a)] == (new_root_key, 1)
    assert GtkPump._widget_attachments[id(frame_b)] == (old_root_key, 1)
    assert GtkPump._by_root_key[old_root_key]._refcount == 1
    assert GtkPump._by_root_key[new_root_key]._refcount == 1

    GtkPump.detach(frame_a)
    GtkPump.detach(frame_b)


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_ensure_attached_is_idempotent(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    GtkPump.ensure_attached(tk_root)
    GtkPump.ensure_attached(tk_root)

    pump = GtkPump._by_root_key[id(tk_root)]
    assert pump._refcount == 1
    GtkPump.detach(tk_root)


def test_gtk_pump_tick_uses_fast_schedule_when_backlog(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[int] = []
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: True,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    monkeypatch.setattr(
        pump._root,
        "after",
        lambda delay, _callback: delays.append(delay) or "after-id",
    )

    _gtk_pump_tick(pump._root_key)

    assert delays == [0]
    assert pump._consecutive_busy == 1
    pump.stop()


def test_gtk_pump_tick_yields_after_consecutive_busy_streak(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry._linux import _PUMP_MAX_CONSECUTIVE_BUSY, _PUMP_TICK_IDLE_MS

    delays: list[int] = []
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: True,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    monkeypatch.setattr(
        pump._root,
        "after",
        lambda delay, _callback: delays.append(delay) or "after-id",
    )

    for _ in range(_PUMP_MAX_CONSECUTIVE_BUSY):
        _gtk_pump_tick(pump._root_key)
    assert delays == [0] * _PUMP_MAX_CONSECUTIVE_BUSY

    _gtk_pump_tick(pump._root_key)
    assert delays[-1] == _PUMP_TICK_IDLE_MS
    assert pump._consecutive_busy == 0
    pump.stop()


def test_pump_gtk_events_scales_bursts_with_refcount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tkwry._linux import pump_gtk_events

    calls: list[int | None] = []

    def track_pump(max_iterations: int | None = None) -> bool:
        calls.append(max_iterations)
        return True

    monkeypatch.setattr("tkwry._core.pump_events", track_pump, raising=False)

    assert pump_gtk_events(refcount=3) is True
    assert len(calls) == 3
    assert all(limit == 512 for limit in calls)


def test_gtk_pump_tick_pumps_multiple_passes_when_backlog(
    tk_root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def backlog_then_idle(**_kwargs: object) -> bool:
        calls["n"] += 1
        return calls["n"] < 3

    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events", backlog_then_idle, raising=False
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    monkeypatch.setattr(
        pump._root,
        "after",
        lambda delay, _callback: "after-id",
    )

    _gtk_pump_tick(pump._root_key)

    assert calls["n"] == 3
    pump.stop()


def test_gtk_pump_falls_back_to_after_idle_when_after_raises(
    tk_root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idle_callbacks: list[object] = []

    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True

    def failing_after(_delay: int, _callback) -> str:
        import tkinter as tk

        raise tk.TclError("interp gone")

    monkeypatch.setattr(pump._root, "after", failing_after)
    monkeypatch.setattr(
        pump._root,
        "after_idle",
        lambda callback: idle_callbacks.append(callback),
    )

    pump._schedule_tick(10)

    assert idle_callbacks
    assert not pump._recovery_pending
    pump.stop()


def test_gtk_pump_marks_recovery_pending_when_all_schedulers_fail(
    tk_root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkinter as tk

    calls: list[int] = []

    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: calls.append(1) or False,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_key[pump._root_key] = pump
    pump._active = True
    pump._refcount = 1

    def failing_after(_delay: int, _callback) -> str:
        raise tk.TclError("interp gone")

    def failing_idle(_callback) -> None:
        raise tk.TclError("interp gone")

    monkeypatch.setattr(pump._root, "after", failing_after)
    monkeypatch.setattr(pump._root, "after_idle", failing_idle)
    monkeypatch.setattr(
        pump._root,
        "winfo_exists",
        lambda: (_ for _ in ()).throw(tk.TclError("destroyed")),
    )

    pump._schedule_tick(10)

    assert pump._recovery_pending

    pump._resume_if_recovery_pending()

    assert pump._active
    assert pump._recovery_pending
    pump.stop()


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_attach_resumes_recovery_pending_pump(
    tk_root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    frame = tk.Frame(tk_root)
    GtkPump.attach(frame)
    pump = GtkPump._by_root_key[id(tk_root)]
    pump._recovery_pending = True
    pump._active = False

    GtkPump.attach(frame)

    assert not pump._recovery_pending
    assert pump._active
    GtkPump.detach(frame)


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_attach_restarts_paused_pump(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter as tk

    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events",
        lambda **_kwargs: False,
        raising=False,
    )
    scheduled: list[int] = []
    monkeypatch.setattr(
        tk_root,
        "after",
        lambda delay, _callback: scheduled.append(delay) or "after-id",
    )

    frame = tk.Frame(tk_root)
    GtkPump.attach(frame)
    pump = GtkPump._by_root_key[id(tk_root)]
    pump._active = False

    GtkPump.attach(frame)

    assert pump._active
    assert scheduled
    GtkPump.detach(frame)
