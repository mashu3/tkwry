"""Tests for Gtk/WebKitGTK runtime helpers."""

from __future__ import annotations

import pytest

from tkwry._runtime import GtkPump, _gtk_pump_tick


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
    callback = scheduled[0]
    assert getattr(callback, "__defaults__", ()) == (pump._root_id,)
    pump.stop()
