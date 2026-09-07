"""Extra coverage for RPC mixin edge paths."""

from __future__ import annotations

import queue
import tkinter as tk
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tkwry import WebView
from tkwry.exceptions import WebViewCreationError
from tkwry.ipc import RpcRequest


def test_rpc_rejects_when_creation_failed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._creation_error = RuntimeError("create failed")
    with pytest.raises(WebViewCreationError, match="native creation failed"):

        @web.rpc
        def ping() -> str:
            return "pong"

    web.destroy()
    frame.destroy()


def test_rpc_named_decorator_and_callable(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.rpc("named")
    def ping() -> str:
        return "pong"

    def also() -> str:
        return "also"

    web.rpc(also, name="also")
    assert "named" in web._rpc_methods
    assert "also" in web._rpc_methods

    @web.rpc
    def bare() -> str:
        return "bare"

    assert "bare" in web._rpc_methods
    web.destroy()
    frame.destroy()


def test_rpc_decorator_with_name_kwarg(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.rpc(name="from_kw")
    def ignored_name() -> str:
        return "ok"

    assert "from_kw" in web._rpc_methods
    assert web.unexpose("from_kw") is True
    assert web.unexpose("missing") is False
    web.destroy()
    frame.destroy()


def test_emit_validation_and_origin(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    with pytest.raises(ValueError, match="non-empty"):
        web.emit("")
    web._bridge_origins = ("https://example.com",)
    web._webview = MagicMock()
    web._webview.url.return_value = "https://other.example/"
    with pytest.raises(ValueError, match="not allowed"):
        web.emit("ping")
    web._webview.url.side_effect = RuntimeError("url fail")
    with pytest.raises(ValueError, match="not allowed"):
        web.emit("ping")
    web.destroy()
    frame.destroy()


def test_emit_app_allows_missing_engine_url(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WebView2 often reports no URL (blank→None) while app= chrome is ready."""
    app_dir = tmp_path / "chrome"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<p>chrome</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=app_dir)
    native = MagicMock()
    native.url.return_value = None
    web._webview = native
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    monkeypatch.setattr(web, "_run_eval_js", MagicMock())
    assert web._emit_eligible() is True
    web.emit("state", {"ok": True})
    web._run_eval_js.assert_called_once()
    web.destroy()
    frame.destroy()


def test_emit_eligible_branches(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web._emit_eligible() in (True, False)
    web._destroyed = True
    assert web._emit_eligible() is False
    web._destroyed = False
    web._untrusted = True
    assert web._emit_eligible() is False
    web.destroy()
    frame.destroy()


def test_watch_app_validation_errors(tk_root, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=app_dir, app_dev=True)

    with pytest.raises(ValueError, match="interval_ms"):
        web.watch_app(interval_ms=50)
    with pytest.raises(ValueError, match="max_files"):
        web.watch_app(max_files=0)
    with pytest.raises(ValueError, match='suffixes must be "*"'):
        web.watch_app(suffixes="js")  # type: ignore[arg-type]

    web.watch_app(suffixes="*", max_files=10)
    web._app_watch_tick(700)
    web._stop_app_watch()
    web.destroy()
    frame.destroy()


def test_watch_app_requires_app(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    with pytest.raises(ValueError, match="requires app="):
        web.watch_app()
    web.destroy()
    frame.destroy()


def test_set_on_ipc_rejects_untrusted(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", untrusted=True)
    with pytest.raises(ValueError, match="untrusted"):
        web.set_on_ipc(lambda _m: None)
    web.destroy()
    frame.destroy()


def test_rpc_bridge_inject_and_nav_sync(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    native = MagicMock()
    web._webview = native
    web._rpc_bridge_wanted = True
    web._inject_rpc_bootstrap()
    native.eval_js.assert_called()
    native.eval_js.side_effect = RuntimeError("eval fail")
    web._rpc_bootstrap_injected = False
    web._inject_rpc_bootstrap()  # exception path

    web._rpc_nav_sync_after_id = None
    web._schedule_rpc_bridge_navigation_sync()
    assert web._rpc_nav_sync_after_id is not None
    # Idle callback may already have run; force second schedule skip.
    web._rpc_nav_sync_after_id = "busy"
    web._schedule_rpc_bridge_navigation_sync()
    web._rpc_nav_sync_after_id = None
    web._sync_rpc_bridge_after_finished()
    web.destroy()
    frame.destroy()


def test_rpc_stream_queue_drop_and_flush(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._rpc_stream_open.add("1:r1")
    # Force overflow by filling to max then one more.
    from tkwry import _rpc_api as rpc_mod

    old_max = rpc_mod.MAX_RPC_STREAM_PENDING
    try:
        rpc_mod.MAX_RPC_STREAM_PENDING = 1
        assert web._enqueue_rpc_stream_chunk("1:r1", {"a": 1}) is True
        assert web._enqueue_rpc_stream_chunk("1:r1", {"a": 2}) is False
        web._flush_rpc_stream_drop_rejects()
    finally:
        rpc_mod.MAX_RPC_STREAM_PENDING = old_max
    assert web._take_rpc_stream_dropped() >= 1
    web.destroy()
    frame.destroy()


def test_cancel_inflight_and_abort(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    fut: Future[object] = Future()
    web._rpc_inflight["1:r1"] = fut
    web._rpc_timeout_after["1:r1"] = web._frame.after(60_000, lambda: None)
    web._rpc_cancel_events["1:r1"] = __import__("threading").Event()
    web._cancel_inflight_rpc_for_navigation()
    assert not web._rpc_inflight

    fut2: Future[object] = Future()
    web._rpc_inflight["2:r2"] = fut2
    web._abort_inflight_rpc()
    assert not web._rpc_inflight
    web.destroy()
    frame.destroy()


def test_handle_rpc_cancel_and_user_cancelled(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._webview = MagicMock()
    settled: list[tuple] = []
    web._settle_rpc = lambda req_id, ok, value: settled.append((req_id, ok, value))  # type: ignore[method-assign]

    # Foreign cancel ignored.
    web._handle_rpc_cancel("1:r1", "https://a/")
    assert not settled

    web._rpc_owners["1:r1"] = "https://a/"
    web._rpc_cancel_events["1:r1"] = __import__("threading").Event()
    web._handle_rpc_cancel("1:r1", "https://a/")
    assert settled and settled[0][0] == "1:r1"

    web._rpc_user_cancelled.add("1:r2")
    web._handle_rpc_request(
        RpcRequest(
            id="1:r2",
            method="x",
            params=(),
            kwargs={},
            cancel=False,
            reject=None,
        ),
        "https://a/",
    )
    assert any(item[0] == "1:r2" for item in settled)
    web.destroy()
    frame.destroy()


def test_push_rpc_chunk_serialization_errors(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web._webview = MagicMock()
    web._rpc_stream_open.add("0:r1")
    web._rpc_epoch = 0
    settled: list[object] = []
    web._settle_rpc = lambda *a, **k: settled.append(a)  # type: ignore[method-assign]

    class _Bad:
        pass

    web._push_rpc_chunk("0:r1", _Bad())
    assert settled
    web.destroy()
    frame.destroy()


def test_drain_rpc_futures_when_destroyed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    fut: Future[object] = Future()
    fut.set_result({"ok": True})
    web._rpc_done_queue.put_nowait(("0:r1", fut))
    web._rpc_stream_queue.put_nowait(("0:r1", {"x": 1}))
    web._destroyed = True
    web._drain_rpc_futures()
    with pytest.raises(queue.Empty):
        web._rpc_done_queue.get_nowait()
    web.destroy()
    frame.destroy()
