"""IPC / RPC / emit / app-watch API mixed into :class:`tkwry.WebView`.

Keeps the communication surface out of the core lifecycle module while
preserving the public ``WebView`` methods unchanged.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from tkwry._app import (
    WATCH_DEFAULT_IGNORE_DIRS,
    WATCH_DEFAULT_MAX_FILES,
    WATCH_DEFAULT_SUFFIXES,
    normalize_watch_suffixes,
    scan_app_mtime,
)
from tkwry.context_menu import (
    CONTEXT_MENU_DISABLE_JS,
    CONTEXT_MENU_JS,
    parse_context_menu_event,
)
from tkwry.exceptions import (
    RpcSerializationError,
    RpcTimeoutError,
    WebViewCreationError,
)
from tkwry.ipc import (
    MAX_IPC_MESSAGE_BYTES,
    RPC_STREAM_DONE,
    RpcHandler,
    RpcMessageTooLarge,
    RpcRegistration,
    RpcRequest,
    RpcRunIn,
    bind_rpc_cancel_event,
    dispatch_rpc,
    emit_script,
    finalize_rpc_result,
    format_rpc_error,
    merge_initialization_script,
    parse_rpc_request,
    rpc_bump_epoch_script,
    rpc_error,
    rpc_id_epoch,
    settle_script,
    stream_chunk_script,
    validate_rpc_timeout,
)
from tkwry.ipc import (
    RPC_BOOTSTRAP_JS as _RPC_BOOTSTRAP_JS,
)

_RPC_EXECUTOR_JOIN_SECONDS = 2.0
# Worker→Tk stream chunks; same depth as native async queues (IPC / RPC / …).
MAX_RPC_STREAM_PENDING = 2048


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
        self._rpc_done_queue: queue.SimpleQueue[tuple[str, Future[Any]]] = (
            queue.SimpleQueue()
        )
        self._rpc_stream_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._rpc_stream_dropped = 0
        self._rpc_stream_open: set[str] = set()
        self._rpc_timeout_after: dict[str, str] = {}
        self._rpc_cancel_events: dict[str, threading.Event] = {}
        self._rpc_user_cancelled: set[str] = set()
        self._rpc_epoch = 0
        self._app_watch_after_id: str | None = None
        self._app_watch_mtime: float | None = None
        self._app_watch_suffixes: frozenset[str] | None = WATCH_DEFAULT_SUFFIXES
        self._app_watch_ignore_dirs: frozenset[str] = WATCH_DEFAULT_IGNORE_DIRS
        self._app_watch_max_files = WATCH_DEFAULT_MAX_FILES
        self._app_watch_limit_warned = False

    def set_ipc_handler(self, handler: Callable[[str], None] | None) -> None:
        """Register or clear the JS → Python IPC handler (Tk main thread).

        IPC is **event notification** (fire-and-forget string messages via
        ``window.ipc.postMessage``). For request/response, use
        :meth:`expose` / ``window.tkwry.call`` instead.
        """
        self._require_not_destroyed("set_ipc_handler")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_ipc_handler()"
            ) from self._creation_error
        if handler is not None and self._untrusted:
            raise ValueError("WebView: untrusted=True cannot use ipc_handler")
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
        allow_any_origin: bool = False,
    ) -> RpcHandler | Callable[[RpcHandler], RpcHandler]:
        """Expose a Python callable to ``window.tkwry.call(name, ...)``.

        Use as ``@web.expose`` / ``@web.expose(name="foo")`` or
        ``web.expose(fn)``. Arguments may be positional and/or keyword
        (``window.tkwry.call(name, …, { kwargs: { … } })``). Return values
        must be JSON-serializable. A **sync generator** is streamed via
        ``window.tkwry.stream`` (``call`` rejects it); each yield is one
        JSON chunk, then the iterator completes. Async generators are not
        supported. Prefer ``thread=True`` so cancel can interrupt between
        yields (main-thread generators block Tk until they finish).

        Execution model:

        - Default / ``run_in="main"`` — handler runs on the **Tk main thread**
          (keeps UI APIs safe; heavy work blocks the UI).
        - ``thread=True`` or ``run_in="worker"`` — handler runs on a background
          thread pool; settle returns on the Tk event poll (never ``after``
          from the worker thread).

        Optional *timeout* (seconds) applies to ``run_in="worker"`` handlers
        and handlers that return a ``Future``. It rejects the Promise if work
        does not finish in time. Cancellation is cooperative only: Python
        cannot preempt a running worker; handlers should poll
        :func:`tkwry.rpc_cancelled` (or :func:`tkwry.rpc_cancel_event`).
        JS may also call ``window.tkwry.cancel(id)`` (``call`` returns a
        Promise with ``.id`` / ``.cancel()``). Timeout on a synchronous
        ``run_in="main"`` handler is ignored. Argument mismatches reject with
        ``TypeError``. Re-registering the same name raises ``ValueError`` unless
        ``replace=True``.

        When :attr:`~tkwry.WebView.bridge_origins` is ``"*"``, pass
        ``allow_any_origin=True`` to acknowledge that every page in the view
        can call this method. ``set_bridge_origins("*")`` is refused if any
        exposed method lacks that flag.

        The low-level ``ipc_handler`` remains available for raw
        ``window.ipc.postMessage`` traffic (IPC = events; RPC = request/response).
        Calls are accepted only from :attr:`~tkwry.WebView.bridge_origins`
        (and :attr:`~tkwry.WebView.bridge_allow`, if set).
        """
        self._require_not_destroyed("expose")
        if self._untrusted:
            raise ValueError("WebView: untrusted=True cannot expose() RPC methods")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call expose()"
            ) from self._creation_error
        if run_in is None:
            run_in = "worker" if thread else "main"
        elif thread and run_in == "main":
            raise ValueError("expose: thread=True conflicts with run_in='main'")
        validate_rpc_timeout(timeout)
        if self._bridge_origins == "*" and not allow_any_origin:
            raise ValueError(
                "expose: bridge_origins='*' requires allow_any_origin=True "
                "(every page can call this method)"
            )

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
                allow_any_origin=allow_any_origin,
            )
            self._enable_rpc()
            return handler

        if fn is not None:
            return register(fn)
        return register

    def rpc(
        self,
        name_or_fn: RpcHandler | str | None = None,
        /,
        *,
        name: str | None = None,
        thread: bool = False,
        run_in: RpcRunIn | None = None,
        timeout: float | None = None,
        replace: bool = False,
        allow_any_origin: bool = False,
    ) -> RpcHandler | Callable[[RpcHandler], RpcHandler]:
        """Register an RPC handler under an explicit method name.

        Sugar for :meth:`expose` — use ``@web.rpc("get_data")`` or ``@web.rpc``
        (method name defaults to the function name). From JavaScript, prefer
        ``await window.tkwry.invoke("get_data", {id: 123})`` to pass a single
        object as Python keyword arguments.
        """
        self._require_not_destroyed("rpc")
        if self._untrusted:
            raise ValueError("WebView: untrusted=True cannot expose() RPC methods")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call rpc()"
            ) from self._creation_error
        expose_kwargs = {
            "thread": thread,
            "run_in": run_in,
            "timeout": timeout,
            "replace": replace,
            "allow_any_origin": allow_any_origin,
        }

        if callable(name_or_fn):
            method = name if name is not None else name_or_fn.__name__
            return self.expose(name_or_fn, name=method, **expose_kwargs)
        if isinstance(name_or_fn, str):

            def register(handler: RpcHandler) -> RpcHandler:
                return self.expose(handler, name=name_or_fn, **expose_kwargs)

            return register

        def register(handler: RpcHandler) -> RpcHandler:
            method = name if name is not None else handler.__name__
            return self.expose(handler, name=method, **expose_kwargs)

        return register

    def unexpose(self, name: str) -> bool:
        """Remove an exposed RPC method. Returns whether it was registered."""
        self._require_not_destroyed("unexpose")
        existed = self._rpc_methods.pop(name, None) is not None
        if self._webview is not None:
            self._webview.set_ipc_listening(self._ipc_listening_wanted())
        return existed

    def emit(self, event: str, data: Any = None) -> None:
        """Emit a fire-and-forget event to JavaScript (``window.tkwry.on``).

        Complements IPC/RPC (JS → Python). Listeners register with
        ``window.tkwry.on(event, handler)``. Payload must be JSON-serializable
        (``datetime``, NaN/Inf, and custom objects raise
        :class:`~tkwry.RpcSerializationError`).
        """
        self._require_not_destroyed("emit")
        if not event:
            raise ValueError("emit: event name must be non-empty")
        if self._untrusted:
            raise ValueError("WebView: untrusted=True cannot emit()")
        current = None
        if self._webview is not None:
            try:
                current = self._webview.url()
            except Exception:
                current = None
        if not self._bridge_origin_allowed(current or "about:blank"):
            raise ValueError(f"emit: current page origin is not allowed ({current!r})")
        script = emit_script(event, data)
        self._require_ready("emit")
        self._rpc_bridge_wanted = True
        self._enable_rpc()
        self.eval_js(script)

    def _emit_eligible(self) -> bool:
        """Whether :meth:`emit` / session broadcast may deliver to this view."""
        if self._destroyed or self._creation_error is not None:
            return False
        if self._untrusted:
            return False
        if not self.ready:
            return False
        current = None
        if self._webview is not None:
            try:
                current = self._webview.url()
            except Exception:
                current = None
        return self._bridge_origin_allowed(current or "about:blank")

    def watch_app(
        self,
        *,
        interval_ms: int = 700,
        suffixes: Collection[str] | str | None = None,
        ignore_dirs: Collection[str] | None = None,
        max_files: int = WATCH_DEFAULT_MAX_FILES,
    ) -> None:
        """Poll ``app=`` files and :meth:`reload` when mtimes change (dev helper).

        Requires ``app=`` at construction. Prefer with ``app_dev=True`` so
        browsers do not cache stale assets. Stopped automatically on
        :meth:`destroy`.

        This is a bounded Tk poll (not OS file notifications). By default only
        common web suffixes are watched and directories such as ``node_modules``,
        ``.git``, and ``.vendor`` are skipped, up to *max_files* (2000). Pass
        ``suffixes="*"`` to watch every file, *ignore_dirs* to override skipped
        directory names, or raise *max_files* for large trees.
        """
        self._require_not_destroyed("watch_app")
        if self._app_root is None:
            raise ValueError("watch_app() requires app= at construction")
        if interval_ms < 100:
            raise ValueError("watch_app: interval_ms must be >= 100")
        if max_files <= 0:
            raise ValueError("watch_app: max_files must be positive")
        if isinstance(suffixes, str):
            if suffixes != "*":
                raise ValueError(
                    'watch_app: suffixes must be "*" or a collection of '
                    'extensions, not a single string (use suffixes=[".js"])'
                )
            self._app_watch_suffixes = None
        elif suffixes is None:
            self._app_watch_suffixes = WATCH_DEFAULT_SUFFIXES
        else:
            self._app_watch_suffixes = normalize_watch_suffixes(suffixes)
        if isinstance(ignore_dirs, str):
            raise ValueError(
                "watch_app: ignore_dirs must be a collection of directory "
                'names, not a string (use ignore_dirs=["node_modules"])'
            )
        self._app_watch_ignore_dirs = (
            WATCH_DEFAULT_IGNORE_DIRS
            if ignore_dirs is None
            else frozenset(str(name) for name in ignore_dirs)
        )
        self._app_watch_max_files = max_files
        self._app_watch_limit_warned = False
        self._stop_app_watch()
        self._app_watch_mtime = self._scan_app_mtime()
        self._schedule_app_watch(interval_ms)

    def _ipc_listening_wanted(self) -> bool:
        return (
            self._ipc_handler is not None
            or bool(self._rpc_methods)
            or self._context_menu_active()
        )

    def _context_menu_active(self) -> bool:
        return (
            getattr(self, "_context_menu_handler", None) is not None
            or getattr(self, "_context_menu_items", None) is not None
        )

    def _inject_rpc_bootstrap(self) -> None:
        """Inject the RPC JS bridge into the current document."""
        native = self._webview
        if native is None or not (self._rpc_bridge_wanted or self._rpc_methods):
            return
        native.set_ipc_listening(True)
        try:
            native.eval_js(_RPC_BOOTSTRAP_JS)
            self._rpc_bootstrap_injected = True
        except Exception:
            traceback.print_exc()

    def _enable_rpc(self) -> None:
        """Turn on IPC listening and ensure the JS bridge bootstrap is present."""
        self._rpc_bridge_wanted = True
        if self._webview is not None:
            if self._ipc_listening_wanted():
                self._webview.set_ipc_listening(True)
            if not self._rpc_bootstrap_injected:
                self._inject_rpc_bootstrap()
        self._sync_page_load_listening()
        self._ensure_event_poll()

    def _inject_context_menu_bridge(self) -> None:
        """Inject the context-menu JS hook into the current document."""
        native = self._webview
        if native is None or not self._context_menu_active():
            return
        native.set_ipc_listening(True)
        try:
            native.eval_js(CONTEXT_MENU_JS)
            self._context_menu_bridge_injected = True
        except Exception:
            traceback.print_exc()

    def _remove_context_menu_bridge(self) -> None:
        """Drop the context-menu JS hook from the current document."""
        if not getattr(self, "_context_menu_bridge_injected", False):
            return
        native = self._webview
        if native is not None:
            try:
                native.eval_js(CONTEXT_MENU_DISABLE_JS)
            except Exception:
                traceback.print_exc()
        self._context_menu_bridge_injected = False

    def _enable_context_menu_bridge(self) -> None:
        """Ensure IPC listening and the context-menu JS hook are active."""
        if not getattr(self, "_context_menu_bridge_injected", False):
            self._inject_context_menu_bridge()
        self._ensure_event_poll()

    def _effective_initialization_script(self) -> str | None:
        from tkwry.context_menu import merge_context_menu_script

        script = merge_initialization_script(
            self._user_initialization_script(),
            rpc_enabled=bool(self._rpc_methods) or self._rpc_bridge_wanted,
        )
        return merge_context_menu_script(
            script,
            context_menu_enabled=self._context_menu_active(),
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
        if executor is None:
            return
        executor.shutdown(wait=False, cancel_futures=True)
        # Join so pool threads do not outlive destroy and race Tcl (macOS/Win
        # abort on the next ``update`` / ``Tk()``). Cooperative handlers exit
        # quickly after cancel; uncooperative ones are capped (~2s). Python
        # cannot forcibly stop a running thread.
        threads = [t for t in getattr(executor, "_threads", ()) if t.is_alive()]
        deadline = time.monotonic() + _RPC_EXECUTOR_JOIN_SECONDS
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        leftover = [t for t in threads if t.is_alive()]
        if leftover:
            print(
                f"tkwry: destroy waited {_RPC_EXECUTOR_JOIN_SECONDS:.0f}s; "
                f"{len(leftover)} RPC worker thread(s) still running "
                "(poll rpc_cancelled(); Python cannot preempt them)",
                file=sys.stderr,
            )

    def _discard_rpc_done_queue(self) -> None:
        q = self._rpc_done_queue
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        self._discard_rpc_stream_queue()

    def _discard_rpc_stream_queue(self) -> None:
        q = self._rpc_stream_queue
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _enqueue_rpc_stream_chunk(self, req_id: str, item: Any) -> bool:
        """Queue a worker stream chunk for the Tk thread. Return False if dropped."""
        q = self._rpc_stream_queue
        if q.qsize() >= MAX_RPC_STREAM_PENDING:
            self._rpc_stream_dropped += 1
            return False
        try:
            q.put_nowait((req_id, item))
        except Exception:
            self._rpc_stream_dropped += 1
            return False
        return True

    def _take_rpc_stream_dropped(self) -> int:
        n = self._rpc_stream_dropped
        self._rpc_stream_dropped = 0
        return n

    def _rpc_settle_allowed(self, req_id: str) -> bool:
        epoch = rpc_id_epoch(req_id)
        if epoch is None:
            return True
        return epoch == self._rpc_epoch

    def _cancel_inflight_rpc_for_navigation(self) -> None:
        """Drop in-flight RPC when the document navigates (no JS settle)."""
        pending = list(self._rpc_inflight.items())
        self._rpc_inflight.clear()
        self._discard_rpc_done_queue()
        for after_id in list(self._rpc_timeout_after.values()):
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass
        self._rpc_timeout_after.clear()
        self._rpc_stream_open.clear()
        for event in self._rpc_cancel_events.values():
            event.set()
        self._rpc_cancel_events.clear()
        self._rpc_user_cancelled.clear()
        for _req_id, fut in pending:
            fut.cancel()

    def _bump_rpc_epoch_for_navigation(self, *, sync_js: bool = True) -> None:
        """Advance RPC ids so stale responses cannot settle a new document."""
        self._rpc_epoch += 1
        self._cancel_inflight_rpc_for_navigation()
        if sync_js:
            self._sync_rpc_epoch_to_js()

    def _sync_rpc_epoch_to_js(self) -> None:
        if self._destroyed or self._webview is None:
            return
        if not (self._rpc_bridge_wanted or self._rpc_methods):
            return
        try:
            self._webview.eval_js(rpc_bump_epoch_script(self._rpc_epoch))
        except Exception:
            traceback.print_exc()

    def _abort_inflight_rpc(self) -> None:
        """Drop inflight RPC on destroy without touching the dying native view."""
        pending = list(self._rpc_inflight.items())
        self._rpc_inflight.clear()
        self._discard_rpc_done_queue()
        for after_id in list(self._rpc_timeout_after.values()):
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass
        self._rpc_timeout_after.clear()
        self._rpc_stream_open.clear()
        for event in self._rpc_cancel_events.values():
            event.set()
        self._rpc_cancel_events.clear()
        self._rpc_user_cancelled.clear()
        for _req_id, fut in pending:
            fut.cancel()
        # Do not eval_js settle: native is going away and worker threads must
        # not race WebKit/WebView2 teardown (Tcl/native abort on destroy+pump).

    def _signal_rpc_cancel(self, req_id: str) -> None:
        event = self._rpc_cancel_events.pop(req_id, None)
        if event is not None:
            event.set()

    def _drop_rpc_cancel(self, req_id: str) -> None:
        self._rpc_cancel_events.pop(req_id, None)

    def _scan_app_mtime(self) -> float:
        root = self._app_root or ""
        latest, _seen, truncated = scan_app_mtime(
            root,
            suffixes=self._app_watch_suffixes,
            ignore_dirs=self._app_watch_ignore_dirs,
            max_files=self._app_watch_max_files,
        )
        if truncated and not self._app_watch_limit_warned:
            self._app_watch_limit_warned = True
            print(
                f"tkwry: watch_app scanned {self._app_watch_max_files} files "
                f"under {root}; further files ignored "
                "(raise max_files= or narrow suffixes=/ignore_dirs=)",
                file=sys.stderr,
            )
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
        for source_url, message in (*rpc_messages, *ipc_messages):
            bridge_url = self._ipc_source_url_for_bridge(source_url)
            request = parse_rpc_request(message)
            if request is not None:
                if not self._bridge_origin_allowed(bridge_url):
                    if request.id and not request.cancel:
                        self._settle_rpc(
                            request.id,
                            ok=False,
                            value=rpc_error(
                                "RpcOriginError",
                                f"RPC from disallowed origin {source_url!r}; "
                                "add it to bridge_origins (or a path prefix)",
                            ),
                        )
                    continue
                self._handle_rpc_request(request)
                continue
            context_event = parse_context_menu_event(message)
            if context_event is not None:
                if not self._bridge_origin_allowed(bridge_url):
                    continue
                self._deliver_context_menu_event(context_event)
                continue
            if not self._bridge_origin_allowed(bridge_url):
                continue
            try:
                oversized = len(message.encode("utf-8")) > MAX_IPC_MESSAGE_BYTES
            except UnicodeEncodeError:
                oversized = True
            if oversized:
                continue
            handler = self._ipc_handler
            if handler is not None:
                self._invoke_callback(handler, message, kind="ipc_handler")

    def _handle_rpc_cancel(self, req_id: str) -> None:
        self._rpc_user_cancelled.add(req_id)
        self._signal_rpc_cancel(req_id)
        after_id = self._rpc_timeout_after.pop(req_id, None)
        if after_id is not None:
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        pending = self._rpc_inflight.pop(req_id, None)
        if pending is not None:
            pending.cancel()
        self._rpc_stream_open.discard(req_id)
        if self._destroyed:
            return
        self._settle_rpc(
            req_id,
            ok=False,
            value=rpc_error("RpcCancelledError", "rpc cancelled"),
        )

    def _handle_rpc_request(self, request: RpcRequest) -> None:
        if request.cancel:
            self._handle_rpc_cancel(request.id)
            return
        if request.reject is not None:
            self._settle_rpc(request.id, ok=False, value=request.reject)
            return
        if request.id in self._rpc_user_cancelled:
            self._rpc_user_cancelled.discard(request.id)
            self._settle_rpc(
                request.id,
                ok=False,
                value=rpc_error("RpcCancelledError", "rpc cancelled"),
            )
            return

        reg = self._rpc_methods.get(request.method)
        cancel_event = threading.Event()
        self._rpc_cancel_events[request.id] = cancel_event

        def submit_worker(fn: Callable[[], Any]) -> Future[Any]:
            def wrapped() -> Any:
                bind_rpc_cancel_event(cancel_event)
                try:
                    return fn()
                finally:
                    bind_rpc_cancel_event(None)

            return self._get_rpc_executor().submit(wrapped)

        bind_rpc_cancel_event(cancel_event)
        if request.stream:
            self._rpc_stream_open.add(request.id)

        def on_stream_chunk(item: Any) -> None:
            if self._destroyed:
                return
            if threading.get_ident() == self._tk_thread_id:
                self._push_rpc_chunk(request.id, item)
                return
            self._enqueue_rpc_stream_chunk(request.id, item)

        try:
            outcome = dispatch_rpc(
                self._rpc_methods,
                request,
                submit_worker=submit_worker,
                include_traceback=self._rpc_traceback,
                on_stream_chunk=on_stream_chunk,
            )
        finally:
            bind_rpc_cancel_event(None)

        timeout = reg.timeout if reg is not None else None
        if isinstance(outcome, Future):
            self._track_rpc_future(request.id, outcome, timeout=timeout)
            return
        self._drop_rpc_cancel(request.id)
        ok, value = outcome
        if ok and value is RPC_STREAM_DONE:
            value = None
        self._settle_rpc(request.id, ok=ok, value=value)

    def _settle_tracked_rpc_future(self, req_id: str, done_fut: Future[Any]) -> None:
        after_id = self._rpc_timeout_after.pop(req_id, None)
        if after_id is not None:
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass
        if self._rpc_inflight.pop(req_id, None) is None:
            return
        if self._destroyed:
            return
        self._drop_rpc_cancel(req_id)
        try:
            value = done_fut.result()
        except Exception as exc:
            self._settle_rpc(
                req_id,
                ok=False,
                value=format_rpc_error(exc, include_traceback=self._rpc_traceback),
            )
            return
        if value is RPC_STREAM_DONE:
            self._settle_rpc(req_id, ok=True, value=None)
            return
        try:
            value = finalize_rpc_result(
                value,
                stream=req_id in self._rpc_stream_open,
                on_stream_chunk=lambda item: self._push_rpc_chunk(req_id, item),
            )
        except Exception as exc:
            self._settle_rpc(
                req_id,
                ok=False,
                value=format_rpc_error(exc, include_traceback=self._rpc_traceback),
            )
            return
        if value is RPC_STREAM_DONE:
            value = None
        self._settle_rpc(req_id, ok=True, value=value)

    def _drain_rpc_stream_chunks(self) -> None:
        """Deliver worker stream chunks on the Tk thread before settling."""
        if self._destroyed:
            self._discard_rpc_stream_queue()
            return
        while True:
            try:
                req_id, item = self._rpc_stream_queue.get_nowait()
            except queue.Empty:
                return
            if self._destroyed:
                self._discard_rpc_stream_queue()
                return
            self._push_rpc_chunk(req_id, item)

    def _drain_rpc_futures(self) -> None:
        """Settle worker RPC completions on the Tk thread (poll / idle)."""
        self._drain_rpc_stream_chunks()
        if self._destroyed:
            self._discard_rpc_done_queue()
            return
        while True:
            try:
                req_id, done_fut = self._rpc_done_queue.get_nowait()
            except queue.Empty:
                return
            if self._destroyed:
                self._discard_rpc_done_queue()
                return
            self._settle_tracked_rpc_future(req_id, done_fut)

    def _push_rpc_chunk(self, req_id: str, value: object) -> None:
        if not self._rpc_settle_allowed(req_id):
            return
        if self._destroyed or req_id not in self._rpc_stream_open:
            return
        try:
            script = stream_chunk_script(req_id, value)
        except RpcSerializationError:
            self._settle_rpc(
                req_id,
                ok=False,
                value=rpc_error(
                    "RpcSerializationError",
                    "value is not JSON-serializable",
                ),
            )
            return
        except RpcMessageTooLarge as exc:
            self._settle_rpc(
                req_id,
                ok=False,
                value=format_rpc_error(exc),
            )
            return
        native = self._webview
        if native is None:
            return
        try:
            native.eval_js(script)
        except Exception:
            traceback.print_exc()

    def _track_rpc_future(
        self,
        req_id: str,
        fut: Future[Any],
        *,
        timeout: float | None,
    ) -> None:
        self._rpc_inflight[req_id] = fut

        def _done(done_fut: Future[Any]) -> None:
            # Worker thread: never touch Tk here (Tcl is not thread-safe).
            if self._destroyed:
                return
            try:
                self._rpc_done_queue.put_nowait((req_id, done_fut))
            except Exception:
                return

        fut.add_done_callback(_done)

        if timeout is None:
            return

        def _on_timeout() -> None:
            self._rpc_timeout_after.pop(req_id, None)
            pending = self._rpc_inflight.pop(req_id, None)
            if pending is None:
                return
            self._signal_rpc_cancel(req_id)
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
        if not self._rpc_settle_allowed(req_id):
            self._drop_rpc_cancel(req_id)
            self._rpc_inflight.pop(req_id, None)
            self._rpc_stream_open.discard(req_id)
            return
        self._rpc_user_cancelled.discard(req_id)
        self._rpc_stream_open.discard(req_id)
        try:
            script = settle_script(req_id, ok=ok, value=value)
        except RpcSerializationError:
            try:
                script = settle_script(
                    req_id,
                    ok=False,
                    value=rpc_error(
                        "RpcSerializationError",
                        "value is not JSON-serializable",
                    ),
                )
            except RpcSerializationError:
                traceback.print_exc()
                return
        native = self._webview
        if native is None:
            return
        try:
            native.eval_js(script)
        except Exception:
            traceback.print_exc()
