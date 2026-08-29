"""Tests for queue overflow drop reporting."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import QueueDropCounts, WebView
from tkwry._rpc_api import MAX_RPC_STREAM_PENDING


@pytest.fixture(autouse=True)
def _noop_linux_gtk_pump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr("tkwry._linux.GtkPump.detach", lambda _widget: None)
    monkeypatch.setattr(
        "tkwry._linux.pump_gtk_events", lambda **_kwargs: False, raising=False
    )


def test_take_queue_drop_counts_before_native_returns_zeros(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0, 0)

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_delegates_to_native(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.take_queue_drop_counts.return_value = (1, 2, 3, 4, 5, 6)
    web._webview = native

    assert web.take_queue_drop_counts() == (1, 2, 3, 4, 5, 6)
    native.take_queue_drop_counts.assert_called_once_with()

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_after_destroy_returns_zeros(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.destroy()

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0, 0)

    frame.destroy()


def test_take_queue_drop_counts_reports_local_eval_drops_on_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._register_pending_eval(lambda _r: None, None)
    web._register_pending_eval(lambda _r: None, None)

    web.destroy()

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 2, 0)

    frame.destroy()


def test_take_queue_drop_stats_includes_download_and_stream(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.take_queue_drop_stats.return_value = (1, 2, 3, 4, 5, 6, 7)
    web._webview = native
    for i in range(MAX_RPC_STREAM_PENDING):
        assert web._enqueue_rpc_stream_chunk("s1", i) is True
    assert web._enqueue_rpc_stream_chunk("s1", "overflow") is False
    assert web._enqueue_rpc_stream_chunk("s1", "again") is False

    stats = web.take_queue_drop_stats()
    assert stats == QueueDropCounts(
        ipc=1,
        page_load=2,
        title=3,
        drag_drop=4,
        eval=5,
        rpc=6,
        download_complete=7,
        rpc_stream=2,
    )
    assert stats.download_complete == 7
    assert stats.rpc_stream == 2
    native.take_queue_drop_stats.assert_called_once_with()
    # Second take clears stream drops.
    assert web.take_queue_drop_stats().rpc_stream == 0

    web.destroy()
    frame.destroy()


def test_take_queue_drop_stats_after_destroy_keeps_local_stream_drops(
    tk_root,
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    for i in range(MAX_RPC_STREAM_PENDING):
        web._enqueue_rpc_stream_chunk("s1", i)
    web._enqueue_rpc_stream_chunk("s1", "overflow")
    web.destroy()

    stats = web.take_queue_drop_stats()
    assert stats == QueueDropCounts(0, 0, 0, 0, 0, 0, 0, 1)
    assert web.take_queue_drop_stats().rpc_stream == 0

    frame.destroy()


def test_take_queue_drop_counts_does_not_clear_rpc_stream(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    for i in range(MAX_RPC_STREAM_PENDING):
        web._enqueue_rpc_stream_chunk("s1", i)
    web._enqueue_rpc_stream_chunk("s1", "overflow")

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0, 0)
    assert web.take_queue_drop_stats().rpc_stream == 1

    web.destroy()
    frame.destroy()
