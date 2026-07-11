"""Helpers for stubbing Linux-only Gtk/WebKitGTK runtime in unit tests."""

from __future__ import annotations

import pytest


def noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable ``tkwry._linux`` helpers; cross-platform unit tests use Tk polling."""
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr(
        "tkwry._linux.GtkPump.attach", lambda _widget: None, raising=False
    )
    monkeypatch.setattr(
        "tkwry._linux.GtkPump.detach", lambda _widget: None, raising=False
    )
    monkeypatch.setattr(
        "tkwry._linux.GtkPump.ensure_attached",
        lambda _widget: None,
        raising=False,
    )
