"""Tkinter WebView widget."""

from __future__ import annotations

import math
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from typing import Literal, TypeAlias

from tkwry._core import (
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
)
from tkwry._core import (
    WebView as NativeWebView,
)
from tkwry._parent import (
    check_tk_thread_id,
    require_tk_thread,
    tk_embed_origin,
    tk_embed_parent,
)
from tkwry._runtime import GtkPump
from tkwry._url import _normalize_url, _validate_url
from tkwry.exceptions import WebViewDestroyedError, WebViewNotReadyError

if sys.platform == "darwin":
    from tkwry._macos import (
        _ensure_mac_pump,
        _ensure_mac_wakeup_pipe,
        _mac_service_wakeup,
        _register_macos_webview,
        _release_tk_keyboard_focus,
        _set_mac_webviews_input_active,
        _unregister_macos_webview,
    )

IpcHandler: TypeAlias = Callable[[str], None]
NavigationHandler: TypeAlias = Callable[[str], bool]
PageLoadHandler: TypeAlias = Callable[[PageLoadEvent, str], None]
TitleChangedHandler: TypeAlias = Callable[[str], None]
NewWindowHandler: TypeAlias = Callable[[str], NewWindowResponse]
DragDropHandler: TypeAlias = Callable[[DragDropEvent, list[str], tuple[int, int]], None]
EvalCallback: TypeAlias = Callable[[str], None]
EvalErrorHandler: TypeAlias = Callable[[Exception], None]
_PendingLoad: TypeAlias = tuple[Literal["url"], str] | tuple[Literal["html"], str]


def _validate_color_component(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not (0 <= value <= 255):
        raise ValueError(f"{name} must be 0-255, got {value}")


def _validate_background_color(color: tuple[int, int, int, int]) -> None:
    if not isinstance(color, tuple) or len(color) != 4:
        raise ValueError("background_color must be a (r, g, b, a) tuple of 4 ints")
    for val, name in zip(color, ("r", "g", "b", "a")):
        _validate_color_component(val, name)


class WebView:
    """Embed a system WebView (wry) inside an existing Tk ``Frame``.

    The host *frame* must be laid out with a real size (``pack`` / ``grid`` /
    ``place``) before the native webview is created. IPC, page-load,
    title-changed, eval callbacks, and drag-and-drop handlers run on the
    **Tk main thread** via an internal queue. Drag-and-drop is notify-only
    (``-> None``); OS drops are always accepted and cannot be denied from
    Python.

    **Navigation hooks** (``on_navigation``, ``on_new_window``) are invoked
    **synchronously on the WebKit thread** — wry needs an immediate return
    value, so they cannot be queued. Keep handlers fast; do not call Tk
    widgets or other thread-unsafe APIs from them. Defer work with
    ``frame.after`` if needed.

    **Navigation** (``load_url`` / ``load_html``): rapid calls are coalesced
    (**last-wins**) — ``load(A); load(B); load(C)`` navigates to ``C`` only.
    Before the native view exists, the last pending load is applied at creation
    (``load_html`` overrides a pending URL).

    **Page load** (``on_page_load``): fires ``Started`` and ``Finished`` for
    every navigation. Events that occurred while no handler was registered are
    **discarded** when a handler is attached.

    **JavaScript** (``eval_js`` / ``eval_js_with_callback``): ``eval_js`` is
    fire-and-forget (Tk idle, no return value). ``eval_js_with_callback`` is
    asynchronous; the callback receives the result string on the Tk main thread.

    Call :meth:`destroy` or destroy the host frame to release the native view.

    All public methods must run on the **Tk thread** (the thread that created
    the host frame's Tcl interpreter and runs the event loop). Calls from other
    threads raise ``RuntimeError``.
    """

    def __init__(
        self,
        frame: tk.Frame,
        *,
        width: int | None = None,
        height: int | None = None,
        url: str | None = None,
        html: str | None = None,
        ipc_handler: IpcHandler | None = None,
        devtools: bool = False,
        background_color: tuple[int, int, int, int] | None = None,
        user_agent: str | None = None,
        initialization_script: str | None = None,
        focused: bool = True,
        on_navigation: NavigationHandler | None = None,
        on_page_load: PageLoadHandler | None = None,
        on_title_changed: TitleChangedHandler | None = None,
        on_new_window: NewWindowHandler | None = None,
        drag_drop_handler: DragDropHandler | None = None,
    ) -> None:
        require_tk_thread(frame)
        if background_color is not None:
            _validate_background_color(background_color)
        self._frame = frame
        self._tk_thread_id = threading.get_ident()
        self._early_create = width is not None or height is not None
        self._init_width = max(width, 1) if width is not None else None
        self._init_height = max(height, 1) if height is not None else None
        self._destroyed = False
        self._ready_delivered = False
        self._ready_callbacks: list[Callable[[], None]] = []
        self._create_pending = False
        self._embed = tk_embed_parent(frame)
        self._webview: NativeWebView | None = None
        self._ipc_handler = ipc_handler
        self._on_navigation = on_navigation
        self._on_page_load = on_page_load
        self._on_title_changed = on_title_changed
        self._on_new_window = on_new_window
        self._drag_drop_handler = drag_drop_handler
        self._devtools = devtools
        self._background_color = background_color
        self._user_agent = user_agent
        self._initialization_script = initialization_script
        if sys.platform == "darwin" and focused:
            # Child WKWebView + focused=True fights Tk for first responder at create.
            focused = False
        self._focused = focused
        self._eval_result_queue: queue.SimpleQueue[tuple[int, EvalCallback, str]] = (
            queue.SimpleQueue()
        )
        self._event_poll_active = False
        self._wait_until_ready_active = False
        self._pending_eval_callbacks = 0
        # Bumped on destroy so late WebKit-thread delivers are discarded.
        self._eval_epoch = 0
        self._pending_url: str | None = url
        self._pending_html: str | None = html
        self._pending_load: _PendingLoad | None = None
        self._flush_load_scheduled = False
        self._bounds_sync_scheduled = False
        self._initial_load: _PendingLoad | None = None
        self._initial_load_attempt = 0
        self._initial_load_after_id: str | None = None

        GtkPump.attach(frame)
        self._frame_bind_ids: list[tuple[str, str]] = []
        for sequence, handler in (
            ("<Configure>", self._on_configure),
            ("<Map>", self._on_map),
            ("<Unmap>", self._on_unmap),
            ("<Destroy>", self._on_destroy),
        ):
            funcid = self._frame.bind(sequence, handler, add="+")
            self._frame_bind_ids.append((sequence, funcid))
        if sys.platform == "darwin":
            _register_macos_webview(self)
        if self._needs_event_poll():
            self._ensure_event_poll()
        if self._creation_size() is not None or self._early_create:
            self._schedule_try_create()

    def pack(self, **kwargs) -> None:
        self._require_tk_thread()
        self._frame.pack(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def grid(self, **kwargs) -> None:
        self._require_tk_thread()
        self._frame.grid(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def place(self, **kwargs) -> None:
        self._require_tk_thread()
        self._frame.place(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def __repr__(self) -> str:
        if self._destroyed:
            state = "destroyed"
            url = None
        elif self._webview is None:
            state = "pending"
            url = self._pending_url
            if url is None and self._pending_html is not None:
                url = "<html>"
        else:
            state = "ready"
            try:
                url = self._webview.url()
            except Exception:
                url = None
        try:
            frame = str(self._frame)
        except Exception:
            frame = "<unavailable>"
        return f"<WebView state={state} url={url!r} frame={frame}>"

    @property
    def ready(self) -> bool:
        """``True`` once the native webview exists with laid-out host geometry."""
        self._require_tk_thread()
        return (
            self._webview is not None and not self._destroyed and self._layout_ready()
        )

    @property
    def url(self) -> str | None:
        """Current document URL, or the pending URL before creation."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is None:
            return self._pending_url
        return self._webview.url()

    @property
    def native(self) -> NativeWebView | None:
        """Underlying :class:`tkwry._core.WebView`, or ``None`` if not created."""
        self._require_tk_thread()
        return self._webview

    @property
    def destroyed(self) -> bool:
        """``True`` after :meth:`destroy` or host-frame destruction."""
        self._require_tk_thread()
        return self._destroyed

    def bind(
        self,
        sequence: str,
        func: Callable[..., object],
        add: Literal["", "+"] | None = None,
    ) -> str:
        """Bind a Tk event on the host frame (e.g. ``\"<<WebViewReady>>\"``)."""
        self._require_tk_thread()
        result = self._frame.bind(sequence, func, add=add)
        if sequence == "<<WebViewReady>>" and self.ready:

            def _deliver_ready(
                _func: Callable = func, _frame: tk.Misc = self._frame
            ) -> None:
                if self._destroyed:
                    return
                captured: list[tk.Event] = []
                probe = "<<WebViewReady-Synthetic>>"

                def _capture(evt: tk.Event) -> None:
                    captured.append(evt)

                bind_id = _frame.bind(probe, _capture)
                try:
                    _frame.event_generate(probe)
                finally:
                    _frame.unbind(probe, bind_id)
                self._invoke_callback(_func, captured[0])

            self._frame.after_idle(_deliver_ready)
        return result

    def when_ready(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* once the native view exists and the host is laid out."""
        self._require_tk_thread()
        if self._destroyed:
            return
        if self.ready:

            def _deliver() -> None:
                if self._destroyed:
                    return
                self._invoke_callback(callback)

            self._frame.after_idle(_deliver)
        else:
            self._ready_callbacks.append(callback)

    def wait_until_ready(self, timeout: float = 30.0) -> bool:
        """Pump a nested Tk event loop until the webview is laid out or *timeout*.

        This is **not** a passive wait: it runs ``root.update()`` repeatedly and
        therefore re-enters other Tk callbacks while waiting. Prefer
        :meth:`when_ready` or ``bind(\"<<WebViewReady>>\")`` when you can avoid
        nesting the event loop (especially from handlers that touch Tk state).

        *timeout* must be a finite number of seconds ``> 0`` so unmapped or
        never-laid-out hosts cannot spin forever. Returns ``True`` if ready,
        ``False`` on timeout or if destroyed while waiting.

        Raises:
            ValueError: if *timeout* is missing, non-positive, or non-finite.
            RuntimeError: if called while another ``wait_until_ready`` is nested
                on this instance.
        """
        self._require_tk_thread()
        if type(timeout) is not int and type(timeout) is not float:
            raise ValueError("timeout must be a finite number of seconds > 0")
        timeout_s = float(timeout)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout must be a finite number of seconds > 0")

        if self.ready:
            return True
        if self._destroyed:
            return False
        if self._wait_until_ready_active:
            raise RuntimeError(
                "wait_until_ready() is already running on this WebView; "
                "nested calls are not supported (this pumps the Tk event loop)"
            )

        root = self._frame.winfo_toplevel()
        deadline = time.monotonic() + timeout_s
        self._wait_until_ready_active = True
        try:
            while not self.ready and not self._destroyed:
                if time.monotonic() >= deadline:
                    return False
                root.update_idletasks()
                root.update()
                if sys.platform == "linux":
                    from tkwry._core import pump_events

                    pump_events()
                time.sleep(0.01)
            return self.ready
        finally:
            self._wait_until_ready_active = False

    def destroy(self) -> None:
        """Hide and release the native webview without destroying the host frame."""
        self._require_tk_thread()
        if self._destroyed:
            return
        self._destroyed = True
        self._event_poll_active = False
        self._cancel_initial_load_timer()
        self._eval_epoch += 1
        self._pending_eval_callbacks = 0
        self._drain_eval_result_queue(discard=True)
        self._ready_delivered = False
        self._ready_callbacks.clear()
        self._unbind_frame_events()
        if self._webview is not None:
            native = self._webview
            try:
                native.destroy()
            except Exception:
                traceback.print_exc()
            finally:
                self._webview = None
        if sys.platform == "darwin":
            _unregister_macos_webview(self)
        elif sys.platform == "linux":
            GtkPump.detach(self._frame)

    def _unbind_frame_events(self) -> None:
        """Drop host-frame binds so ``destroy()`` does not pin this instance."""
        for sequence, funcid in self._frame_bind_ids:
            try:
                self._frame.unbind(sequence, funcid)
            except tk.TclError:
                pass
        self._frame_bind_ids.clear()

    def load_url(self, url: str) -> None:
        """Navigate to *url* (``http``/``https``/``file``; scheme optional).

        Local filesystem paths (``/path/to/page.html``, ``C:\\page.html``) are
        normalized to ``file://`` URLs so relative assets resolve correctly.

        Multiple rapid calls are coalesced (**last-wins**): only the final URL
        is loaded. Before the native view exists, the URL is stored and applied
        at creation (unless superseded by :meth:`load_html`).
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        normalized = _normalize_url(url)
        _validate_url(normalized)
        if self._webview is None:
            self._pending_url = normalized
            self._pending_html = None
            return
        # Supersede constructor deferred load so it cannot overwrite this nav.
        self._initial_load = None
        self._pending_load = ("url", normalized)
        self._schedule_flush_load()

    def load_html(self, html: str) -> None:
        """Load inline HTML.

        Like :meth:`load_url`, rapid calls are coalesced (**last-wins**).
        ``load_html`` supersedes any pending :meth:`load_url` call.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is None:
            self._pending_html = html
            self._pending_url = None
            return
        # Supersede constructor deferred load so it cannot overwrite this nav.
        self._initial_load = None
        self._pending_load = ("html", html)
        self._schedule_flush_load()

    def reload(self) -> None:
        native = self._require_ready("reload")
        # Supersede constructor deferred load so it cannot overwrite this reload.
        self._initial_load = None
        self._cancel_initial_load_timer()
        native.reload()
        if self._on_page_load is not None:
            self._ensure_event_poll()
            self._service_linux_events()

    def eval_js(self, script: str, *, on_error: EvalErrorHandler | None = None) -> None:
        """Evaluate JavaScript without waiting for a result.

        The script is scheduled on the Tk idle loop (not synchronous). There is
        no return value; use :meth:`eval_js_with_callback` when you need the
        result. If *on_error* is provided, it is called with the exception on
        failure; otherwise the traceback is printed to stderr.
        """
        self._require_ready("eval_js")
        self._frame.after_idle(lambda: self._run_eval_js(script, on_error))

    def eval_js_with_callback(
        self,
        script: str,
        callback: EvalCallback,
        *,
        on_error: EvalErrorHandler | None = None,
    ) -> None:
        """Evaluate JavaScript and invoke *callback* with the result string.

        Asynchronous: *callback* runs on the **Tk main thread** after the script
        completes. The result is always a ``str`` (including JSON literals).
        If *on_error* is provided, it is called with the exception on failure;
        otherwise the traceback is printed to stderr.
        """
        self._require_ready("eval_js_with_callback")
        epoch = self._eval_epoch
        self._pending_eval_callbacks += 1
        self._ensure_event_poll()

        def _run() -> None:
            if self._destroyed or self._webview is None or epoch != self._eval_epoch:
                self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)
                return

            def deliver(result: str) -> None:
                # May run on the WebKit thread — queue only; never touch Tk/counter.
                if epoch != self._eval_epoch:
                    return
                self._eval_result_queue.put((epoch, callback, result))

            try:
                self._webview.eval_js_with_callback(script, deliver)
            except Exception as exc:
                self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)
                if on_error is not None:
                    self._invoke_callback(on_error, exc)
                else:
                    traceback.print_exc()

        self._frame.after_idle(_run)

    def focus(self) -> None:
        """Move keyboard focus to the WebView (``makeFirstResponder`` on macOS)."""
        native = self._require_ready("focus")
        native.focus()
        if sys.platform == "darwin":
            toplevel = self._frame.winfo_toplevel()
            _set_mac_webviews_input_active(toplevel, self)
            _release_tk_keyboard_focus(toplevel)

    def focus_parent(self) -> None:
        """Return keyboard focus to the native parent view (macOS Tk coexistence)."""
        native = self._require_ready("focus_parent")
        native.focus_parent()
        if sys.platform == "darwin":
            _set_mac_webviews_input_active(self._frame.winfo_toplevel(), None)

    def set_background_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        native = self._require_ready("set_background_color")
        for val, name in ((r, "r"), (g, "g"), (b, "b"), (a, "a")):
            _validate_color_component(val, name)
        native.set_background_color(r, g, b, a)

    def open_devtools(self) -> None:
        self._require_ready("open_devtools").open_devtools()

    def close_devtools(self) -> None:
        self._require_ready("close_devtools").close_devtools()

    def is_devtools_open(self) -> bool:
        return self._require_ready("is_devtools_open").is_devtools_open()

    def set_ipc_handler(self, handler: IpcHandler | None) -> None:
        self._require_tk_thread()
        self._ipc_handler = handler
        if self._webview is not None:
            self._webview.set_ipc_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_navigation(self, handler: NavigationHandler | None) -> None:
        """Register a navigation hook (runs synchronously on the WebKit thread)."""
        self._require_tk_thread()
        self._on_navigation = handler
        if self._webview is not None:
            if handler is not None:
                self._webview.set_on_navigation(self._native_navigation)
            else:
                self._webview.clear_on_navigation()

    def set_on_page_load(self, handler: PageLoadHandler | None) -> None:
        self._require_tk_thread()
        self._on_page_load = handler
        if self._webview is not None:
            self._webview.set_page_load_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def sync_bounds(self) -> None:
        """Push the host frame's size and position to the native WebView.

        Called automatically on ``<Configure>``, ``<Map>``, and ``<Unmap>``.
        Call this manually after layout changes that do not emit Configure
        (e.g. custom geometry) so the WebView reflows — useful for centered
        images and responsive content.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        self._sync_bounds()

    def set_on_title_changed(self, handler: TitleChangedHandler | None) -> None:
        self._require_tk_thread()
        self._on_title_changed = handler
        if self._webview is not None:
            self._webview.set_title_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_new_window(self, handler: NewWindowHandler | None) -> None:
        """Register a new-window hook (runs synchronously on the WebKit thread)."""
        self._require_tk_thread()
        self._on_new_window = handler
        if self._webview is not None:
            if handler is not None:
                self._webview.set_on_new_window(self._native_new_window)
            else:
                self._webview.clear_on_new_window()

    def set_drag_drop_handler(self, handler: DragDropHandler | None) -> None:
        """Register a notify-only drop handler (runs on the Tk main thread).

        Events are queued from the WebKit thread; the handler cannot accept or
        deny the OS drop. Clearing with ``None`` stops native collection.
        """
        self._require_tk_thread()
        self._drag_drop_handler = handler
        if self._webview is not None:
            self._webview.set_drag_drop_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def _schedule_try_create(self) -> None:
        if self._destroyed or self._webview is not None or self._create_pending:
            return
        self._create_pending = True
        self._frame.after_idle(self._run_try_create)

    def _run_try_create(self) -> None:
        self._create_pending = False
        self._try_create()

    def _require_tk_thread(self) -> None:
        # Compare a plain int only — never touch Tk/Tcl from a foreign thread.
        check_tk_thread_id(self._tk_thread_id)

    def _require_ready(self, method: str) -> NativeWebView:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )
        if not self.ready:
            raise WebViewNotReadyError(
                f"WebView is not ready; call wait_until_ready() or bind to "
                f"<<WebViewReady>> before calling {method}()"
            )
        assert self._webview is not None
        return self._webview

    def _creation_size(self) -> tuple[int, int] | None:
        self._frame.update_idletasks()
        frame_w = self._frame.winfo_width()
        frame_h = self._frame.winfo_height()
        if frame_w > 1 and frame_h > 1:
            return frame_w, frame_h

        width = frame_w if frame_w > 1 else self._init_width
        height = frame_h if frame_h > 1 else self._init_height
        if width is None or height is None:
            return None
        return width, height

    def _layout_ready(self) -> bool:
        """Whether the host frame has real geometry for callbacks and API use."""
        if self._webview is None or self._destroyed:
            return False
        try:
            if not self._frame.winfo_exists():
                return False
            if self._frame.winfo_width() <= 1 or self._frame.winfo_height() <= 1:
                return False
            if sys.platform != "linux" and not self._frame.winfo_viewable():
                return False
            return True
        except tk.TclError:
            return False

    def _maybe_fire_ready(self) -> None:
        if self._ready_delivered or self._destroyed or self._webview is None:
            return
        if not self._layout_ready():
            return
        self._ready_delivered = True
        self._fire_ready()

    def _fire_ready(self) -> None:
        callbacks = self._ready_callbacks
        self._ready_callbacks = []

        def _deliver_ready() -> None:
            if self._destroyed:
                return
            # Defer bind handlers until create/bounds/poll paths return. event_generate
            # from idle is synchronous for bindings but no longer re-enters _try_create.
            self._frame.event_generate("<<WebViewReady>>")
            for callback in callbacks:
                if self._destroyed:
                    return
                self._invoke_callback(callback)

        self._frame.after_idle(_deliver_ready)

    def _needs_event_poll(self) -> bool:
        return any(
            (
                self._ipc_handler is not None,
                self._on_page_load is not None,
                self._on_title_changed is not None,
                self._drag_drop_handler is not None,
            )
        )

    def _native_drag_drop(
        self, event: DragDropEvent, paths: list[str], position: tuple[int, int]
    ) -> None:
        """Inject a drag-drop event into the same queue OS drops use (tests)."""
        native = self._webview
        if native is None or self._drag_drop_handler is None:
            return
        # Python handlers are authoritative for async queues.
        native.set_drag_drop_listening(True)
        native._enqueue_drag_drop_event(event, paths, position)
        self._ensure_event_poll()

    def _native_navigation(self, url: str) -> bool:
        if self._on_navigation is None:
            return True
        try:
            result = self._on_navigation(url)
        except Exception:
            traceback.print_exc()
            return False
        if type(result) is not bool:
            print(
                f"tkwry: on_navigation must return bool, got {type(result).__name__}",
                file=sys.stderr,
            )
            return False
        return result

    def _native_title_changed(self, title: str) -> None:
        native = self._webview
        if native is None or self._on_title_changed is None:
            return
        native.set_title_listening(True)
        native._enqueue_title_event(title)
        self._ensure_event_poll()

    def _native_new_window(self, url: str) -> NewWindowResponse:
        if self._on_new_window is None:
            return NewWindowResponse.Allow
        try:
            result = self._on_new_window(url)
        except Exception:
            traceback.print_exc()
            return NewWindowResponse.Deny
        if not isinstance(result, NewWindowResponse):
            print(
                "tkwry: on_new_window must return NewWindowResponse, "
                f"got {type(result).__name__}",
                file=sys.stderr,
            )
            return NewWindowResponse.Deny
        return result

    def _enqueue_ipc(self, message: str) -> None:
        native = self._webview
        if native is None or self._ipc_handler is None:
            return
        native.set_ipc_listening(True)
        native._enqueue_ipc_message(message)
        self._ensure_event_poll()

    def _sync_async_listening(self) -> None:
        native = self._webview
        if native is None:
            return
        native.set_ipc_listening(self._ipc_handler is not None)
        native.set_page_load_listening(self._on_page_load is not None)
        native.set_title_listening(self._on_title_changed is not None)
        native.set_drag_drop_listening(self._drag_drop_handler is not None)

    def _invoke_callback(self, callback: Callable[..., object], *args: object) -> None:
        try:
            callback(*args)
        except Exception:
            traceback.print_exc()

    def _deliver_ipc_messages(self) -> None:
        handler = self._ipc_handler
        native = self._webview
        if handler is None or native is None:
            return
        for message in native.drain_ipc_messages():
            self._invoke_callback(handler, message)

    def _deliver_title_events(self) -> None:
        handler = self._on_title_changed
        native = self._webview
        if handler is None or native is None:
            return
        for title in native.drain_title_events():
            self._invoke_callback(handler, title)

    def _deliver_drag_drop_events(self) -> None:
        handler = self._drag_drop_handler
        native = self._webview
        if handler is None or native is None:
            return
        for event, paths, position in native.drain_drag_drop_events():
            self._invoke_callback(handler, event, paths, position)

    def _deliver_page_load_events(self) -> None:
        page_load = self._on_page_load
        native = self._webview
        if native is None or page_load is None:
            return
        pending = native.drain_page_load_events()
        for event, page_url in pending:
            self._invoke_callback(page_load, event, page_url)

    def _service_linux_events(self, *, gtk_rounds: int = 32) -> None:
        if sys.platform != "linux" or self._destroyed:
            return
        from tkwry._core import pump_events

        for _ in range(gtk_rounds):
            pump_events()
        self._deliver_page_load_events()

    def _ensure_event_poll(self) -> None:
        if self._event_poll_active or self._destroyed:
            return
        self._event_poll_active = True
        self._frame.after(1, self._poll_events)

    def _drain_eval_result_queue(self, *, discard: bool = False) -> None:
        """Consume queued eval results on the Tk thread (or drop them on destroy)."""
        while True:
            try:
                epoch, callback, result = self._eval_result_queue.get_nowait()
            except queue.Empty:
                break
            if epoch != self._eval_epoch:
                continue
            if discard:
                self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)
                continue
            self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)
            self._invoke_callback(callback, result)

    def _poll_events(self) -> None:
        if self._destroyed:
            self._event_poll_active = False
            return
        if sys.platform == "linux":
            from tkwry._core import pump_events

            pump_events()
        elif sys.platform == "darwin":
            _mac_service_wakeup(self._frame.winfo_toplevel())

        handler = self._ipc_handler
        if handler is not None:
            self._deliver_ipc_messages()

        self._deliver_page_load_events()

        if self._on_title_changed is not None:
            self._deliver_title_events()

        if self._drag_drop_handler is not None:
            self._deliver_drag_drop_events()

        self._drain_eval_result_queue()

        if self._should_keep_polling():
            delay = 1 if sys.platform == "linux" else 10
            self._frame.after(delay, self._poll_events)
        else:
            # Clear before re-check so a concurrent ensure_event_poll can re-arm.
            self._event_poll_active = False
            if self._should_keep_polling():
                self._ensure_event_poll()

    def _should_keep_polling(self) -> bool:
        if self._needs_event_poll():
            return True
        return self._pending_eval_callbacks > 0 or not self._eval_result_queue.empty()

    def _try_create(self) -> None:
        if self._destroyed or self._webview is not None:
            return

        size = self._creation_size()
        if size is None:
            return
        width, height = size

        url = self._pending_url
        if url:
            url = _normalize_url(url)
            _validate_url(url)

        html = self._pending_html
        initial_load: _PendingLoad | None = None
        if html is not None:
            initial_load = ("html", html)
        elif url is not None:
            initial_load = ("url", url)

        kwargs: dict = {
            "width": width,
            "height": height,
            "visible": self._frame_should_show(),
            "devtools": self._devtools,
            "focused": self._focused,
        }
        if self._background_color is not None:
            kwargs["background_color"] = self._background_color
        if self._user_agent is not None:
            kwargs["user_agent"] = self._user_agent
        if self._initialization_script is not None:
            kwargs["initialization_script"] = self._initialization_script
        if self._on_navigation is not None:
            kwargs["on_navigation"] = self._native_navigation
        if self._on_new_window is not None:
            kwargs["on_new_window"] = self._native_new_window

        if sys.platform == "linux":
            from tkwry._core import pump_events

            for _ in range(20):
                pump_events()

        self._webview = NativeWebView(
            self._embed.handle,
            owner_thread=self._tk_thread_id,
            **kwargs,
        )
        self._sync_async_listening()
        self._pending_url = None
        self._pending_html = None
        self._sync_bounds()
        self._schedule_bounds_sync()
        if initial_load is not None:
            self._initial_load = initial_load
            if sys.platform == "linux":
                self._run_initial_load()
            if self._initial_load is not None:
                self._schedule_initial_load()
        if sys.platform == "darwin" and self._webview is not None:
            toplevel = self._frame.winfo_toplevel()
            _ensure_mac_wakeup_pipe(toplevel, self._webview)
            _ensure_mac_pump(toplevel)
        if self._needs_event_poll():
            self._ensure_event_poll()
            if sys.platform == "linux":
                for _ in range(10):
                    self._service_linux_events()
        self._maybe_fire_ready()

    def _run_eval_js(
        self, script: str, on_error: EvalErrorHandler | None = None
    ) -> None:
        if self._destroyed or self._webview is None:
            return
        try:
            self._webview.eval_js(script)
        except Exception as exc:
            if on_error is not None:
                self._invoke_callback(on_error, exc)
            else:
                traceback.print_exc()

    def _frame_ready_for_initial_load(self) -> bool:
        """Whether the host frame is laid out enough to load content."""
        try:
            if not self._frame.winfo_exists() or self._webview is None:
                return False
            # Creation already required a real size; Xvfb can still report 1×1 later.
            if sys.platform == "linux":
                return True
            if self._frame.winfo_width() <= 1 or self._frame.winfo_height() <= 1:
                return False
            return bool(self._frame.winfo_viewable())
        except tk.TclError:
            return False

    def _bump_initial_load_attempt(self) -> None:
        self._initial_load_attempt += 1
        if self._initial_load_attempt >= self._initial_load_attempts():
            self._initial_load = None

    def _schedule_flush_load(self) -> None:
        if self._flush_load_scheduled:
            return
        self._flush_load_scheduled = True
        self._frame.after_idle(self._flush_load)

    def _initial_load_attempts(self) -> int:
        """Headless Linux and macOS may need a second navigation after compositing."""
        if sys.platform in ("darwin", "linux"):
            return 2
        return 1

    def _cancel_initial_load_timer(self) -> None:
        after_id = self._initial_load_after_id
        if after_id is None:
            return
        self._initial_load_after_id = None
        try:
            self._frame.winfo_toplevel().after_cancel(after_id)
        except tk.TclError:
            pass

    def _schedule_initial_load(self) -> None:
        if self._initial_load is None:
            return
        self._cancel_initial_load_timer()
        try:
            toplevel = self._frame.winfo_toplevel()
            if sys.platform == "darwin":
                delay = 200
            else:
                delay = 150 if sys.platform == "linux" else 100
            self._initial_load_after_id = toplevel.after(delay, self._run_initial_load)
        except tk.TclError:
            self._initial_load_after_id = None

    def _maybe_reschedule_initial_load(self) -> None:
        if self._initial_load is not None and not self._destroyed:
            self._schedule_initial_load()

    def _run_initial_load(self) -> None:
        self._initial_load_after_id = None
        load = self._initial_load
        if load is None or self._destroyed or self._webview is None:
            return
        if self._pending_load is not None:
            # A later load_url/load_html already won; drop constructor content.
            self._initial_load = None
            return
        if not self._frame_ready_for_initial_load():
            self._maybe_reschedule_initial_load()
            return
        self._sync_bounds()
        # Re-check after sync: load_* may have cleared or replaced this.
        if self._initial_load is not load or self._pending_load is not None:
            self._initial_load = None
            return
        kind, payload = load
        try:
            if kind == "url":
                self._webview.load_url(payload)
            else:
                self._webview.load_html(payload)
        except Exception:
            traceback.print_exc()
            self._bump_initial_load_attempt()
            self._maybe_reschedule_initial_load()
            return
        self._sync_bounds()
        gtk_rounds = 64 if sys.platform == "linux" else 32
        self._service_linux_events(gtk_rounds=gtk_rounds)
        if self._on_page_load is not None:
            self._ensure_event_poll()
        self._initial_load = None

    def _flush_load(self) -> None:
        self._flush_load_scheduled = False
        if self._destroyed or self._webview is None or self._pending_load is None:
            return
        kind, payload = self._pending_load
        self._pending_load = None
        self._initial_load = None
        try:
            if kind == "url":
                self._webview.load_url(payload)
            else:
                self._webview.load_html(payload)
        except Exception:
            traceback.print_exc()
            return
        self._sync_bounds()
        self._service_linux_events()
        if self._on_page_load is not None:
            self._ensure_event_poll()

    def _bounds_size(self) -> tuple[int, int] | None:
        """Return the width/height to push, or None when geometry is not meaningful."""
        try:
            if not self._frame.winfo_exists():
                return None
            frame_w = self._frame.winfo_width()
            frame_h = self._frame.winfo_height()
            width = frame_w if frame_w > 1 else self._init_width
            height = frame_h if frame_h > 1 else self._init_height
            if width is None or height is None or width <= 1 or height <= 1:
                return None
            return width, height
        except tk.TclError:
            return None

    def _frame_should_show(self) -> bool:
        try:
            if not self._frame.winfo_exists():
                return False
            if sys.platform != "linux" and not self._frame.winfo_viewable():
                return False
            return self._bounds_size() is not None
        except tk.TclError:
            return False

    def _schedule_bounds_sync(self) -> None:
        if self._destroyed or self._bounds_sync_scheduled:
            return
        self._bounds_sync_scheduled = True
        try:
            self._frame.update_idletasks()
            self._frame.after_idle(self._deferred_sync_bounds)
        except tk.TclError:
            self._bounds_sync_scheduled = False

    def _deferred_sync_bounds(self) -> None:
        self._bounds_sync_scheduled = False
        self._sync_bounds()
        self._maybe_fire_ready()

    def _sync_bounds(self) -> bool:
        if self._webview is None:
            return False
        if not self._frame_should_show():
            try:
                self._webview.set_visible(False)
            except Exception:
                return False
            return False
        size = self._bounds_size()
        if size is None:
            try:
                self._webview.set_visible(False)
            except Exception:
                return False
            return False
        width, height = size
        try:
            self._frame.update_idletasks()
            x, y = tk_embed_origin(self._frame, root_relative=self._embed.root_relative)
        except tk.TclError:
            return False
        try:
            self._webview.set_bounds(x, y, width, height)
            self._webview.set_visible(True)
        except Exception:
            return False
        return True

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        if self._webview is None:
            self._schedule_try_create()
        else:
            self._schedule_bounds_sync()
            self._maybe_fire_ready()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._schedule_bounds_sync()
        self._maybe_fire_ready()
        self._frame.after_idle(self._run_initial_load)

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._schedule_bounds_sync()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self._frame:
            return
        self.destroy()
