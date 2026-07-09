"""Tests for Gtk/WebKitGTK runtime helpers."""

from __future__ import annotations

import sys

import pytest

from tkwry._runtime import GtkPump, _gtk_pump_tick


@pytest.fixture(autouse=True)
def _clear_gtk_pumps() -> None:
    GtkPump._by_root_id.clear()
    GtkPump._widget_attachments.clear()
    yield
    GtkPump._by_root_id.clear()
    GtkPump._widget_attachments.clear()


def test_gtk_pump_tick_skips_when_stopped(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda: calls.append(1),
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_id[pump._root_id] = pump
    pump._active = True
    pump.stop()

    _gtk_pump_tick(pump._root_id)

    assert calls == []


def test_gtk_pump_tick_stops_when_root_destroyed(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda: calls.append(1),
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_id[pump._root_id] = pump
    pump._active = True
    root_id = pump._root_id
    tk_root.destroy()

    _gtk_pump_tick(root_id)

    assert calls == []
    assert root_id not in GtkPump._by_root_id


def test_gtk_pump_schedules_next_tick_with_root_id_only(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[object] = []
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda: None,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_id[pump._root_id] = pump
    pump._active = True

    def capture_after(_delay: int, callback) -> str:
        scheduled.append(callback)
        return "after-id"

    monkeypatch.setattr(pump._root, "after", capture_after)

    _gtk_pump_tick(pump._root_id)

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
        lambda: None,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_id[pump._root_id] = pump
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
        lambda: calls.append(1),
        raising=False,
    )

    pump1 = GtkPump(tk_root)
    GtkPump._by_root_id[pump1._root_id] = pump1
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
    GtkPump._by_root_id[pump2._root_id] = pump2
    pump2._active = True

    calls.clear()
    _gtk_pump_tick(pump2._root_id)

    assert calls == [1]
    pump2.stop()


@pytest.mark.skipif(sys.platform != "linux", reason="GtkPump is Linux-only")
def test_gtk_pump_attach_detach_stops_when_last_webview_gone(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")

    GtkPump.attach(tk_root)
    GtkPump.attach(tk_root)
    root_id = tk_root.winfo_id()
    pump = GtkPump._by_root_id[root_id]
    assert pump._refcount == 2
    assert pump._active

    GtkPump.detach(tk_root)
    assert root_id in GtkPump._by_root_id
    assert pump._refcount == 1
    assert pump._active

    GtkPump.detach(tk_root)
    assert root_id not in GtkPump._by_root_id
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
    root_id = tk_root.winfo_id()
    pump = GtkPump._by_root_id[root_id]
    assert pump._refcount == 1

    frame.destroy()
    tk_root.update_idletasks()

    GtkPump.detach(frame)

    assert root_id not in GtkPump._by_root_id
    assert not pump._active
    assert id(frame) not in GtkPump._widget_attachments


def test_gtk_pump_stop_does_not_zero_refcount(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events",
        lambda: None,
        raising=False,
    )

    pump = GtkPump(tk_root)
    GtkPump._by_root_id[pump._root_id] = pump
    pump._active = True
    pump._refcount = 2

    pump.stop()

    assert pump._refcount == 2
    assert pump._root_id not in GtkPump._by_root_id
