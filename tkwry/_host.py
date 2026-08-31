"""Frame-host ownership, Tk wakeup pipes, and sync-hook drainage.

Kept separate from :mod:`tkwry.webview` so the widget class file stays focused
on the WebView lifecycle. Symbols are re-exported from ``webview`` for
existing internal imports / tests.
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
import tkinter as tk
import traceback
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tkwry.webview import WebView

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


def _configure_wakeup_write_fd(write_fd: int) -> None:
    """Mark the wakeup pipe write end non-blocking (D26/D27)."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(write_fd)
        if handle == -1:
            return
        pipe_nowait = wintypes.DWORD(0x00000001)
        ctypes.windll.kernel32.SetNamedPipeHandleState(
            wintypes.HANDLE(handle),
            ctypes.byref(pipe_nowait),
            None,
            None,
        )
        return
    import fcntl

    flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
    fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _open_wakeup_pipe() -> tuple[int, int]:
    """Create a wakeup pipe with a non-blocking write end."""
    read_fd, write_fd = os.pipe()
    _configure_wakeup_write_fd(write_fd)
    return read_fd, write_fd


def _toplevel_wakeup_read_fd(toplevel: tk.Misc) -> int | None:
    if sys.platform == "darwin":
        return getattr(toplevel, "_tkwry_mac_wake_read_fd", None)
    return getattr(toplevel, "_tkwry_wake_read_fd", None)


def _toplevel_wakeup_write_fd(toplevel: tk.Misc) -> int | None:
    if sys.platform == "darwin":
        return getattr(toplevel, "_tkwry_mac_wake_write_fd", None)
    return getattr(toplevel, "_tkwry_wake_write_fd", None)


# When Tk has no createfilehandler (typical Windows), poll the shared wakeup
# pipe on an after timer — same role as macOS idle pump for D21 delivery.
_WAKE_AFTER_POLL_MS = 16


def _wakeup_read_fd_readable(read_fd: int) -> bool:
    """Return whether *read_fd* has buffered wakeup bytes (non-blocking)."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(read_fd)
        avail = wintypes.DWORD(0)
        if not ctypes.windll.kernel32.PeekNamedPipe(
            handle, None, 0, None, ctypes.byref(avail), None
        ):
            return False
        return avail.value > 0
    try:
        import select

        return bool(select.select([read_fd], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def _drain_wakeup_read_fd(read_fd: int) -> bool:
    """Drain wakeup bytes from *read_fd*; return True if any were read."""
    read_any = False
    try:
        while _wakeup_read_fd_readable(read_fd):
            chunk = os.read(read_fd, 64)
            if not chunk:
                break
            read_any = True
    except OSError:
        pass
    return read_any


def _pump_toplevel_wakeup_pipe(toplevel: tk.Misc) -> None:
    read_fd = _toplevel_wakeup_read_fd(toplevel)
    if read_fd is None:
        return
    _drain_wakeup_read_fd(read_fd)


def _pump_shared_wake_read_fd(toplevel: tk.Misc) -> bool:
    """Pump the Win/Linux shared wakeup pipe (``_tkwry_wake_read_fd`` only)."""
    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is None:
        return False
    return _drain_wakeup_read_fd(read_fd)


def _wakeup_pipe_readable(toplevel: tk.Misc) -> bool:
    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is None:
        return False
    return _wakeup_read_fd_readable(read_fd)


def _service_toplevel_wakeup(toplevel: tk.Misc) -> None:
    """Pump the shared pipe and drain sync hooks / wakeup-backed async queues."""
    _pump_shared_wake_read_fd(toplevel)
    _drain_toplevel_sync_hooks(toplevel)


def _run_pending_webview_destroy(web: WebView) -> None:
    """Run a queued ``__del__`` destroy on the Tk thread or via emergency teardown.

    Tk thread: cancel deferred callbacks then :meth:`WebView.destroy`.
    Any other thread: :meth:`WebView._teardown_native_if_alive` so eval/ready
    generation stays aligned. Never call bare ``_force_native_teardown`` here —
    that path is native-only (teardown poll timeout).
    """
    if web._destroyed:
        return
    if threading.get_ident() == web._tk_thread_id:
        try:
            web._cancel_deferred_callbacks()
            web.destroy()
        except Exception:
            traceback.print_exc()
            if not web._destroyed:
                try:
                    web._teardown_native_if_alive()
                except Exception:
                    traceback.print_exc()
        return
    try:
        web._teardown_native_if_alive()
    except Exception:
        traceback.print_exc()


def _drain_toplevel_sync_hooks(toplevel: tk.Misc) -> None:
    """Drain sync hooks and wakeup-backed async queues for WebViews on *toplevel*.

    The pipe wakeup is shared: navigation/new-window/download *start* sync hooks
    and download-*complete* async events all write it. Complete events must be
    delivered here so ``last_download`` / Tk events work without
    ``on_download_complete`` (and without an idle ``_webview is not None`` poll).
    """
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
            web._wake_async_events()
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
        _run_pending_webview_destroy(web)
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
            _run_pending_webview_destroy(web)
    _atexit_destroy_toplevels[:] = live


def _stop_wakeup_after_poll(toplevel: tk.Misc) -> None:
    setattr(toplevel, "_tkwry_wake_after_poll", False)
    if hasattr(toplevel, "_tkwry_wake_fileevent"):
        delattr(toplevel, "_tkwry_wake_fileevent")


def _wakeup_after_poll_tick(toplevel: tk.Misc) -> None:
    """``after`` fallback when ``createfilehandler`` is unavailable (Windows)."""
    if getattr(toplevel, "_tkwry_wake_read_fd", None) is None:
        _stop_wakeup_after_poll(toplevel)
        return
    if not getattr(toplevel, "_tkwry_wake_pipe_users", 0):
        _stop_wakeup_after_poll(toplevel)
        return
    try:
        if not toplevel.winfo_exists():
            _stop_wakeup_after_poll(toplevel)
            return
    except tk.TclError:
        _stop_wakeup_after_poll(toplevel)
        return

    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is not None and _drain_wakeup_read_fd(read_fd):
        _drain_toplevel_sync_hooks(toplevel)

    try:
        toplevel.after(_WAKE_AFTER_POLL_MS, _wakeup_after_poll_tick, toplevel)
    except tk.TclError:
        _stop_wakeup_after_poll(toplevel)


def _ensure_wakeup_after_poll(toplevel: tk.Misc) -> None:
    """Arm a light ``after`` poll for the shared wakeup pipe (D23)."""
    if getattr(toplevel, "_tkwry_wake_after_poll", False):
        return
    setattr(toplevel, "_tkwry_wake_after_poll", True)
    setattr(toplevel, "_tkwry_wake_fileevent", True)
    try:
        toplevel.after(0, _wakeup_after_poll_tick, toplevel)
    except tk.TclError:
        _stop_wakeup_after_poll(toplevel)


def _ensure_tk_wakeup_fileevent(toplevel: tk.Misc) -> None:
    """Register a Tcl readable handler so sync hooks drain without polling delay.

    Windows (and any Tk without ``createfilehandler``) uses
    :func:`_ensure_wakeup_after_poll` instead — required for D21 handler-less
    download-complete delivery without an idle ``_webview is not None`` latch.
    """
    if sys.platform == "darwin" or getattr(toplevel, "_tkwry_wake_fileevent", False):
        return
    read_fd = getattr(toplevel, "_tkwry_wake_read_fd", None)
    if read_fd is None:
        return

    def _on_wake(_fd: int, _mask: int) -> None:
        _service_toplevel_wakeup(toplevel)

    try:
        create_handler = getattr(toplevel, "createfilehandler", None)
        if create_handler is None:
            _ensure_wakeup_after_poll(toplevel)
            return
        create_handler(read_fd, tk.READABLE, _on_wake)
        setattr(toplevel, "_tkwry_wake_fileevent", True)
    except (tk.TclError, OSError, ValueError):
        _ensure_wakeup_after_poll(toplevel)


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
        "_tkwry_wake_after_poll",
        "_tkwry_sync_hook_webviews",
    ):
        if hasattr(toplevel, attr):
            delattr(toplevel, attr)
