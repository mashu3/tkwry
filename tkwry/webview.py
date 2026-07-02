"""Tkinter WebView widget."""

from __future__ import annotations

import os
import queue
import sys
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from tkwry._core import (
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
)
from tkwry._core import (
    WebView as NativeWebView,
)
from tkwry._parent import tk_embed_origin, tk_embed_parent
from tkwry._runtime import GtkPump
from tkwry._url import _normalize_url, _validate_url
from tkwry.exceptions import WebViewDestroyedError, WebViewNotReadyError

if TYPE_CHECKING:
    from tkwry._core import WebView as NativeWebViewType

NavigationHandler = Callable[[str], bool]
PageLoadHandler = Callable[[PageLoadEvent, str], None]
TitleChangedHandler = Callable[[str], None]
NewWindowHandler = Callable[[str], NewWindowResponse]
DragDropHandler = Callable[[DragDropEvent, list[str], tuple[int, int]], bool]
EvalCallback = Callable[[str], None]
_LoadKind = str  # "url" | "html"

_MAC_TEXT_CLASSES = ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox")
_MAC_KEY_GUARD_TAG = "TkwryMacWebKeyGuard"


def _widget_accepts_tk_keys(widget: tk.Misc) -> bool:
    try:
        cls = widget.winfo_class()
    except tk.TclError:
        return False
    return cls in _MAC_TEXT_CLASSES


def _release_tk_keyboard_focus(toplevel: tk.Misc) -> None:
    try:
        focused = toplevel.focus_get()
    except tk.TclError:
        return
    if focused is None or not _widget_accepts_tk_keys(focused):
        return
    try:
        toplevel.focus_force()
    except tk.TclError:
        pass


def _mac_webviews(toplevel: tk.Misc) -> list[WebView]:
    registered = getattr(toplevel, "_tkwry_mac_webviews", None) or []
    return [w for w in registered if not w.destroyed and w.native is not None]


def _mac_web_input_active(toplevel: tk.Misc) -> bool:
    return bool(getattr(toplevel, "_tkwry_mac_web_input_active", False))


def _sync_mac_web_input_cache(toplevel: tk.Misc) -> None:
    active = False
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None and native.mac_web_input_active():
            active = True
            break
    toplevel._tkwry_mac_web_input_active = active


def _drain_mac_tk_unfocus(toplevel: tk.Misc) -> bool:
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None and native.take_mac_tk_unfocus():
            _release_tk_keyboard_focus(toplevel)
            return True
    return False


def _mac_unfocus_pending(toplevel: tk.Misc) -> bool:
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None and native.mac_tk_unfocus_pending():
            return True
    return False


def _mac_pipe_readable(toplevel: tk.Misc) -> bool:
    read_fd = getattr(toplevel, "_tkwry_mac_wake_read_fd", None)
    if read_fd is None:
        return False
    try:
        import select

        return bool(select.select([read_fd], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def _mac_pump_wakeup_pipe(toplevel: tk.Misc) -> None:
    read_fd = getattr(toplevel, "_tkwry_mac_wake_read_fd", None)
    if read_fd is None:
        return
    try:
        import select

        while select.select([read_fd], [], [], 0)[0]:
            if not os.read(read_fd, 64):
                break
    except (OSError, ValueError):
        pass


def _mac_service_wakeup(toplevel: tk.Misc) -> bool:
    """Drain Rust→Python unfocus signals on the Tk thread."""
    _mac_pump_wakeup_pipe(toplevel)
    drained = _drain_mac_tk_unfocus(toplevel)
    _sync_mac_web_input_cache(toplevel)
    return drained


def _mac_pump_tick(toplevel: tk.Misc) -> None:
    if not _mac_webviews(toplevel):
        toplevel._tkwry_mac_pump_active = False
        return
    _mac_service_wakeup(toplevel)
    if _mac_unfocus_pending(toplevel) or _mac_pipe_readable(toplevel):
        delay = 0
    elif _mac_web_input_active(toplevel):
        delay = 16
    else:
        delay = 200
    toplevel.after(delay, _mac_pump_tick, toplevel)


def _ensure_mac_pump(toplevel: tk.Misc) -> None:
    if getattr(toplevel, "_tkwry_mac_pump_active", False):
        return
    toplevel._tkwry_mac_pump_active = True
    toplevel.after(0, _mac_pump_tick, toplevel)


def _mac_input_wakeup(event: tk.Event) -> None:
    """Drain Rust focus flags promptly when Tcl sees a click."""
    toplevel = event.widget.winfo_toplevel()
    if not getattr(toplevel, "_tkwry_mac_webviews", None):
        return
    _mac_service_wakeup(toplevel)
    pump_idle = not getattr(toplevel, "_tkwry_mac_pump_active", False)
    if pump_idle and _mac_webviews(toplevel):
        _ensure_mac_pump(toplevel)


def _mac_web_key_guard(event: tk.Event) -> str | None:
    toplevel = event.widget.winfo_toplevel()
    if _mac_web_input_active(toplevel):
        return "break"
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None and native.mac_web_input_active():
            toplevel._tkwry_mac_web_input_active = True
            if _mac_unfocus_pending(toplevel):
                toplevel.after(0, _mac_service_wakeup, toplevel)
            return "break"
    return None


def _ensure_mac_wakeup_pipe(toplevel: tk.Misc, native: NativeWebViewType) -> None:
    if getattr(toplevel, "_tkwry_mac_wake_read_fd", None) is not None:
        native.set_mac_wakeup_write_fd(toplevel._tkwry_mac_wake_write_fd)
        return

    read_fd, write_fd = os.pipe()
    toplevel._tkwry_mac_wake_read_fd = read_fd
    toplevel._tkwry_mac_wake_write_fd = write_fd
    native.set_mac_wakeup_write_fd(write_fd)


def _teardown_mac_wakeup_pipe(toplevel: tk.Misc) -> None:
    toplevel._tkwry_mac_pump_active = False
    read_fd = getattr(toplevel, "_tkwry_mac_wake_read_fd", None)
    if read_fd is None:
        return
    for fd in (read_fd, getattr(toplevel, "_tkwry_mac_wake_write_fd", None)):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    for attr in (
        "_tkwry_mac_wake_read_fd",
        "_tkwry_mac_wake_write_fd",
    ):
        if hasattr(toplevel, attr):
            delattr(toplevel, attr)


def _prepend_mac_key_guard(widget: tk.Misc) -> None:
    try:
        tags = widget.bindtags()
    except tk.TclError:
        return
    if tags and tags[0] == _MAC_KEY_GUARD_TAG:
        return
    filtered = tuple(tag for tag in tags if tag != _MAC_KEY_GUARD_TAG)
    widget.bindtags((_MAC_KEY_GUARD_TAG, *filtered))


def _tag_mac_text_widgets(root: tk.Misc) -> None:
    if _widget_accepts_tk_keys(root):
        _prepend_mac_key_guard(root)
    for child in root.winfo_children():
        _tag_mac_text_widgets(child)


def _ensure_mac_key_guard(toplevel: tk.Misc) -> None:
    if getattr(toplevel, "_tkwry_mac_key_guard", False):
        return
    toplevel._tkwry_mac_key_guard = True
    toplevel.bind_class(_MAC_KEY_GUARD_TAG, "<KeyPress>", _mac_web_key_guard)
    toplevel.bind_all("<Button-1>", _mac_input_wakeup, add="+")
    _prepend_mac_key_guard(toplevel)
    _tag_mac_text_widgets(toplevel)


def _set_mac_webviews_input_active(
    toplevel: tk.Misc, active_web: WebView | None
) -> None:
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None:
            native.set_mac_web_input_active(web is active_web)
    toplevel._tkwry_mac_web_input_active = active_web is not None


def _register_macos_webview(web: WebView) -> None:
    toplevel = web._frame.winfo_toplevel()
    views: list[WebView] | None = getattr(toplevel, "_tkwry_mac_webviews", None)
    if views is None:
        views = []
        toplevel._tkwry_mac_webviews = views
        toplevel._tkwry_mac_web_input_active = False
        _ensure_mac_key_guard(toplevel)
    views.append(web)


def _unregister_macos_webview(web: WebView) -> None:
    toplevel = web._frame.winfo_toplevel()
    views = getattr(toplevel, "_tkwry_mac_webviews", None)
    if views:
        try:
            views.remove(web)
        except ValueError:
            pass
        if not views:
            _teardown_mac_wakeup_pipe(toplevel)


class WebView:
    """Embed a system WebView (wry) inside an existing Tk ``Frame``.

    The host *frame* must be laid out with a real size (``pack`` / ``grid`` /
    ``place``) before the native webview is created. IPC, page-load, title,
    eval callbacks, and drag-and-drop handlers run on the **Tk main thread**
    via an internal queue.

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
    """

    def __init__(
        self,
        frame: tk.Frame,
        *,
        width: int = 800,
        height: int = 600,
        url: Optional[str] = None,
        html: Optional[str] = None,
        ipc_handler: Optional[Callable[[str], None]] = None,
        devtools: bool = False,
        background_color: Optional[tuple[int, int, int, int]] = None,
        user_agent: Optional[str] = None,
        initialization_script: Optional[str] = None,
        focused: bool = True,
        on_navigation: Optional[NavigationHandler] = None,
        on_page_load: Optional[PageLoadHandler] = None,
        on_title_changed: Optional[TitleChangedHandler] = None,
        on_new_window: Optional[NewWindowHandler] = None,
        drag_drop_handler: Optional[DragDropHandler] = None,
    ) -> None:
        self._frame = frame
        self._init_width = max(width, 1)
        self._init_height = max(height, 1)
        self._early_create = width != 800 or height != 600
        self._destroyed = False
        self._ready_callbacks: list[Callable[[], None]] = []
        self._create_pending = False
        self._embed = tk_embed_parent(frame)
        self._webview: Optional[NativeWebViewType] = None
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
        self._ipc_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._title_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._eval_result_queue: queue.SimpleQueue[tuple[EvalCallback, str]] = (
            queue.SimpleQueue()
        )
        self._drag_drop_queue: queue.SimpleQueue[
            tuple[DragDropEvent, list[str], tuple[int, int]]
        ] = queue.SimpleQueue()
        self._event_poll_active = False
        self._pending_url: Optional[str] = url
        self._pending_html: Optional[str] = html
        self._pending_load: Optional[tuple[_LoadKind, str]] = None
        self._flush_load_scheduled = False
        self._initial_load: Optional[tuple[_LoadKind, str]] = None
        self._initial_load_attempt = 0

        GtkPump.attach(frame)
        self._frame.bind("<Configure>", self._on_configure, add="+")
        self._frame.bind("<Map>", self._on_map, add="+")
        self._frame.bind("<Unmap>", self._on_unmap, add="+")
        self._frame.bind("<Destroy>", self._on_destroy, add="+")
        if sys.platform == "darwin":
            _register_macos_webview(self)
        if self._needs_event_poll():
            self._ensure_event_poll()
        if self._creation_size() is not None or self._early_create:
            self._schedule_try_create()

    def pack(self, **kwargs) -> None:
        self._frame.pack(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    def grid(self, **kwargs) -> None:
        self._frame.grid(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    def place(self, **kwargs) -> None:
        self._frame.place(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    @property
    def ready(self) -> bool:
        """``True`` once the native webview has been created and not destroyed."""
        return self._webview is not None and not self._destroyed

    @property
    def url(self) -> Optional[str]:
        """Current document URL, or the pending URL before creation."""
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is None:
            return self._pending_url
        return self._webview.url()

    @property
    def native(self) -> Optional[NativeWebViewType]:
        """Underlying :class:`tkwry._core.WebView`, or ``None`` if not created."""
        return self._webview

    @property
    def destroyed(self) -> bool:
        """``True`` after :meth:`destroy` or host-frame destruction."""
        return self._destroyed

    def bind(self, sequence: str, func: Callable, add: str | None = None) -> str:
        """Bind a Tk event on the host frame (e.g. ``\"<<WebViewReady>>\"``)."""
        if sequence == "<<WebViewReady>>" and self.ready:
            self._frame.after_idle(func)
        return self._frame.bind(sequence, func, add=add)

    def when_ready(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* on the Tk main thread once the native view exists."""
        if self._destroyed:
            return
        if self.ready:
            self._frame.after_idle(callback)
        else:
            self._ready_callbacks.append(callback)

    def wait_until_ready(self, timeout: float | None = 30.0) -> bool:
        """Pump the Tk loop until the native webview is created or *timeout* elapses."""
        if self.ready:
            return True
        if self._destroyed:
            return False
        root = self._frame.winfo_toplevel()
        deadline = time.monotonic() + timeout if timeout is not None else None
        gtk_pump = None
        if sys.platform == "linux":
            from tkwry._core import pump_events as gtk_pump

        while not self.ready and not self._destroyed:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            root.update_idletasks()
            root.update()
            if gtk_pump is not None:
                gtk_pump()
            root.after(10)
            root.update()
        return self.ready

    def destroy(self) -> None:
        """Hide and release the native webview without destroying the host frame."""
        if self._destroyed:
            return
        self._destroyed = True
        self._event_poll_active = False
        self._ready_callbacks.clear()
        if sys.platform == "darwin":
            _unregister_macos_webview(self)
        if self._webview is not None:
            self._webview.destroy()
            self._webview = None

    def load_url(self, url: str) -> None:
        """Navigate to *url* (``http``/``https`` only; scheme optional).

        Multiple rapid calls are coalesced (**last-wins**): only the final URL
        is loaded. Before the native view exists, the URL is stored and applied
        at creation (unless superseded by :meth:`load_html`).
        """
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        normalized = _normalize_url(url)
        _validate_url(normalized)
        if self._webview is None:
            self._pending_url = normalized
            self._pending_html = None
            return
        self._pending_load = ("url", normalized)
        self._schedule_flush_load()

    def load_html(self, html: str) -> None:
        """Load inline HTML.

        Like :meth:`load_url`, rapid calls are coalesced (**last-wins**).
        ``load_html`` supersedes any pending :meth:`load_url` call.
        """
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        if self._webview is None:
            self._pending_html = html
            self._pending_url = None
            return
        self._pending_load = ("html", html)
        self._schedule_flush_load()

    def reload(self) -> None:
        self._require_ready("reload")
        self._webview.reload()
        if self._on_page_load is not None:
            self._ensure_event_poll()
            self._service_linux_events()

    def eval_js(self, script: str) -> None:
        """Evaluate JavaScript without waiting for a result.

        The script is scheduled on the Tk idle loop (not synchronous). There is
        no return value; use :meth:`eval_js_with_callback` when you need the
        result. JavaScript errors are printed to stderr and do not propagate to
        the caller.
        """
        self._require_ready("eval_js")
        self._frame.after_idle(lambda: self._run_eval_js(script))

    def eval_js_with_callback(self, script: str, callback: EvalCallback) -> None:
        """Evaluate JavaScript and invoke *callback* with the result string.

        Asynchronous: *callback* runs on the **Tk main thread** after the script
        completes. The result is always a ``str`` (including JSON literals).
        """
        self._require_ready("eval_js_with_callback")
        self._ensure_event_poll()

        def _run() -> None:
            if self._destroyed or self._webview is None:
                return

            def deliver(result: str) -> None:
                self._eval_result_queue.put((callback, result))

            self._webview.eval_js_with_callback(script, deliver)

        self._frame.after_idle(_run)

    def focus(self) -> None:
        """Move keyboard focus to the WebView (``makeFirstResponder`` on macOS)."""
        self._require_ready("focus")
        self._webview.focus()
        if sys.platform == "darwin":
            toplevel = self._frame.winfo_toplevel()
            _set_mac_webviews_input_active(toplevel, self)
            _release_tk_keyboard_focus(toplevel)

    def focus_parent(self) -> None:
        """Return keyboard focus to the native parent view (macOS Tk coexistence)."""
        self._require_ready("focus_parent")
        self._webview.focus_parent()
        if sys.platform == "darwin":
            _set_mac_webviews_input_active(self._frame.winfo_toplevel(), None)

    def set_background_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        self._require_ready("set_background_color")
        self._webview.set_background_color(r, g, b, a)

    def open_devtools(self) -> None:
        self._require_ready("open_devtools")
        self._webview.open_devtools()

    def close_devtools(self) -> None:
        self._require_ready("close_devtools")
        self._webview.close_devtools()

    def is_devtools_open(self) -> bool:
        self._require_ready("is_devtools_open")
        return self._webview.is_devtools_open()

    def set_ipc_handler(self, handler: Optional[Callable[[str], None]]) -> None:
        self._ipc_handler = handler
        if self._webview is not None:
            if handler is not None:
                self._webview.set_ipc_handler(self._enqueue_ipc)
            else:
                self._webview.clear_ipc_handler()
        if handler is not None:
            self._ensure_event_poll()

    def set_on_navigation(self, handler: Optional[NavigationHandler]) -> None:
        self._on_navigation = handler
        if self._webview is not None and handler is not None:
            self._webview.set_on_navigation(self._native_navigation)

    def set_on_page_load(self, handler: Optional[PageLoadHandler]) -> None:
        self._on_page_load = handler
        if handler is not None:
            self._discard_page_load_backlog()
            self._ensure_event_poll()

    def sync_bounds(self) -> None:
        """Push the host frame's size and position to the native WebView.

        Called automatically on ``<Configure>``, ``<Map>``, and ``<Unmap>``.
        Call this manually after layout changes that do not emit Configure
        (e.g. custom geometry) so the WebView reflows — useful for centered
        images and responsive content.
        """
        if self._destroyed:
            raise WebViewDestroyedError("WebView.destroy() was called")
        self._sync_bounds()

    def set_on_title_changed(self, handler: Optional[TitleChangedHandler]) -> None:
        self._on_title_changed = handler
        if self._webview is not None and handler is not None:
            self._webview.set_on_title_changed(self._native_title_changed)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_new_window(self, handler: Optional[NewWindowHandler]) -> None:
        self._on_new_window = handler
        if self._webview is not None and handler is not None:
            self._webview.set_on_new_window(self._on_new_window)

    def set_drag_drop_handler(self, handler: Optional[DragDropHandler]) -> None:
        self._drag_drop_handler = handler
        if self._webview is not None and handler is not None:
            self._webview.set_drag_drop_handler(self._native_drag_drop)
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

    def _require_ready(self, method: str) -> None:
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )
        if self._webview is None:
            raise WebViewNotReadyError(
                f"WebView is not ready; call wait_until_ready() or bind to "
                f"<<WebViewReady>> before calling {method}()"
            )

    def _creation_size(self) -> tuple[int, int] | None:
        self._frame.update_idletasks()
        frame_w = self._frame.winfo_width()
        frame_h = self._frame.winfo_height()
        if frame_w > 1 and frame_h > 1:
            return frame_w, frame_h
        if self._early_create:
            return self._init_width, self._init_height
        return None

    def _fire_ready(self) -> None:
        callbacks = self._ready_callbacks
        self._ready_callbacks = []
        self._frame.event_generate("<<WebViewReady>>", when="tail")
        for callback in callbacks:
            self._frame.after_idle(callback)

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
    ) -> bool:
        # wry calls this from the WebKit thread during an active OS drag.
        # Never touch Tk here — queue and return immediately so the drop can finish.
        if event == DragDropEvent.Over:
            return True
        if self._drag_drop_handler is not None:
            self._drag_drop_queue.put((event, paths, position))
        return True

    def _native_navigation(self, url: str) -> bool:
        if self._on_navigation is None:
            return True
        return self._on_navigation(url)

    def _native_title_changed(self, title: str) -> None:
        self._title_queue.put(title)

    def _enqueue_ipc(self, message: str) -> None:
        self._ipc_queue.put(message)

    def _discard_page_load_backlog(self) -> None:
        native = self._webview
        if native is not None:
            native.drain_page_load_events()

    def _invoke_callback(self, callback: Callable[..., None], *args: object) -> None:
        try:
            callback(*args)
        except Exception:
            traceback.print_exc()

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
            while True:
                try:
                    message = self._ipc_queue.get_nowait()
                except queue.Empty:
                    break
                self._invoke_callback(handler, message)

        self._deliver_page_load_events()

        title_handler = self._on_title_changed
        if title_handler is not None:
            while True:
                try:
                    title = self._title_queue.get_nowait()
                except queue.Empty:
                    break
                self._invoke_callback(title_handler, title)

        drag_handler = self._drag_drop_handler
        if drag_handler is not None:
            while True:
                try:
                    event, paths, position = self._drag_drop_queue.get_nowait()
                except queue.Empty:
                    break
                self._invoke_callback(drag_handler, event, paths, position)

        while True:
            try:
                callback, result = self._eval_result_queue.get_nowait()
            except queue.Empty:
                break
            self._invoke_callback(callback, result)

        if self._should_keep_polling():
            delay = 1 if sys.platform == "linux" else 10
            self._frame.after(delay, self._poll_events)
        else:
            self._event_poll_active = False

    def _should_keep_polling(self) -> bool:
        if self._needs_event_poll():
            return True
        return not (
            self._eval_result_queue.empty()
            and self._title_queue.empty()
            and self._ipc_queue.empty()
            and self._drag_drop_queue.empty()
        )

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
        initial_load: Optional[tuple[_LoadKind, str]] = None
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
        if self._ipc_handler is not None:
            kwargs["ipc_handler"] = self._enqueue_ipc
        if self._on_navigation is not None:
            kwargs["on_navigation"] = self._native_navigation
        if self._on_title_changed is not None:
            kwargs["on_title_changed"] = self._native_title_changed
        if self._on_new_window is not None:
            kwargs["on_new_window"] = self._on_new_window
        if self._drag_drop_handler is not None:
            kwargs["drag_drop_handler"] = self._native_drag_drop

        if sys.platform == "linux":
            from tkwry._core import pump_events

            for _ in range(20):
                pump_events()

        self._webview = NativeWebView(self._embed.handle, **kwargs)
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
        self._fire_ready()

    def _run_eval_js(self, script: str) -> None:
        if self._destroyed or self._webview is None:
            return
        try:
            self._webview.eval_js(script)
        except Exception:
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

    def _schedule_initial_load(self) -> None:
        if self._initial_load is None:
            return
        toplevel = self._frame.winfo_toplevel()
        toplevel.after_idle(self._run_initial_load)
        if sys.platform == "darwin":
            # WKWebView may paint blank when navigation runs before compositing.
            toplevel.after(200, self._run_initial_load)
        elif sys.platform == "linux":
            # WebKitGTK needs extra GTK time in headless CI before URL settles.
            toplevel.after(150, self._run_initial_load)

    def _run_initial_load(self) -> None:
        load = self._initial_load
        if load is None or self._destroyed or self._webview is None:
            return
        if not self._frame_ready_for_initial_load():
            self._bump_initial_load_attempt()
            return
        self._sync_bounds()
        kind, payload = load
        try:
            if kind == "url":
                self._webview.load_url(payload)
            else:
                self._webview.load_html(payload)
        except Exception:
            traceback.print_exc()
            self._bump_initial_load_attempt()
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
        if kind == "url":
            self._webview.load_url(payload)
        else:
            self._webview.load_html(payload)
        self._sync_bounds()
        self._service_linux_events()
        if self._on_page_load is not None:
            self._ensure_event_poll()

    def _frame_should_show(self) -> bool:
        try:
            if not self._frame.winfo_exists():
                return False
            if sys.platform != "linux" and not self._frame.winfo_viewable():
                return False
            if self._frame.winfo_width() <= 1 or self._frame.winfo_height() <= 1:
                return False
            return True
        except tk.TclError:
            return False

    def _schedule_bounds_sync(self) -> None:
        if self._destroyed:
            return
        self._frame.update_idletasks()
        self._frame.after_idle(self._sync_bounds)

    def _sync_bounds(self) -> None:
        if self._webview is None:
            return
        if not self._frame_should_show():
            self._webview.set_visible(False)
            return
        self._frame.update_idletasks()
        width = max(self._frame.winfo_width(), 1)
        height = max(self._frame.winfo_height(), 1)
        x, y = tk_embed_origin(self._frame, root_relative=self._embed.root_relative)
        self._webview.set_bounds(x, y, width, height)
        self._webview.set_visible(True)

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        if self._webview is None:
            self._schedule_try_create()
        else:
            self._sync_bounds()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self._frame:
            self._frame.after_idle(self._sync_bounds)
            self._frame.after_idle(self._run_initial_load)

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self._frame:
            self._frame.after_idle(self._sync_bounds)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self._frame:
            return
        self.destroy()
