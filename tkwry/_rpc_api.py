"""IPC / RPC / emit / app-watch API mixed into :class:`tkwry.WebView`.

Keeps the communication surface out of the core lifecycle module while
preserving the public ``WebView`` methods unchanged.
"""

from __future__ import annotations

import os
import tkinter as tk
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tkwry.exceptions import (
    RpcTimeoutError,
    WebViewCreationError,
    WebViewDestroyedError,
)
from tkwry.ipc import (
    RPC_BOOTSTRAP_JS as _RPC_BOOTSTRAP_JS,
)
from tkwry.ipc import (
    RpcHandler,
    RpcRegistration,
    RpcRequest,
    RpcRunIn,
    dispatch_rpc,
    emit_script,
    format_rpc_error,
    merge_initialization_script,
    parse_rpc_request,
    rpc_error,
    settle_script,
)


class WebViewRpcMixin:
    """JS bridge: IPC handler, ``expose`` / ``emit``, and ``watch_app``."""

    def _init_rpc_state(
        self,
        *,
        ipc_handler: Callable[[str], None] | None,
        rpc_traceback: bool,
    ) -> None:
        self._ipc_handler = ipc_handler
        self._rpc_methods: dict[str, RpcRegistration] = {}
        self._rpc_bootstrap_injected = False
        self._rpc_bridge_wanted = False
        self._rpc_traceback = rpc_traceback or bool(
            os.environ.get("TKWRY_RPC_TRACEBACK")
        )
        self._rpc_executor: ThreadPoolExecutor | None = None
        self._rpc_inflight: dict[str, Future[Any]] = {}
        self._rpc_timeout_after: dict[str, str] = {}
        self._app_watch_after_id: str | None = None
        self._app_watch_mtime: float | None = None

    def set_ipc_handler(self, handler: Callable[[str], None] | None) -> None:
        """Register or clear the JS → Python IPC handler (Tk main thread).

        IPC is **event notification** (fire-and-forget string messages via
        ``window.ipc.postMessage``). For request/response, use
        :meth:`expose` / ``window.tkwry.call`` instead.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_ipc_handler()"
            ) from self._creation_error
        self._ipc_handler = handler
        if self._webview is not None:
            self._webview.set_ipc_listening(self._ipc_listening_wanted())
        if self._ipc_listening_wanted():
            self._ensure_event_poll()

    def expose(
        self,
        fn: RpcHandler | None = None,
        /,
        *,
        name: str | None = None,
        thread: bool = False,
        run_in: RpcRunIn | None = None,
        timeout: float | None = None,
        replace: bool = False,
    ) -> RpcHandler | Callable[[RpcHandler], RpcHandler]:
        """Expose a Python callable to ``window.tkwry.call(name, ...)``.

        Use as ``@web.expose`` / ``@web.expose(name="foo")`` or
        ``web.expose(fn)``. Arguments may be positional and/or keyword
        (``window.tkwry.call(name, …, { kwargs: { … } })``). Return values
        must be JSON-serializable.

        Execution model:

        - Default / ``run_in="main"`` — handler runs on the **Tk main thread**
          (keeps UI APIs safe; heavy work blocks the UI).
        - ``thread=True`` or ``run_in="worker"`` — handler runs on a background
          thread pool; settle returns to Tk via ``after_idle``.

        Optional *timeout* (seconds) rejects the Promise if the handler (or a
        returned ``Future``) does not finish in time. Re-registering the same
        name raises ``ValueError`` unless ``replace=True``.

        The low-level ``ipc_handler`` remains available for raw
        ``window.ipc.postMessage`` traffic (IPC = events; RPC = request/response).
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call expose()"
            ) from self._creation_error
        if run_in is None:
            run_in = "worker" if thread else "main"
        elif thread and run_in == "main":
            raise ValueError("expose: thread=True conflicts with run_in='main'")
        if timeout is not None and timeout <= 0:
            raise ValueError("expose: timeout must be positive when set")

        def register(handler: RpcHandler) -> RpcHandler:
            method = name if name is not None else handler.__name__
            if not method:
                raise ValueError("exposed RPC method name must be non-empty")
            if method in self._rpc_methods and not replace:
                raise ValueError(
                    f"RPC method {method!r} is already exposed; "
                    "pass replace=True to overwrite"
                )
            self._rpc_methods[method] = RpcRegistration(
                handler=handler,
                run_in=run_in,
                timeout=timeout,
            )
            self._enable_rpc()
            return handler

        if fn is not None:
            return register(fn)
        return register

    def unexpose(self, name: str) -> bool:
        """Remove an exposed RPC method. Returns whether it was registered."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        existed = self._rpc_methods.pop(name, None) is not None
        if self._webview is not None:
            self._webview.set_ipc_listening(self._ipc_listening_wanted())
        return existed

    def emit(self, event: str, data: Any = None) -> None:
        """Emit a fire-and-forget event to JavaScript (``window.tkwry.on``).

        Complements IPC/RPC (JS → Python). Listeners register with
        ``window.tkwry.on(event, handler)``. Payload must be JSON-serializable.
        """
        self._require_tk_thread()
        if not event:
            raise ValueError("emit: event name must be non-empty")
        self._require_ready("emit")
        self._rpc_bridge_wanted = True
        self._enable_rpc()
        self.eval_js(emit_script(event, data))

    def watch_app(self, *, interval_ms: int = 700) -> None:
        """Poll ``app=`` files and :meth:`reload` when mtimes change (dev helper).

        Requires ``app=`` at construction. Prefer with ``app_dev=True`` so
        browsers do not cache stale assets. Stopped automatically on
        :meth:`destroy`.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._app_root is None:
            raise ValueError("watch_app() requires app= at construction")
        if interval_ms < 100:
            raise ValueError("watch_app: interval_ms must be >= 100")
        self._stop_app_watch()
        self._app_watch_mtime = self._scan_app_mtime()
        self._schedule_app_watch(interval_ms)

    def _ipc_listening_wanted(self) -> bool:
        return self._ipc_handler is not None or bool(self._rpc_methods)

    def _enable_rpc(self) -> None:
        """Turn on IPC listening and ensure the JS bridge bootstrap is present."""
        self._rpc_bridge_wanted = True
        if self._webview is not None:
            if self._rpc_methods or self._ipc_handler is not None:
                self._webview.set_ipc_listening(True)
            if not self._rpc_bootstrap_injected:
                try:
                    self._webview.eval_js(_RPC_BOOTSTRAP_JS)
                    self._rpc_bootstrap_injected = True
                except Exception:
                    traceback.print_exc()
        self._ensure_event_poll()

    def _effective_initialization_script(self) -> str | None:
        return merge_initialization_script(
            self._initialization_script,
            rpc_enabled=bool(self._rpc_methods) or self._rpc_bridge_wanted,
        )

    def _get_rpc_executor(self) -> ThreadPoolExecutor:
        if self._rpc_executor is None:
            self._rpc_executor = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="tkwry-rpc",
            )
        return self._rpc_executor

    def _shutdown_rpc_executor(self) -> None:
        executor = self._rpc_executor
        self._rpc_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _abort_inflight_rpc(self) -> None:
        """Reject pending RPC Promises before the native view is released."""
        error = rpc_error("WebViewDestroyedError", "webview destroyed")
        pending = list(self._rpc_inflight.items())
        self._rpc_inflight.clear()
        for after_id in list(self._rpc_timeout_after.values()):
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        self._rpc_timeout_after.clear()
        for req_id, fut in pending:
            fut.cancel()
            self._settle_rpc(req_id, ok=False, value=error)

    def _scan_app_mtime(self) -> float:
        root = Path(self._app_root or "")
        latest = 0.0
        if not root.is_dir():
            return latest
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
        return latest

    def _schedule_app_watch(self, interval_ms: int) -> None:
        def _tick() -> None:
            self._app_watch_after_id = None
            if self._destroyed or self._app_root is None:
                return
            current = self._scan_app_mtime()
            previous = self._app_watch_mtime
            self._app_watch_mtime = current
            if previous is not None and current > previous and self.ready:
                try:
                    self.reload()
                except Exception:
                    traceback.print_exc()
            self._schedule_app_watch(interval_ms)

        try:
            self._app_watch_after_id = self._frame.after(interval_ms, _tick)
            self._track_after(self._app_watch_after_id)
        except tk.TclError:
            self._app_watch_after_id = None

    def _stop_app_watch(self) -> None:
        after_id = self._app_watch_after_id
        self._app_watch_after_id = None
        if after_id is not None:
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass

    def _deliver_ipc_messages(self) -> None:
        native = self._webview
        if native is None or not self._ipc_listening_wanted():
            return
        rpc_messages = native.drain_rpc_messages()
        ipc_messages = native.drain_ipc_messages()
        for message in (*rpc_messages, *ipc_messages):
            request = parse_rpc_request(message)
            if request is not None:
                self._handle_rpc_request(request)
                continue
            handler = self._ipc_handler
            if handler is not None:
                self._invoke_callback(handler, message)

    def _handle_rpc_request(self, request: RpcRequest) -> None:
        reg = self._rpc_methods.get(request.method)

        def submit_worker(fn: Callable[[], Any]) -> Future[Any]:
            return self._get_rpc_executor().submit(fn)

        outcome = dispatch_rpc(
            self._rpc_methods,
            request,
            submit_worker=submit_worker,
            include_traceback=self._rpc_traceback,
        )
        timeout = reg.timeout if reg is not None else None
        if isinstance(outcome, Future):
            self._track_rpc_future(request.id, outcome, timeout=timeout)
            return
        ok, value = outcome
        self._settle_rpc(request.id, ok=ok, value=value)

    def _track_rpc_future(
        self,
        req_id: str,
        fut: Future[Any],
        *,
        timeout: float | None,
    ) -> None:
        self._rpc_inflight[req_id] = fut

        def _done(done_fut: Future[Any]) -> None:
            def _settle() -> None:
                after_id = self._rpc_timeout_after.pop(req_id, None)
                if after_id is not None:
                    try:
                        self._frame.after_cancel(after_id)
                    except (tk.TclError, ValueError):
                        pass
                if self._rpc_inflight.pop(req_id, None) is None:
                    return
                if self._destroyed:
                    return
                try:
                    value = done_fut.result()
                except Exception as exc:
                    self._settle_rpc(
                        req_id,
                        ok=False,
                        value=format_rpc_error(
                            exc, include_traceback=self._rpc_traceback
                        ),
                    )
                    return
                try:
                    import json as _json

                    _json.dumps(value)
                except (TypeError, ValueError):
                    self._settle_rpc(
                        req_id,
                        ok=False,
                        value=rpc_error(
                            "RpcSerializationError",
                            "rpc result is not JSON-serializable",
                        ),
                    )
                    return
                self._settle_rpc(req_id, ok=True, value=value)

            try:
                self._frame.after_idle(_settle)
            except tk.TclError:
                pass

        fut.add_done_callback(_done)

        if timeout is None:
            return

        def _on_timeout() -> None:
            self._rpc_timeout_after.pop(req_id, None)
            pending = self._rpc_inflight.pop(req_id, None)
            if pending is None:
                return
            pending.cancel()
            if self._destroyed:
                return
            self._settle_rpc(
                req_id,
                ok=False,
                value=format_rpc_error(
                    RpcTimeoutError(f"rpc timeout after {timeout}s"),
                    include_traceback=False,
                ),
            )

        try:
            after_id = self._frame.after(int(timeout * 1000), _on_timeout)
            self._rpc_timeout_after[req_id] = after_id
            self._track_after(after_id)
        except tk.TclError:
            pass

    def _settle_rpc(self, req_id: str, *, ok: bool, value: object) -> None:
        script = settle_script(req_id, ok=ok, value=value)
        native = self._webview
        if native is None:
            return
        try:
            native.eval_js(script)
        except Exception:
            traceback.print_exc()
