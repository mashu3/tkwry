"""Tests for frame-host weakref cleanup and GC-time destroy."""

from __future__ import annotations

import gc
import tkinter as tk

import pytest

from tkwry import WebView
from tkwry.webview import _frame_webview_refs


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._runtime.GtkPump.attach", lambda _widget: None)
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
