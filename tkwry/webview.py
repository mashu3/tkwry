"""Tkinter WebView widget."""

from __future__ import annotations

import atexit
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import weakref
from collections.abc import Callable
from typing import Literal, TypeAlias, TypeVar, cast

from tkwry._core import (
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
)
from tkwry._core import (
    WebView as NativeWebView,
)
from tkwry._linux import GtkPump
from tkwry._parent import (
    check_tk_thread_id,
    require_tk_thread,
    tk_embed_origin,
    tk_embed_parent,
)
from tkwry._url import _normalize_url, _validate_url
from tkwry.exceptions import (
    WebViewCreationError,
    WebViewDestroyedError,
    WebViewNotReadyError,
)

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
_PendingEval: TypeAlias = tuple[float, EvalCallback, EvalErrorHandler | None]
_NativeEvalWait: TypeAlias = tuple[int, int, EvalCallback, EvalErrorHandler | None]
_SyncHookItem: TypeAlias = tuple[
    Callable[[], object],
    list[object],
    object,
    threading.Event,
    list[bool],
    list[bool],
    list[float],
]
_EVAL_CALLBACK_TIMEOUT_S = 30.0
_SYNC_HOOK_TIMEOUT_S = 30.0
_SYNC_HOOK_HANDLER_TIMEOUT_S = 30.0
_SYNC_HOOK_MAX_WAIT_S = _SYNC_HOOK_TIMEOUT_S + _SYNC_HOOK_HANDLER_TIMEOUT_S
_MIN_LAYOUT_DIMENSION = 2
_CREATE_MAX_ATTEMPTS = 30
_FLUSH_LOAD_MAX_ATTEMPTS = 3
_FLUSH_LOAD_RETRY_BASE_MS = 150
_FLUSH_LOAD_RETRY_MAX_MS = 2000
_NATIVE_TEARDOWN_MAX_ATTEMPTS = 100
_QUEUE_DROP_IPC = 0
_QUEUE_DROP_PAGE_LOAD = 1
_QUEUE_DROP_TITLE = 2
_QUEUE_DROP_DRAG_DROP = 3
_QUEUE_DROP_EVAL = 4
_T = TypeVar("_T")
_frame_webview_refs: dict[int, weakref.ReferenceType[WebView]] = {}
_atexit_destroy_drain_registered = False
_atexit_destroy_toplevels: list[weakref.ReferenceType[tk.Misc]] = []


def _frame_webview_weakref_dead(ref: weakref.ReferenceType[WebView]) -> None:
    dead = [key for key, entry in _frame_webview_refs.items() if entry is ref]
    for key in dead:
        _frame_webview_refs.pop(key, None)


def _claim_frame_host(frame: tk.Misc, web: WebView) -> None:
    """Raise if *frame* already hosts a live WebView."""
    key = id(frame)
    existing = _frame_webview_refs.get(key)
    if existing is not None:
        prior = existing()
        if prior is not None and not prior.destroyed:
            raise ValueError(
                "tkwry: only one WebView per host frame is supported; "
                "create a child frame for each embedded view"
            )
        if prior is None:
            del _frame_webview_refs[key]
    _frame_webview_refs[key] = weakref.ref(web, _frame_webview_weakref_dead)


def _release_frame_host(frame: tk.Misc, web: WebView) -> None:
    key = id(frame)
    existing = _frame_webview_refs.get(key)
    if existing is not None and existing() is web:
        del _frame_webview_refs[key]


def _validate_color_component(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not (0 <= value <= 255):
        raise ValueError(f"{name} must be 0-255, got {value}")


def _validate_dimension(value: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < _MIN_LAYOUT_DIMENSION:
        raise ValueError(f"{name} must be >= {_MIN_LAYOUT_DIMENSION}, got {value}")
    return value


def _validate_background_color(color: tuple[int, int, int, int]) -> None:
    if not isinstance(color, tuple) or len(color) != 4:
        raise ValueError("background_color must be a (r, g, b, a) tuple of 4 ints")
    for val, name in zip(color, ("r", "g", "b", "a")):
        _validate_color_component(val, name)


def _toplevel_wakeup_read_fd(toplevel: tk.Misc) -> int | None:
    if sys.platform == "darwin":
        return getattr(toplevel, "_tkwry_mac_wake_read_fd", None)
    return getattr(toplevel, "_tkwry_wake_read_fd", None)


def _pump_toplevel_wakeup_pipe(toplevel: tk.Misc) -> None:
    read_fd = _toplevel_wakeup_read_fd(toplevel)
    if read_fd is None:
        return
    try:
        import select

        while select.select([read_fd], [], [], 0)[0]:
            if not os.read(read_fd, 64):
                break
    except (OSError, ValueError):
        pass


def _drain_toplevel_sync_hooks(toplevel: tk.Misc) -> None:
    """Drain pending navigation/new-window hooks for all WebViews on *toplevel*."""
    _drain_pending_destroy_webviews(toplevel)
    refs = getattr(toplevel, "_tkwry_sync_hook_webviews", None)
    if not refs:
        return
    live: list[weakref.ReferenceType[WebView]] = []
    for ref in refs:
        web = ref()
        if web is None:
            continue
        live.append(ref)
        if not web._destroyed:
            web._drain_sync_hooks()
    if live:
        setattr(toplevel, "_tkwry_sync_hook_webviews", live)
    elif hasattr(toplevel, "_tkwry_sync_hook_webviews"):
        delattr(toplevel, "_tkwry_sync_hook_webviews")


def _drain_pending_destroy_webviews(toplevel: tk.Misc) -> None:
    """Run ``destroy()`` queued from off-thread ``__del__`` on the Tk thread."""
    refs = getattr(toplevel, "_tkwry_pending_destroy_webviews", None)
    if not refs:
        return
    live: list[weakref.ReferenceType[WebView]] = []
    for ref in refs:
        web = ref()
        if web is None or web._destroyed:
            continue
        if threading.get_ident() != web._tk_thread_id:
            live.append(ref)
            continue
        try:
            web._cancel_deferred_callbacks()
            web.destroy()
        except Exception:
            traceback.print_exc()
            if not web._destroyed:
                live.append(ref)
    if live:
        setattr(toplevel, "_tkwry_pending_destroy_webviews", live)
    elif hasattr(toplevel, "_tkwry_pending_destroy_webviews"):
        delattr(toplevel, "_tkwry_pending_destroy_webviews")


def _ensure_atexit_destroy_drain() -> None:
    global _atexit_destroy_drain_registered
    if _atexit_destroy_drain_registered:
        return
    _atexit_destroy_drain_registered = True
    atexit.register(_atexit_drain_pending_destroys)


def _track_atexit_destroy_toplevel(toplevel: tk.Misc) -> None:
    _ensure_atexit_destroy_drain()
    for ref in _atexit_destroy_toplevels:
        if ref() is toplevel:
            return
    _atexit_destroy_toplevels.append(weakref.ref(toplevel))


def _atexit_drain_pending_destroys() -> None:
    live: list[weakref.ReferenceType[tk.Misc]] = []
    for ref in _atexit_destroy_toplevels:
        toplevel = ref()
        if toplevel is None:
            continue
        live.append(ref)
        for _ in range(32):
            try:
                toplevel.update_idletasks()
                toplevel.update()
            except tk.TclError:
                break
            _drain_pending_destroy_webviews(toplevel)
            if not getattr(toplevel, "_tkwry_pending_destroy_webviews", None):
                break
        refs = getattr(toplevel, "_tkwry_pending_destroy_webviews", None)
        if not refs:
            continue
        for pending_ref in list(refs):
            web = pending_ref()
            if web is None or web._destroyed:
                continue
            if threading.get_ident() == web._tk_thread_id:
                try:
                    web._cancel_deferred_callbacks()
                    web.destroy()
                except Exception:
                    traceback.print_exc()
            else:
                try:
                    web._force_native_teardown()
                except Exception:
                    traceback.print_exc()
    _atexit_destroy_toplevels[:] = live


def _ensure_tk_wakeup_fileevent(toplevel: tk.Misc) -> None:
    """Register a Tcl readable handler so sync hooks drain without polling delay."""
    if sys.platform == "darwin" or getattr(toplevel, "_tkwry_wake_fileevent", False):
        return
    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is None:
        return

    def _on_wake(_fd: int, _mask: int) -> None:
        _pump_toplevel_wakeup_pipe(toplevel)
        _drain_toplevel_sync_hooks(toplevel)

    try:
        create_handler = getattr(toplevel, "createfilehandler", None)
        if create_handler is None:
            return
        create_handler(read_fd, tk.READABLE, _on_wake)
        setattr(toplevel, "_tkwry_wake_fileevent", True)
    except (tk.TclError, OSError, ValueError):
        pass


def _register_sync_hook_webview(toplevel: tk.Misc, web: WebView) -> None:
    refs: list[weakref.ReferenceType[WebView]] | None = getattr(
        toplevel, "_tkwry_sync_hook_webviews", None
    )
    if refs is None:
        refs = []
        setattr(toplevel, "_tkwry_sync_hook_webviews", refs)
    refs.append(weakref.ref(web))


def _unregister_sync_hook_webview(web: WebView) -> None:
    if sys.platform == "darwin":
        return
    try:
        toplevel = web._frame.winfo_toplevel()
    except tk.TclError:
        return
    refs = getattr(toplevel, "_tkwry_sync_hook_webviews", None)
    if not refs:
        return
    refs[:] = [entry for entry in refs if entry() is not web]
    if not refs and hasattr(toplevel, "_tkwry_sync_hook_webviews"):
        delattr(toplevel, "_tkwry_sync_hook_webviews")


def _release_tk_wakeup_pipe(toplevel: tk.Misc) -> None:
    """Close the Win/Linux sync-hook wakeup pipe when the last user is gone."""
    users = getattr(toplevel, "_tkwry_wake_pipe_users", None)
    if users is None:
        return
    users -= 1
    if users > 0:
        setattr(toplevel, "_tkwry_wake_pipe_users", users)
        return
    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is not None and getattr(toplevel, "_tkwry_wake_fileevent", False):
        try:
            delete_handler = getattr(toplevel, "deletefilehandler", None)
            if delete_handler is not None:
                delete_handler(read_fd)
        except tk.TclError:
            pass
    for fd in (
        read_fd,
        getattr(toplevel, "_tkwry_wake_write_fd", None),
    ):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    for attr in (
        "_tkwry_wake_read_fd",
        "_tkwry_wake_write_fd",
        "_tkwry_wake_pipe_users",
        "_tkwry_wake_fileevent",
        "_tkwry_sync_hook_webviews",
    ):
        if hasattr(toplevel, attr):
            delattr(toplevel, attr)


def _noop_native_eval_callback(_result: str) -> None:
    """Stub passed to Rust; Python delivers via ``_native_eval_wait``."""


class WebView:
    """Embed a system WebView (wry) inside an existing Tk ``Frame``.

    The host *frame* must be laid out with a real size (``pack`` / ``grid`` /
    ``place``) before the native webview is created. IPC, page-load,
    title-changed, eval callbacks, and drag-and-drop handlers run on the
    **Tk main thread** via an internal queue. Drag-and-drop is notify-only
    (``-> None``); OS drops are always accepted and cannot be denied from
    Python.

    **Lifecycle state** (maintainers: triage ``ready`` / initial load /
    ``destroy`` regressions against this table before patching):

    +---------------+--------+-------+-----------+---------------------------+
    | State         | native | ready | destroyed | Allowed public API        |
    +===============+========+=======+===========+===========================+
    | pre-create    | None   | False | False     | ``load_*``,               |
    |               |        |       |           | ``wait_until_ready``,     |
    |               |        |       |           | handler setters,          |
    |               |        |       |           | ``bind`` / ``when_ready``,|
    |               |        |       |           | ``sync_bounds``,          |
    |               |        |       |           | ``destroy``               |
    +---------------+--------+-------+-----------+---------------------------+
    | unmapped      | Some   | False | False     | Same as pre-create;       |
    |               |        |       |           | ``_require_ready`` APIs   |
    |               |        |       |           | (``eval_js``, ``reload``, |
    |               |        |       |           | ``focus``, …) raise       |
    |               |        |       |           | ``WebViewNotReadyError``  |
    +---------------+--------+-------+-----------+---------------------------+
    | ready+mapped  | Some   | True  | False     | All public methods        |
    +---------------+--------+-------+-----------+---------------------------+
    | destroyed     | None   | —     | True      | None; raises              |
    |               |        |       |           | ``WebViewDestroyedError`` |
    +---------------+--------+-------+-----------+---------------------------+

    **Initial load** (constructor ``url`` / ``html`` vs user ``load_*``):

    1. **At create** — pending ``url``/``html`` is captured once in
       ``_try_create`` as ``_initial_load``.
    2. **Post-ready** — after native creation, ``_run_initial_load`` is
       scheduled once (delayed) when the host frame is viewable with real
       geometry.
    3. **User ``load_*`` last-wins** — :meth:`load_url` / :meth:`load_html`
       supersede any pending constructor load and coalesce rapid calls (only
       the final URL/HTML is applied).

    **Navigation hooks** (``on_navigation``, ``on_new_window``) must return
    immediately to WebKit, so the native layer blocks until your handler
    finishes. Handlers run on the **Tk main thread** (queued from WebKit).
    Keep them fast.

    **Navigation** (``load_url`` / ``load_html``): rapid calls are coalesced
    (**last-wins**) — ``load(A); load(B); load(C)`` navigates to ``C`` only.
    Before the native view exists, the last pending load is applied at creation
    (``load_html`` overrides a pending URL). If both ``url`` and ``html`` are
    passed to the constructor, ``html`` wins and a warning is printed to stderr.

    **Ready** (``<<WebViewReady>>`` / :meth:`when_ready`): fires once per
    instance when the native view first becomes laid out; unmap/remap does not
    re-fire the event.

    **Page load** (``on_page_load``): fires ``Started`` and ``Finished`` for
    every navigation. Events are buffered (up to a fixed cap) until a handler
    is registered and delivered when :meth:`set_on_page_load` attaches one.

    **JavaScript** (``eval_js`` / ``eval_js_with_callback``): ``eval_js`` is
    fire-and-forget (Tk idle, no return value). ``eval_js_with_callback`` is
    asynchronous; the callback receives the result string on the Tk main thread.

    Call :meth:`destroy` or destroy the host frame to release the native view.
    After :meth:`destroy`, the instance cannot be reused; create a new
    ``WebView`` on the same or another frame instead.

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
        self._toplevel: tk.Misc
        try:
            self._toplevel = frame.winfo_toplevel()
        except tk.TclError:
            self._toplevel = frame
        self._tk_thread_id = threading.get_ident()
        self._early_create = width is not None or height is not None
        self._init_width = (
            _validate_dimension(width, "width") if width is not None else None
        )
        self._init_height = (
            _validate_dimension(height, "height") if height is not None else None
        )
        self._destroyed = False
        self._ready_delivered = False
        self._ready_pending = False
        self._ready_callbacks: list[Callable[[], None]] = []
        self._create_pending = False
        self._create_attempt = 0
        self._creation_error: BaseException | None = None
        self._flush_load_attempt = 0
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
        self._focus_when_ready = False
        if sys.platform == "darwin" and focused:
            # Child WKWebView + focused=True fights Tk for first responder at create.
            self._focus_when_ready = True
            focused = False
        self._focused = focused
        self._event_poll_active = False
        self._wait_until_ready_active = False
        self._pending_eval_callbacks = 0
        self._eval_token_seq = 0
        self._pending_eval_tokens: dict[int, _PendingEval] = {}
        self._native_eval_wait: dict[int, _NativeEvalWait] = {}
        self._sync_hook_queue: queue.SimpleQueue[_SyncHookItem] = queue.SimpleQueue()
        self._sync_hook_depth = 0
        self._tk_wakeup_write_fd: int | None = None
        # Bumped on destroy so late WebKit-thread delivers are discarded.
        self._eval_epoch = 0
        if url is not None and html is not None:
            print(
                "tkwry: html= takes precedence over url= when both are given",
                file=sys.stderr,
            )
        if url is not None:
            url = _normalize_url(url)
            _validate_url(url)
        self._pending_url = url
        self._pending_html = html
        self._pending_load: _PendingLoad | None = None
        self._flush_load_scheduled = False
        self._post_nav_drain_scheduled = False
        self._in_poll_events = False
        self._pending_eval_js: tuple[str, EvalErrorHandler | None] | None = None
        self._eval_js_scheduled = False
        self._local_queue_drop_counts = [0, 0, 0, 0, 0]
        self._bounds_sync_scheduled = False
        self._stacking_sync_scheduled = False
        self._initial_load: _PendingLoad | None = None
        self._initial_load_attempt = 0
        self._initial_load_after_id: str | None = None
        self._deferred_after_ids: list[str] = []
        self._native_teardown_pending: NativeWebView | None = None
        self._native_teardown_attempts = 0

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
        _claim_frame_host(frame, self)

    def pack(self, **kwargs) -> None:
        self._require_not_destroyed("pack")
        self._frame.pack(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def grid(self, **kwargs) -> None:
        self._require_not_destroyed("grid")
        self._frame.grid(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def place(self, **kwargs) -> None:
        self._require_not_destroyed("place")
        self._frame.place(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()
        self._maybe_fire_ready()

    def __repr__(self) -> str:
        self._require_tk_thread()
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
            if self._pending_url is not None:
                return self._pending_url
            if self._pending_html is not None:
                return "<html>"
            return None
        return self._webview.url()

    @property
    def creation_failed(self) -> bool:
        """``True`` when native creation was abandoned after all retries."""
        self._require_tk_thread()
        return self._creation_error is not None

    @property
    def creation_error(self) -> BaseException | None:
        """The exception from the final failed creation attempt, if any."""
        self._require_tk_thread()
        return self._creation_error

    @property
    def native(self) -> NativeWebView | None:
        """Underlying :class:`tkwry._core.WebView`, or ``None`` if not created."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
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
        self._require_not_destroyed("bind")
        result = self._frame.bind(sequence, func, add=add)
        if sequence == "<<WebViewReady>>" and self._ready_delivered:

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
                if captured:
                    evt = captured[0]
                else:
                    evt = tk.Event()
                    evt.widget = _frame
                self._invoke_callback(_func, evt)

            self._frame.after_idle(_deliver_ready)
        return result

    def when_ready(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* once the native view exists and the host is laid out."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._ready_delivered:

            def _deliver() -> None:
                if self._destroyed:
                    return
                self._invoke_callback(callback)

            self._frame.after_idle(_deliver)
        else:
            self._ready_callbacks.append(callback)

    def wait_until_ready(self, timeout: float = 30.0) -> bool:
        """Pump a nested Tk event loop until the webview is laid out or *timeout*.

        This pumps the Tk event loop via ``update_idletasks`` and ``update``.
        Nested :meth:`wait_until_ready` on the same instance raises
        :exc:`RuntimeError`. Prefer
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
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite number of seconds > 0")
        timeout_s = float(timeout)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout must be a finite number of seconds > 0")

        if self.ready:
            return True
        if self._creation_error is not None:
            return False
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
                if self._creation_error is not None:
                    return False
                if time.monotonic() >= deadline:
                    return False
                self._pump_wait_until_ready(root)
                time.sleep(0.01)
            return self.ready
        finally:
            self._wait_until_ready_active = False

    def __del__(self) -> None:
        try:
            if not hasattr(self, "_destroyed") or self._destroyed:
                return
            if threading.get_ident() == self._tk_thread_id:
                self._cancel_deferred_callbacks()
                self.destroy()
            else:
                self._schedule_destroy_on_tk_thread()
        except Exception:
            if hasattr(self, "_destroyed") and not self._destroyed:
                try:
                    self._teardown_native_if_alive()
                except Exception:
                    pass

    def _schedule_destroy_on_tk_thread(self) -> None:
        """Best-effort ``destroy()`` when ``__del__`` runs off the Tk thread."""
        tk_thread_id = self._tk_thread_id

        def _run() -> None:
            if threading.get_ident() != tk_thread_id:
                return
            if self._destroyed:
                return
            self._cancel_deferred_callbacks()
            self.destroy()

        if threading.get_ident() == tk_thread_id:
            try:
                self._frame.after(0, _run)
            except (AttributeError, tk.TclError, RuntimeError):
                self._teardown_native_if_alive()
            return

        toplevel = getattr(self, "_toplevel", None)
        if toplevel is None:
            self._teardown_native_if_alive()
            return

        try:
            pending: list[weakref.ReferenceType[WebView]] | None = getattr(
                toplevel, "_tkwry_pending_destroy_webviews", None
            )
            if pending is None:
                pending = []
                setattr(toplevel, "_tkwry_pending_destroy_webviews", pending)
            pending.append(weakref.ref(self))
            _track_atexit_destroy_toplevel(toplevel)
            write_fd = self._tk_wakeup_write_fd
            if write_fd is None:
                if sys.platform == "darwin":
                    write_fd = getattr(toplevel, "_tkwry_mac_wake_write_fd", None)
                else:
                    write_fd = getattr(toplevel, "_tkwry_wake_write_fd", None)
            if write_fd is None:
                self._teardown_native_if_alive()
                return
            os.write(write_fd, b"\x01")
        except Exception:
            self._teardown_native_if_alive()

    def _teardown_native_if_alive(self) -> None:
        """Release the native WebView when Tk teardown is impossible."""
        if self._destroyed:
            return
        self._destroyed = True
        self._event_poll_active = False
        self._eval_epoch += 1
        self._pending_eval_callbacks = 0
        self._pending_eval_tokens.clear()
        self._native_eval_wait.clear()
        self._abort_sync_hooks()
        try:
            _release_frame_host(self._frame, self)
        except Exception:
            pass
        _unregister_sync_hook_webview(self)
        self._release_native_view(hide=False)
        if sys.platform == "darwin":
            try:
                _unregister_macos_webview(self)
            except Exception:
                pass

    def destroy(self) -> None:
        """Hide and release the native webview without destroying the host frame.

        The instance cannot be reused after this call; create a new ``WebView``
        if you need another embedded view.
        """
        self._require_tk_thread()
        if self._destroyed:
            return
        self._destroyed = True
        self._cancel_deferred_callbacks()
        self._eval_epoch += 1
        if self._pending_eval_tokens:
            self._bump_queue_drop(_QUEUE_DROP_EVAL, len(self._pending_eval_tokens))
        self._pending_eval_callbacks = 0
        self._pending_eval_tokens.clear()
        self._native_eval_wait.clear()
        self._pending_eval_js = None
        self._eval_js_scheduled = False
        self._abort_sync_hooks()
        self._ready_delivered = False
        self._ready_pending = False
        self._ready_callbacks.clear()
        _release_frame_host(self._frame, self)
        self._unbind_frame_events()
        _unregister_sync_hook_webview(self)
        had_native = self._webview is not None
        self._release_native_view(hide=True)
        if self._tk_wakeup_write_fd is not None and sys.platform != "darwin":
            self._tk_wakeup_write_fd = None
            try:
                _release_tk_wakeup_pipe(self._frame.winfo_toplevel())
            except tk.TclError:
                pass
        if sys.platform == "darwin":
            _unregister_macos_webview(self)
        elif sys.platform == "linux":
            GtkPump.detach(self._frame)
            if had_native or self._native_teardown_pending is not None:
                from tkwry._linux import pump_gtk_events

                for _ in range(24):
                    if self._native_teardown_pending is not None:
                        self._finish_native_teardown()
                    pump_gtk_events()
                    if self._native_teardown_pending is None:
                        break
                    try:
                        self._toplevel.update_idletasks()
                    except tk.TclError:
                        break
        if self._native_teardown_pending is not None:
            self._ensure_event_poll()
        else:
            self._event_poll_active = False

    def _unbind_frame_events(self) -> None:
        """Drop host-frame binds so ``destroy()`` does not pin this instance."""
        for sequence, funcid in self._frame_bind_ids:
            try:
                self._frame.unbind(sequence, funcid)
            except tk.TclError:
                pass
        self._frame_bind_ids.clear()

    def _ensure_gtk_pump_attached(self) -> None:
        if sys.platform != "linux" or self._destroyed or self._webview is None:
            return
        try:
            GtkPump.ensure_attached(self._frame)
        except tk.TclError:
            GtkPump._schedule_attach_retry(self._frame)

    def _attach_gtk_pump_for_native(self) -> None:
        if sys.platform != "linux" or self._destroyed:
            return
        try:
            GtkPump.ensure_attached(self._frame)
        except tk.TclError:
            GtkPump._schedule_attach_retry(self._frame)

    def _native_is_alive(self, native: NativeWebView) -> bool:
        try:
            return native.is_alive()
        except Exception:
            return False

    def _hide_native_view(self, native: NativeWebView) -> None:
        try:
            native.set_visible(False)
        except Exception:
            pass

    def _release_native_view(self, *, hide: bool) -> None:
        native = self._webview
        if native is None:
            return
        if hide:
            self._hide_native_view(native)
        try:
            native.destroy()
        except Exception:
            traceback.print_exc()
        if self._native_is_alive(native):
            self._native_teardown_pending = native
            self._native_teardown_attempts = 0
        self._webview = None
        if self._native_teardown_pending is not None:
            self._ensure_event_poll()

    def _force_native_teardown(self) -> None:
        """Best-effort native release when Tk-thread destroy is unavailable."""
        native = self._webview
        if native is None and self._native_teardown_pending is not None:
            native = self._native_teardown_pending
        if native is None:
            return
        try:
            native.force_destroy()
        except Exception:
            traceback.print_exc()
        self._native_teardown_pending = None
        self._native_teardown_attempts = 0
        self._webview = None

    def _finish_native_teardown(self) -> None:
        native = self._native_teardown_pending
        if native is None:
            return
        try:
            if self._native_is_alive(native):
                self._hide_native_view(native)
                native.destroy()
            if not self._native_is_alive(native):
                self._native_teardown_pending = None
                self._native_teardown_attempts = 0
                if self._destroyed and not self._should_keep_polling():
                    self._event_poll_active = False
                return
            self._native_teardown_attempts += 1
            if self._native_teardown_attempts >= _NATIVE_TEARDOWN_MAX_ATTEMPTS:
                print(
                    "tkwry: native teardown timed out after "
                    f"{_NATIVE_TEARDOWN_MAX_ATTEMPTS} poll attempts; "
                    "forcing release",
                    file=sys.stderr,
                )
                try:
                    native.force_destroy()
                except Exception:
                    traceback.print_exc()
                self._native_teardown_pending = None
                self._native_teardown_attempts = 0
                if self._destroyed and not self._should_keep_polling():
                    self._event_poll_active = False
        except Exception:
            traceback.print_exc()

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
        if self._webview is None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call load_url()"
            ) from self._creation_error
        if self._webview is None:
            self._pending_url = normalized
            self._pending_html = None
            return
        if self._sync_hook_depth > 0:
            self._initial_load = None
            self._pending_load = ("url", normalized)
            self._track_after(self._frame.after_idle(self._flush_load))
            return
        # Supersede constructor deferred load so it cannot overwrite this nav.
        self._initial_load = None
        self._pending_load = ("url", normalized)
        self._dispatch_pending_load()

    def load_html(self, html: str) -> None:
        """Load inline HTML.

        Like :meth:`load_url`, rapid calls are coalesced (**last-wins**).
        ``load_html`` supersedes any pending :meth:`load_url` call.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call load_html()"
            ) from self._creation_error
        if self._webview is None:
            self._pending_html = html
            self._pending_url = None
            return
        if self._sync_hook_depth > 0:
            self._initial_load = None
            self._pending_load = ("html", html)
            self._track_after(self._frame.after_idle(self._flush_load))
            return
        # Supersede constructor deferred load so it cannot overwrite this nav.
        self._initial_load = None
        self._pending_load = ("html", html)
        self._dispatch_pending_load()

    def _dispatch_pending_load(self) -> None:
        """Run coalesced load on Linux immediately when not inside event poll."""
        if (
            sys.platform == "linux"
            and self._webview is not None
            and self._sync_hook_depth == 0
            and not self._in_poll_events
        ):
            if self._flush_load_scheduled:
                return
            self._flush_load_scheduled = True
            self._flush_load()
            return
        self._schedule_flush_load()

    def reload(self) -> None:
        native = self._require_ready("reload")
        # Supersede constructor deferred load so it cannot overwrite this reload.
        self._initial_load = None
        self._cancel_initial_load_timer()
        # Drop any idle-coalesced load_url/load_html so it cannot overwrite reload.
        self._pending_load = None
        self._flush_load_attempt = 0
        if self._sync_hook_depth > 0:
            self._track_after(self._frame.after_idle(self._run_deferred_reload))
            return
        native.reload()
        if self._on_page_load is not None:
            self._ensure_event_poll()
        self._finish_navigation()

    def _run_deferred_reload(self) -> None:
        if self._destroyed or self._webview is None:
            return
        try:
            self._webview.reload()
        except Exception:
            traceback.print_exc()
            return
        if self._on_page_load is not None:
            self._ensure_event_poll()
        self._finish_navigation()

    def eval_js(self, script: str, *, on_error: EvalErrorHandler | None = None) -> None:
        """Evaluate JavaScript without waiting for a result.

        The script is scheduled on the Tk idle loop (not synchronous). There is
        no return value; use :meth:`eval_js_with_callback` when you need the
        result. If *on_error* is provided, it is called with the exception on
        failure; otherwise the traceback is printed to stderr.
        """
        self._require_ready("eval_js")
        self._pending_eval_js = (script, on_error)
        self._schedule_eval_js()

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
        If *on_error* is provided, it is called with the exception on failure
        or on timeout (30s); otherwise the traceback is printed to stderr and
        *callback* is not invoked on timeout.
        """
        self._require_ready("eval_js_with_callback")
        epoch = self._eval_epoch
        token = self._register_pending_eval(callback, on_error)
        self._ensure_event_poll()

        def _run() -> None:
            if self._destroyed or self._webview is None or epoch != self._eval_epoch:
                self._release_pending_eval(token)
                return

            try:
                native_token = self._webview.eval_js_with_callback(
                    script, _noop_native_eval_callback
                )
            except Exception as exc:
                self._release_pending_eval(token)
                if on_error is not None:
                    self._invoke_callback(on_error, exc)
                else:
                    traceback.print_exc()
                return
            self._native_eval_wait[native_token] = (
                epoch,
                token,
                callback,
                on_error,
            )

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
        self._background_color = (r, g, b, a)
        native.set_background_color(r, g, b, a)

    def set_user_agent(self, user_agent: str | None) -> None:
        """Set the user agent applied when the native view is first created."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is not None:
            raise ValueError(
                "user_agent cannot be changed after the native WebView is created"
            )
        self._user_agent = user_agent

    def set_initialization_script(self, script: str | None) -> None:
        """Set the initialization script applied when the native view is created."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is not None:
            raise ValueError(
                "initialization_script cannot be changed after the native "
                "WebView is created"
            )
        self._initialization_script = script

    def open_devtools(self) -> None:
        self._require_ready("open_devtools").open_devtools()

    def close_devtools(self) -> None:
        self._require_ready("close_devtools").close_devtools()

    def is_devtools_open(self) -> bool:
        return self._require_ready("is_devtools_open").is_devtools_open()

    def set_ipc_handler(self, handler: IpcHandler | None) -> None:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_ipc_handler()"
            ) from self._creation_error
        self._ipc_handler = handler
        if self._webview is not None:
            self._webview.set_ipc_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_navigation(self, handler: NavigationHandler | None) -> None:
        """Register a navigation hook (runs on the Tk main thread)."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_navigation()"
            ) from self._creation_error
        self._on_navigation = handler
        if self._webview is not None:
            if handler is not None:
                self._webview.set_on_navigation(self._native_navigation)
            else:
                self._webview.clear_on_navigation()
        if handler is not None:
            self._ensure_tk_wakeup_pipe()
            self._ensure_event_poll()

    def set_on_page_load(self, handler: PageLoadHandler | None) -> None:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_page_load()"
            ) from self._creation_error
        self._on_page_load = handler
        if self._webview is not None:
            self._webview.set_page_load_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()
            self._deliver_page_load_events()

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
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call sync_bounds()"
            ) from self._creation_error
        self._sync_bounds_and_stacking()

    def take_queue_drop_counts(self) -> tuple[int, int, int, int, int]:
        """Return overflow drop counts since the last call.

        Returns ``(ipc, page_load, title, drag_drop, eval)``. Each internal
        Each internal queue caps at 2048 pending items; additional events are
        compacted or discarded and counted here so applications can detect
        handler backlogs.
        """
        self._require_tk_thread()
        local = self._take_local_queue_drop_counts()
        if self._destroyed or self._webview is None:
            return local
        native = self._webview.take_queue_drop_counts()
        return (
            local[0] + native[0],
            local[1] + native[1],
            local[2] + native[2],
            local[3] + native[3],
            local[4] + native[4],
        )

    def set_on_title_changed(self, handler: TitleChangedHandler | None) -> None:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_title_changed()"
            ) from self._creation_error
        self._on_title_changed = handler
        if self._webview is not None:
            self._webview.set_title_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_new_window(self, handler: NewWindowHandler | None) -> None:
        """Register a new-window hook (runs on the Tk main thread)."""
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_new_window()"
            ) from self._creation_error
        self._on_new_window = handler
        if self._webview is not None:
            if handler is not None:
                self._webview.set_on_new_window(self._native_new_window)
            else:
                self._webview.clear_on_new_window()
        if handler is not None:
            self._ensure_tk_wakeup_pipe()
            self._ensure_event_poll()

    def set_drag_drop_handler(self, handler: DragDropHandler | None) -> None:
        """Register a notify-only drop handler (runs on the Tk main thread).

        Events are queued from the WebKit thread; the handler cannot accept or
        deny the OS drop. Clearing with ``None`` stops native collection.
        """
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_drag_drop_handler()"
            ) from self._creation_error
        self._drag_drop_handler = handler
        if self._webview is not None:
            self._webview.set_drag_drop_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def _schedule_try_create(self, *, delay_ms: int | None = None) -> None:
        if (
            self._destroyed
            or self._webview is not None
            or self._create_pending
            or self._creation_error is not None
        ):
            return
        self._create_pending = True
        if delay_ms is None:
            self._track_after(self._frame.after_idle(self._run_try_create))
        else:
            self._track_after(self._frame.after(delay_ms, self._run_try_create))

    def _track_after(self, after_id: str | None) -> str | None:
        if after_id:
            self._deferred_after_ids.append(after_id)
        return after_id

    def _cancel_deferred_callbacks(self) -> None:
        self._cancel_initial_load_timer()
        self._create_pending = False
        self._flush_load_scheduled = False
        self._eval_js_scheduled = False
        self._bounds_sync_scheduled = False
        self._stacking_sync_scheduled = False
        self._pending_eval_js = None
        after_ids = self._deferred_after_ids
        self._deferred_after_ids = []
        for after_id in after_ids:
            if not after_id:
                continue
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass

    def _run_try_create(self) -> None:
        self._create_pending = False
        self._try_create()

    def _require_tk_thread(self) -> None:
        # Compare a plain int only — never touch Tk/Tcl from a foreign thread.
        check_tk_thread_id(self._tk_thread_id)

    def _require_not_destroyed(self, method: str) -> None:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )

    def _require_ready(self, method: str) -> NativeWebView:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )
        if self._creation_error is not None:
            raise WebViewCreationError(
                f"WebView native creation failed; cannot call {method}()"
            ) from self._creation_error
        if not self.ready:
            raise WebViewNotReadyError(
                f"WebView is not ready; call wait_until_ready() or bind to "
                f"<<WebViewReady>> before calling {method}()"
            )
        assert self._webview is not None
        return self._webview

    def _bump_queue_drop(self, kind: int, count: int = 1) -> None:
        if count <= 0:
            return
        self._local_queue_drop_counts[kind] += count

    def _take_local_queue_drop_counts(self) -> tuple[int, int, int, int, int]:
        ipc, page_load, title, drag_drop, eval_ = self._local_queue_drop_counts
        self._local_queue_drop_counts = [0, 0, 0, 0, 0]
        return (ipc, page_load, title, drag_drop, eval_)

    def _pump_wait_until_ready(self, root: tk.Misc) -> None:
        """Advance this WebView; ``update_idletasks`` runs before ``update``."""
        root.update_idletasks()
        if (
            not self._destroyed
            and self._webview is None
            and self._creation_error is None
        ):
            if not self._create_pending and self._creation_size() is not None:
                self._try_create()
        if self._webview is not None and not self._destroyed:
            if self._bounds_sync_scheduled:
                self._deferred_sync_bounds()
            elif not self._layout_ready():
                self._sync_bounds()
            if self._should_keep_polling() or self._event_poll_active:
                self._poll_events()
        if sys.platform == "linux":
            from tkwry._linux import pump_gtk_events

            pump_gtk_events()
        try:
            root.update()
        except tk.TclError:
            pass

    def _schedule_eval_js(self) -> None:
        if self._eval_js_scheduled:
            return
        self._eval_js_scheduled = True
        self._track_after(self._frame.after_idle(self._flush_eval_js))

    def _flush_eval_js(self) -> None:
        self._eval_js_scheduled = False
        pending = self._pending_eval_js
        self._pending_eval_js = None
        if pending is None or self._destroyed or self._webview is None:
            return
        script, on_error = pending
        self._run_eval_js(script, on_error)

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
        if width <= 1 or height <= 1:
            return None
        return width, height

    def _layout_ready(self) -> bool:
        """Whether the host frame has real geometry for callbacks and API use."""
        if self._webview is None or self._destroyed:
            return False
        return self._frame_is_laid_out()

    def _frame_is_laid_out(self) -> bool:
        """Whether the host is managed by pack/grid/place with usable size."""
        try:
            if not self._frame.winfo_exists():
                return False
            if not self._frame.winfo_manager():
                return False
        except tk.TclError:
            return False
        return self._bounds_size() is not None

    def _maybe_fire_ready(self) -> None:
        if self._destroyed or self._webview is None:
            return
        if not self._layout_ready():
            return
        if self._ready_delivered or self._ready_pending:
            return
        self._ready_pending = True
        self._fire_ready()

    def _fire_ready(self) -> None:
        def _deliver_ready() -> None:
            if self._destroyed:
                self._ready_pending = False
                return
            # Defer bind handlers until create/bounds/poll paths return. event_generate
            # from idle is synchronous for bindings but no longer re-enters _try_create.
            self._frame.event_generate("<<WebViewReady>>")
            self._ready_delivered = True
            self._ready_pending = False
            callbacks = self._ready_callbacks
            self._ready_callbacks = []
            for callback in callbacks:
                self._invoke_callback(callback)
            if self._focus_when_ready:
                self._focus_when_ready = False
                if self._webview is not None:
                    try:
                        self.focus()
                    except Exception:
                        traceback.print_exc()

        self._track_after(self._frame.after_idle(_deliver_ready))

    def _needs_event_poll(self) -> bool:
        return any(
            (
                self._ipc_handler is not None,
                self._on_navigation is not None,
                self._on_new_window is not None,
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

    def _invoke_navigation_handler(self, url: str) -> bool:
        handler = self._on_navigation
        if handler is None:
            return True
        try:
            result = handler(url)
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

    def _native_navigation(self, url: str) -> bool:
        if self._on_navigation is None:
            return True
        return self._dispatch_sync_hook(
            lambda: self._invoke_navigation_handler(url),
            default=False,
        )

    def _native_title_changed(self, title: str) -> None:
        native = self._webview
        if native is None or self._on_title_changed is None:
            return
        native.set_title_listening(True)
        native._enqueue_title_event(title)
        self._ensure_event_poll()

    def _invoke_new_window_handler(self, url: str) -> NewWindowResponse:
        handler = self._on_new_window
        if handler is None:
            return NewWindowResponse.Allow
        try:
            result = handler(url)
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

    def _native_new_window(self, url: str) -> NewWindowResponse:
        if self._on_new_window is None:
            return NewWindowResponse.Allow
        return self._dispatch_sync_hook(
            lambda: self._invoke_new_window_handler(url),
            default=NewWindowResponse.Deny,
        )

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

    def _ensure_tk_wakeup_pipe(self) -> None:
        toplevel = self._frame.winfo_toplevel()
        if sys.platform == "darwin":
            write_fd = getattr(toplevel, "_tkwry_mac_wake_write_fd", None)
        else:
            write_fd = getattr(toplevel, "_tkwry_wake_write_fd", None)
            if write_fd is None:
                read_fd, write_fd = os.pipe()
                setattr(toplevel, "_tkwry_wake_read_fd", read_fd)
                setattr(toplevel, "_tkwry_wake_write_fd", write_fd)
                setattr(toplevel, "_tkwry_wake_pipe_users", 0)
            setattr(
                toplevel,
                "_tkwry_wake_pipe_users",
                getattr(toplevel, "_tkwry_wake_pipe_users", 0) + 1,
            )
            _ensure_tk_wakeup_fileevent(toplevel)
            _register_sync_hook_webview(toplevel, self)
        self._tk_wakeup_write_fd = write_fd
        native = self._webview
        if native is not None and write_fd is not None:
            native.set_mac_wakeup_write_fd(write_fd)

    def _wake_tk_for_sync_hook(self) -> None:
        write_fd = self._tk_wakeup_write_fd
        if write_fd is None:
            return
        try:
            os.write(write_fd, b"\x01")
        except OSError:
            pass

    def _schedule_sync_hook_drain(self) -> None:
        """Ask the Tk thread to drain Python-side sync hooks."""
        self._wake_tk_for_sync_hook()
        if threading.get_ident() != self._tk_thread_id:
            return
        try:
            self._frame.after(0, self._drain_sync_hooks)
        except (tk.TclError, RuntimeError):
            pass

    def _dispatch_sync_hook(self, invoke: Callable[[], _T], default: _T) -> _T:
        if threading.get_ident() == self._tk_thread_id:
            return self._run_sync_hook_invoke(invoke, default)

        done = threading.Event()
        result: list[object] = [default]
        cancelled = [False]
        started = [False]
        handler_started_at = [0.0]
        self._sync_hook_queue.put(
            (
                invoke,
                result,
                default,
                done,
                cancelled,
                started,
                handler_started_at,
            )
        )
        self._ensure_event_poll()
        self._schedule_sync_hook_drain()
        enqueued_at = time.monotonic()
        deadline = enqueued_at + _SYNC_HOOK_TIMEOUT_S
        absolute_deadline = enqueued_at + _SYNC_HOOK_MAX_WAIT_S
        while not done.is_set():
            if not started[0]:
                remaining = min(deadline, absolute_deadline) - time.monotonic()
                if remaining <= 0:
                    cancelled[0] = True
                    print(
                        f"tkwry: sync hook timed out after {_SYNC_HOOK_TIMEOUT_S:g}s",
                        file=sys.stderr,
                    )
                    return default
                done.wait(timeout=min(0.05, remaining))
            else:
                handler_deadline = handler_started_at[0] + _SYNC_HOOK_HANDLER_TIMEOUT_S
                remaining = min(handler_deadline, absolute_deadline) - time.monotonic()
                if remaining <= 0:
                    cancelled[0] = True
                    if time.monotonic() >= absolute_deadline:
                        print(
                            "tkwry: sync hook timed out after "
                            f"{_SYNC_HOOK_MAX_WAIT_S:g}s total",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            "tkwry: sync hook handler timed out after "
                            f"{_SYNC_HOOK_HANDLER_TIMEOUT_S:g}s",
                            file=sys.stderr,
                        )
                    return default
                done.wait(timeout=min(0.05, remaining))
            # Wake the Tk thread; native/Python drains run on the owner thread only.
            self._schedule_sync_hook_drain()
        return cast(_T, result[0])

    def _run_sync_hook_invoke(self, invoke: Callable[[], _T], default: _T) -> _T:
        self._sync_hook_depth += 1
        try:
            return invoke()
        except Exception:
            traceback.print_exc()
            return default
        finally:
            self._sync_hook_depth -= 1

    def _drain_sync_hooks(self) -> None:
        while True:
            try:
                (
                    invoke,
                    result,
                    default,
                    done,
                    cancelled,
                    started,
                    handler_started_at,
                ) = self._sync_hook_queue.get_nowait()
            except queue.Empty:
                break
            if cancelled[0] or self._destroyed:
                result[0] = default
            else:
                started[0] = True
                handler_started_at[0] = time.monotonic()
                result[0] = self._run_sync_hook_invoke(invoke, default)
            done.set()

    def _abort_sync_hooks(self) -> None:
        while True:
            try:
                (
                    _invoke,
                    result,
                    default,
                    done,
                    cancelled,
                    _started,
                    _handler_started_at,
                ) = self._sync_hook_queue.get_nowait()
            except queue.Empty:
                break
            cancelled[0] = True
            result[0] = default
            done.set()

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

    def _deliver_async_event_queues(self) -> None:
        """Drain native async queues into Python callbacks."""
        native = self._webview
        if native is not None:
            native.drain_sync_hooks()
        self._drain_sync_hooks()
        if self._ipc_handler is not None:
            self._deliver_ipc_messages()
        self._deliver_page_load_events()
        if self._on_title_changed is not None:
            self._deliver_title_events()
        if self._drag_drop_handler is not None:
            self._deliver_drag_drop_events()

    def _service_linux_events(self, *, gtk_rounds: int = 32, passes: int = 1) -> None:
        if sys.platform != "linux" or self._destroyed:
            return
        from tkwry._linux import pump_gtk_events

        for _ in range(passes):
            pump_gtk_events(bursts=gtk_rounds)
            self._deliver_async_event_queues()

    def _schedule_post_navigation_drain(self) -> None:
        """Queue GTK/WebKit drain on Tk idle (avoids nested pump deadlocks)."""
        if self._destroyed or sys.platform != "linux":
            return
        if self._post_nav_drain_scheduled:
            return
        self._post_nav_drain_scheduled = True

        def _drain() -> None:
            self._post_nav_drain_scheduled = False
            if self._destroyed:
                return
            self._drain_after_navigation()

        self._track_after(self._frame.after_idle(_drain))

    def _finish_navigation(self) -> None:
        """Pump GTK and deliver async queues after a navigation."""
        if self._destroyed:
            return
        if sys.platform == "linux":
            if self._in_poll_events:
                self._schedule_post_navigation_drain()
            else:
                self._drain_after_navigation()
        else:
            self._service_linux_events()

    def _drain_after_navigation(self) -> None:
        """Bounded GTK pump + queue delivery after navigation."""
        if sys.platform != "linux" or self._destroyed:
            return
        from tkwry._linux import pump_gtk_events

        for _ in range(4):
            pump_gtk_events()
            self._deliver_async_event_queues()

    def _register_pending_eval(
        self,
        callback: EvalCallback,
        on_error: EvalErrorHandler | None,
    ) -> int:
        token = self._eval_token_seq
        self._eval_token_seq += 1
        self._pending_eval_tokens[token] = (
            time.monotonic() + _EVAL_CALLBACK_TIMEOUT_S,
            callback,
            on_error,
        )
        self._pending_eval_callbacks += 1
        return token

    def _release_pending_eval(self, token: int) -> None:
        if token not in self._pending_eval_tokens:
            return
        del self._pending_eval_tokens[token]
        self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)

    def _drop_native_eval_wait_for_py_token(self, py_token: int) -> None:
        for native_token, wait in list(self._native_eval_wait.items()):
            if wait[1] == py_token:
                del self._native_eval_wait[native_token]

    def _expire_pending_evals(self) -> None:
        if not self._pending_eval_tokens:
            return
        now = time.monotonic()
        for token, (deadline, callback, on_error) in list(
            self._pending_eval_tokens.items()
        ):
            if now >= deadline:
                self._release_pending_eval(token)
                self._drop_native_eval_wait_for_py_token(token)
                self._bump_queue_drop(_QUEUE_DROP_EVAL)
                exc = TimeoutError(
                    f"eval_js_with_callback timed out after "
                    f"{_EVAL_CALLBACK_TIMEOUT_S:g}s"
                )
                if on_error is not None:
                    self._invoke_callback(on_error, exc)
                else:
                    print(f"tkwry: {exc}", file=sys.stderr)

    def _ensure_event_poll(self) -> None:
        if self._event_poll_active or self._destroyed:
            return
        self._event_poll_active = True
        self._track_after(self._frame.after(1, self._poll_events))

    def _drain_native_eval_callbacks(self) -> None:
        native = self._webview
        if native is None:
            return
        for native_token, _callback, result in native.drain_eval_callbacks():
            wait = self._native_eval_wait.pop(native_token, None)
            if wait is None:
                continue
            wait_epoch, py_token, expected_cb, on_error = wait
            if py_token not in self._pending_eval_tokens:
                continue
            self._release_pending_eval(py_token)
            if wait_epoch != self._eval_epoch:
                continue
            if result is None:
                self._bump_queue_drop(_QUEUE_DROP_EVAL)
                if on_error is not None:
                    self._invoke_callback(
                        on_error,
                        RuntimeError("eval result dropped (pending queue full)"),
                    )
                continue
            self._invoke_callback(expected_cb, result)

    def _poll_events(self) -> None:
        self._in_poll_events = True
        try:
            self._poll_events_impl()
        finally:
            self._in_poll_events = False

    def _poll_events_impl(self) -> None:
        try:
            _drain_pending_destroy_webviews(self._toplevel)
        except tk.TclError:
            return
        self._finish_native_teardown()
        if self._destroyed:
            if self._native_teardown_pending is not None:
                self._track_after(self._frame.after(1, self._poll_events))
            else:
                self._event_poll_active = False
            return
        if sys.platform == "linux":
            _pump_toplevel_wakeup_pipe(self._toplevel)
            from tkwry._linux import pump_gtk_events

            pump_gtk_events()
        elif sys.platform == "darwin":
            _mac_service_wakeup(self._toplevel)
        else:
            _pump_toplevel_wakeup_pipe(self._toplevel)

        native = self._webview
        if native is not None:
            native.drain_sync_hooks()
        self._drain_sync_hooks()

        handler = self._ipc_handler
        if handler is not None:
            self._deliver_ipc_messages()

        self._deliver_page_load_events()

        if self._on_title_changed is not None:
            self._deliver_title_events()

        if self._drag_drop_handler is not None:
            self._deliver_drag_drop_events()

        self._expire_pending_evals()
        self._drain_native_eval_callbacks()

        if self._should_keep_polling():
            delay = 1 if sys.platform == "linux" else 10
            self._track_after(self._frame.after(delay, self._poll_events))
        else:
            # Clear before re-check so a concurrent ensure_event_poll can re-arm.
            self._event_poll_active = False
            if self._should_keep_polling():
                self._ensure_event_poll()

    def _should_keep_polling(self) -> bool:
        if self._native_teardown_pending is not None:
            return True
        if self._needs_event_poll():
            return True
        return self._pending_eval_callbacks > 0 or bool(self._native_eval_wait)

    def _try_create(self) -> None:
        if (
            self._destroyed
            or self._webview is not None
            or self._creation_error is not None
        ):
            return

        size = self._creation_size()
        if size is None:
            return
        width, height = size

        url = self._pending_url
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
        kwargs["page_load_listening"] = self._on_page_load is not None
        kwargs["ipc_listening"] = self._ipc_handler is not None
        kwargs["title_listening"] = self._on_title_changed is not None
        kwargs["drag_drop_listening"] = self._drag_drop_handler is not None

        if sys.platform == "linux":
            self._attach_gtk_pump_for_native()
            from tkwry._linux import pump_gtk_events

            pump_gtk_events(bursts=20)
            self._ensure_tk_wakeup_pipe()

        try:
            self._webview = NativeWebView(
                self._embed.handle,
                owner_thread=self._tk_thread_id,
                **kwargs,
            )
        except Exception as exc:
            traceback.print_exc()
            self._create_attempt += 1
            if self._create_attempt >= _CREATE_MAX_ATTEMPTS:
                self._creation_error = exc
                print(
                    f"tkwry: failed to create native WebView after "
                    f"{_CREATE_MAX_ATTEMPTS} attempts; giving up",
                    file=sys.stderr,
                )
                return
            delay = min(5000, 50 * (2 ** min(self._create_attempt - 1, 6)))
            self._schedule_try_create(delay_ms=delay)
            return
        self._create_attempt = 0
        if sys.platform != "darwin":
            self._ensure_tk_wakeup_pipe()
        self._sync_async_listening()
        self._ensure_gtk_pump_attached()
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
                for _ in range(3):
                    self._service_linux_events(gtk_rounds=32, passes=2)
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
            if self._frame.winfo_width() <= 1 or self._frame.winfo_height() <= 1:
                return False
            # Xvfb headless: winfo_viewable() stays False while geometry is valid.
            if sys.platform == "linux":
                return True
            return bool(self._frame.winfo_viewable())
        except tk.TclError:
            return False

    def _bump_initial_load_attempt(self) -> None:
        self._initial_load_attempt += 1
        max_attempts = self._initial_load_attempts()
        if self._initial_load_attempt >= max_attempts:
            print(
                "tkwry: initial load failed after "
                f"{max_attempts} attempt(s); will retry",
                file=sys.stderr,
            )
            self._initial_load_attempt = 0

    def _schedule_flush_load(self, *, delay_ms: int | None = None) -> None:
        if self._flush_load_scheduled:
            return
        self._flush_load_scheduled = True
        if delay_ms is None:
            self._track_after(self._frame.after_idle(self._flush_load))
        else:
            self._track_after(self._frame.after(delay_ms, self._flush_load))

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
        if sys.platform == "linux":
            if kind == "url":
                self._pending_load = ("url", payload)
            else:
                self._pending_load = ("html", payload)
            self._dispatch_pending_load()
            if self._pending_load is None:
                self._initial_load = None
            return
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
        self._finish_navigation()
        if self._on_page_load is not None:
            self._ensure_event_poll()
        self._initial_load = None

    def _flush_load(self) -> None:
        self._flush_load_scheduled = False
        if self._destroyed or self._webview is None or self._pending_load is None:
            return
        kind, payload = self._pending_load
        if sys.platform == "linux":
            self._sync_bounds()
        try:
            if kind == "url":
                self._webview.load_url(payload)
            else:
                self._webview.load_html(payload)
        except Exception:
            traceback.print_exc()
            self._flush_load_attempt += 1
            if self._destroyed or self._pending_load is None:
                return
            delay_ms = min(
                _FLUSH_LOAD_RETRY_MAX_MS,
                _FLUSH_LOAD_RETRY_BASE_MS * (2 ** min(self._flush_load_attempt - 1, 4)),
            )
            if self._flush_load_attempt >= _FLUSH_LOAD_MAX_ATTEMPTS:
                print(
                    "tkwry: load failed after "
                    f"{self._flush_load_attempt} attempt(s); retrying in "
                    f"{delay_ms}ms",
                    file=sys.stderr,
                )
                self._flush_load_attempt = 0
            self._schedule_flush_load(delay_ms=delay_ms)
            return
        self._pending_load = None
        self._flush_load_attempt = 0
        self._initial_load = None
        self._sync_bounds()
        self._finish_navigation()
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
            if self._bounds_size() is None:
                return False
            # Xvfb headless: winfo_viewable() stays False while geometry is valid.
            if sys.platform == "linux":
                return True
            if not self._frame.winfo_viewable():
                return False
            return True
        except tk.TclError:
            return False

    def _schedule_bounds_sync(self) -> None:
        if self._destroyed or self._bounds_sync_scheduled:
            return
        self._bounds_sync_scheduled = True
        try:
            self._frame.update_idletasks()
            self._track_after(self._frame.after_idle(self._deferred_sync_bounds))
        except tk.TclError:
            self._bounds_sync_scheduled = False

    def _deferred_sync_bounds(self) -> None:
        self._bounds_sync_scheduled = False
        self._sync_bounds_and_stacking()
        self._maybe_fire_ready()

    def _sync_bounds_and_stacking(self) -> bool:
        synced = self._sync_bounds()
        if synced:
            self._schedule_stacking_sync()
        return synced

    def _schedule_stacking_sync(self) -> None:
        if sys.platform != "win32" or self._webview is None or self._destroyed:
            return
        if self._stacking_sync_scheduled:
            return
        self._stacking_sync_scheduled = True
        try:
            self._track_after(self._frame.after_idle(self._deferred_sync_stacking))
        except tk.TclError:
            self._stacking_sync_scheduled = False

    def _deferred_sync_stacking(self) -> None:
        self._stacking_sync_scheduled = False
        self._sync_tk_stacking_order()

    def _sync_tk_stacking_order(self) -> None:
        if sys.platform != "win32" or self._webview is None or self._destroyed:
            return
        try:
            from tkwry._win32 import raise_frame_webview

            parent = self._frame.master
            for child in parent.winfo_children():
                key = id(child)
                ref = _frame_webview_refs.get(key)
                if ref is None:
                    continue
                web = ref()
                if web is None or web._destroyed or web._webview is None:
                    continue
                raise_frame_webview(child.winfo_id())
        except tk.TclError:
            pass
        except Exception:
            traceback.print_exc()

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
        elif self._bounds_size() is not None:
            self._sync_bounds_and_stacking()
            self._maybe_fire_ready()
        else:
            self._schedule_bounds_sync()
            self._maybe_fire_ready()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._ensure_gtk_pump_attached()
        self._schedule_bounds_sync()
        self._maybe_fire_ready()
        self._track_after(self._frame.after_idle(self._run_initial_load))

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._schedule_bounds_sync()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self._frame:
            return
        self.destroy()
