"""Unit tests for WebView.expose registration rules."""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView, rpc_cancelled


def test_expose_rejects_duplicate_names(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def greet() -> str:
        return "hi"

    with pytest.raises(ValueError, match="already exposed"):

        @web.expose
        def greet() -> str:  # noqa: F811
            return "bye"

    @web.expose(replace=True)
    def greet() -> str:  # noqa: F811
        return "ok"

    assert web.unexpose("greet") is True
    assert web.unexpose("greet") is False

    web.destroy()
    frame.destroy()


def test_expose_thread_conflicts_with_main(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    with pytest.raises(ValueError, match="conflicts"):

        @web.expose(thread=True, run_in="main")
        def bad() -> None:
            return None

    web.destroy()
    frame.destroy()


@pytest.mark.parametrize(
    "bad_timeout",
    [0, -1, float("nan"), float("inf"), float("-inf"), True],
)
def test_expose_rejects_non_finite_timeout(tk_root, bad_timeout: float | bool) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    with pytest.raises(ValueError, match="timeout must be a finite positive"):

        @web.expose(thread=True, timeout=bad_timeout)  # type: ignore[arg-type]
        def slow() -> None:
            return None

    web.destroy()
    frame.destroy()


def test_rpc_delivered_from_dedicated_queue(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    sums: list[int] = []
    ipc_seen: list[str] = []

    @web.expose
    def add(a: int, b: int) -> int:
        total = int(a) + int(b)
        sums.append(total)
        return total

    web.set_ipc_handler(ipc_seen.append)
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {"__tkwry": "rpc", "id": "r1", "method": "add", "params": [2, 3]}
            ),
        ),
        ("about:blank", "flood"),
    ]
    web._webview = native
    web._deliver_ipc_messages()

    assert sums == [5]
    assert ipc_seen == ["flood"]
    native.eval_js.assert_called_once()

    web.destroy()
    frame.destroy()


def test_rpc_stream_chunks_then_settles(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ticks() -> object:
        yield 1
        yield 2

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s1",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()

    scripts = [call.args[0] for call in native.eval_js.call_args_list]
    chunks = [src for src in scripts if "_chunk" in src]
    settles = [src for src in scripts if "_settle" in src]
    assert len(chunks) == 2
    assert any("1" in src for src in chunks)
    assert any("2" in src for src in chunks)
    assert len(settles) == 1
    assert "true" in settles[0]

    web.destroy()
    frame.destroy()


def test_rpc_stream_cancel_envelope_stops_generator(tk_root) -> None:
    """JS cancel (same envelope as call) sets rpc_cancelled and rejects."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    first = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True)
    def ticks() -> object:
        started.set()
        yield 1
        first.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                return
            time.sleep(0.02)
        yield 2

    web._cancel_deferred_callbacks()
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s9",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not first.is_set():
        web._drain_rpc_futures()
        time.sleep(0.02)
    assert first.is_set()
    web._drain_rpc_futures()

    native.drain_window_ipc_messages.return_value = [
        ("about:blank", json.dumps({"__tkwry": "rpc", "id": "s9", "cancel": True}))
    ]
    web._deliver_ipc_messages()
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not saw_cancel.is_set():
        tk_root.update()
        web._drain_rpc_futures()
        time.sleep(0.02)
    assert saw_cancel.is_set()

    scripts = [call.args[0] for call in native.eval_js.call_args_list]
    chunks = [src for src in scripts if "_chunk" in src]
    settles = [src for src in scripts if "_settle" in src]
    assert len(chunks) == 1
    assert any("RpcCancelledError" in src for src in settles)

    web.destroy()
    frame.destroy()


def test_rpc_stream_destroy_cancels_open_stream(tk_root) -> None:
    """destroy() sets rpc_cancelled and does not eval_js-settle the stream."""
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web._cancel_deferred_callbacks()
    started = threading.Event()
    saw_cancel = threading.Event()
    finished = threading.Event()

    @web.expose(thread=True)
    def ticks() -> object:
        started.set()
        yield 1
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                break
            time.sleep(0.02)
        finished.set()

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s1",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        web._drain_rpc_futures()
        scripts = [call.args[0] for call in native.eval_js.call_args_list]
        if any("_chunk" in src for src in scripts):
            break
        time.sleep(0.02)
    calls_before_destroy = native.eval_js.call_count
    web.destroy()
    frame.destroy()
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not finished.is_set():
        time.sleep(0.02)
    assert saw_cancel.is_set()
    assert finished.is_set()
    web._drain_rpc_futures()
    assert native.eval_js.call_count == calls_before_destroy


def test_rpc_stream_oversized_chunk_rejects(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_STREAM_CHUNK_BYTES", 16)
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ticks() -> object:
        yield "x" * 80

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s1",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()

    scripts = [call.args[0] for call in native.eval_js.call_args_list]
    assert not any("_chunk" in src for src in scripts)
    settles = [src for src in scripts if "_settle" in src]
    assert len(settles) == 1
    assert "false" in settles[0]
    assert "RpcMessageTooLarge" in settles[0]

    web.destroy()
    frame.destroy()


def test_rpc_call_rejects_generator(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def ticks() -> object:
        yield 1

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ticks", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()

    native.eval_js.assert_called_once()
    script = native.eval_js.call_args[0][0]
    assert "_settle" in script
    assert "false" in script
    assert "TypeError" in script

    web.destroy()
    frame.destroy()


def test_rpc_worker_stream_hops_to_tk(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web._cancel_deferred_callbacks()
    started = threading.Event()

    @web.expose(thread=True)
    def ticks() -> object:
        started.set()
        yield 1
        yield 2

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s1",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        web._drain_rpc_futures()
        scripts = [call.args[0] for call in native.eval_js.call_args_list]
        chunks = [src for src in scripts if "_chunk" in src]
        settles = [src for src in scripts if "_settle" in src]
        if len(chunks) >= 2 and settles:
            break
        time.sleep(0.02)
    scripts = [call.args[0] for call in native.eval_js.call_args_list]
    assert len([src for src in scripts if "_chunk" in src]) == 2
    assert any("_settle" in src for src in scripts)

    web.destroy()
    frame.destroy()


def test_ipc_and_rpc_delivered_in_enqueue_order(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    order: list[str] = []

    @web.expose
    def ping() -> str:
        order.append("rpc")
        return "ok"

    web.set_ipc_handler(lambda msg: order.append(f"ipc:{msg}"))
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        ("about:blank", "first"),
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        ),
        ("about:blank", "second"),
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert order == ["ipc:first", "rpc", "ipc:second"]

    web.destroy()
    frame.destroy()


def test_rpc_stream_queue_caps_and_counts_drops(tk_root) -> None:
    from tkwry._rpc_api import MAX_RPC_STREAM_PENDING

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    for i in range(MAX_RPC_STREAM_PENDING):
        assert web._enqueue_rpc_stream_chunk("s1", i) is True
    assert web._rpc_stream_queue.qsize() == MAX_RPC_STREAM_PENDING
    assert web._enqueue_rpc_stream_chunk("s1", "overflow") is False
    assert web._rpc_stream_dropped == 1
    assert web._rpc_stream_queue.qsize() == MAX_RPC_STREAM_PENDING
    assert web._enqueue_rpc_stream_chunk("s1", "again") is False
    assert web._rpc_stream_dropped == 2

    web.destroy()
    frame.destroy()


def test_rpc_stream_drop_rejects_open_stream(tk_root) -> None:
    from tkwry._rpc_api import MAX_RPC_STREAM_PENDING

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    gate = threading.Event()

    @web.expose(thread=True)
    def ticks() -> object:
        started.set()
        yield 1
        gate.wait(timeout=2.0)
        yield 2

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps(
                {
                    "__tkwry": "rpc",
                    "id": "s1",
                    "method": "ticks",
                    "params": [],
                    "stream": True,
                }
            ),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    for i in range(MAX_RPC_STREAM_PENDING):
        web._enqueue_rpc_stream_chunk("s1", i)
    assert web._enqueue_rpc_stream_chunk("s1", "overflow") is False
    web._drain_rpc_futures()
    scripts = [call.args[0] for call in native.eval_js.call_args_list]
    settles = [src for src in scripts if "_settle" in src]
    assert len(settles) == 1
    assert "false" in settles[0]
    assert "RpcStreamOverflowError" in settles[0]
    gate.set()

    web.destroy()
    frame.destroy()


def test_rpc_timeout_sets_cancel_flag(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True, timeout=0.15)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                return "cancelled"
            time.sleep(0.02)
        return "done"

    web._cancel_deferred_callbacks()
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "slow", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not saw_cancel.is_set():
        tk_root.update()
        time.sleep(0.02)
    assert saw_cancel.is_set()

    web.destroy()
    frame.destroy()


def test_rpc_cancel_envelope_sets_flag_and_rejects(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    started = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                saw_cancel.set()
                return "cancelled"
            time.sleep(0.02)
        return "done"

    web._cancel_deferred_callbacks()
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r9", "method": "slow", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)

    native.drain_window_ipc_messages.return_value = [
        ("about:blank", json.dumps({"__tkwry": "rpc", "id": "r9", "cancel": True}))
    ]
    web._deliver_ipc_messages()
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not saw_cancel.is_set():
        tk_root.update()
        time.sleep(0.02)
    assert saw_cancel.is_set()

    web.destroy()
    frame.destroy()


def test_rpc_worker_done_after_destroy_skips_tk(tk_root) -> None:
    """Late worker completion must not hop to Tk (queue only; poll drains)."""
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web._cancel_deferred_callbacks()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    @web.expose(thread=True)
    def slow() -> str:
        started.set()
        while not release.wait(timeout=0.05):
            if rpc_cancelled():
                break
        finished.set()
        return "done"

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "slow", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert started.wait(timeout=2.0)
    web.destroy()
    frame.destroy()
    release.set()
    assert finished.wait(timeout=2.0)
    web._drain_rpc_futures()


def test_rpc_worker_settles_on_poll(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    web._cancel_deferred_callbacks()
    done = threading.Event()

    @web.expose(thread=True)
    def ping() -> str:
        done.set()
        return "pong"

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert done.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        web._drain_rpc_futures()
        if native.eval_js.called:
            break
        time.sleep(0.02)
    assert native.eval_js.called
    web.destroy()
    frame.destroy()


def test_emit_rejects_non_json(tk_root) -> None:
    from tkwry import RpcSerializationError

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    with pytest.raises(RpcSerializationError):
        web.emit("bad", object())

    web.destroy()
    frame.destroy()
