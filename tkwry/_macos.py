"""macOS-specific focus management and wakeup pipe utilities."""

from __future__ import annotations

import os
import tkinter as tk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tkwry._core import WebView as NativeWebViewType
    from tkwry.webview import WebView

_MAC_TEXT_CLASSES = (
    "Entry",
    "TEntry",
    "Text",
    "Spinbox",
    "TSpinbox",
    "TCombobox",
)
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


def _mac_bind_root(widget: tk.Misc) -> tk.Misc:
    """Return the ``Tk`` instance used for global ``bind`` / ``unbind`` calls."""
    try:
        return widget._root()
    except (AttributeError, tk.TclError):
        pass
    current: tk.Misc = widget
    while True:
        try:
            if current.winfo_class() == "Tk":
                return current
            master = current.master
        except tk.TclError:
            break
        if master in (None, ""):
            break
        current = master
    return widget


def _unbind_mac_global(
    bind_root: tk.Misc,
    toplevel: tk.Misc,
    sequence: str,
    funcid: str | None,
) -> None:
    if funcid is None:
        return
    seen: set[int] = set()
    for candidate in (bind_root, toplevel):
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            candidate._unbind(("bind", "all", sequence), funcid)
            return
        except tk.TclError:
            pass
    try:
        fallback = toplevel._root()
    except (AttributeError, tk.TclError):
        return
    if id(fallback) in seen:
        return
    try:
        fallback._unbind(("bind", "all", sequence), funcid)
    except tk.TclError:
        pass


def _mac_web_input_active(toplevel: tk.Misc) -> bool:
    active = False
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None and native.mac_web_input_active():
            active = True
            break
    toplevel._tkwry_mac_web_input_active = active
    return active


def _sync_mac_web_input_cache(toplevel: tk.Misc) -> None:
    _mac_web_input_active(toplevel)


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
    """Drain Rust->Python unfocus signals on the Tk thread."""
    _mac_pump_wakeup_pipe(toplevel)
    drained = _drain_mac_tk_unfocus(toplevel)
    for web in _mac_webviews(toplevel):
        web._drain_sync_hooks()
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


def _mac_widget_mapped(event: tk.Event) -> None:
    """Tag text-like widgets when they (or a subtree) is mapped at runtime."""
    toplevel = event.widget.winfo_toplevel()
    if not getattr(toplevel, "_tkwry_mac_webviews", None):
        return
    _tag_mac_text_widgets(event.widget)


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
    bind_root = _mac_bind_root(toplevel)
    toplevel._tkwry_mac_key_guard = True
    toplevel._tkwry_mac_bind_root = bind_root
    toplevel.bind_class(_MAC_KEY_GUARD_TAG, "<KeyPress>", _mac_web_key_guard)
    # Store funcids so teardown can remove only our handlers (bind_all is global).
    toplevel._tkwry_mac_button1_bind_id = bind_root.bind_all(
        "<Button-1>", _mac_input_wakeup, add="+"
    )
    toplevel._tkwry_mac_map_bind_id = bind_root.bind_all(
        "<Map>", _mac_widget_mapped, add="+"
    )
    _prepend_mac_key_guard(toplevel)
    _tag_mac_text_widgets(toplevel)


def _teardown_mac_key_guard(toplevel: tk.Misc) -> None:
    if not getattr(toplevel, "_tkwry_mac_key_guard", False):
        return
    bind_root = getattr(toplevel, "_tkwry_mac_bind_root", None) or _mac_bind_root(
        toplevel
    )
    for sequence, attr in (
        ("<Button-1>", "_tkwry_mac_button1_bind_id"),
        ("<Map>", "_tkwry_mac_map_bind_id"),
    ):
        funcid = getattr(toplevel, attr, None)
        _unbind_mac_global(bind_root, toplevel, sequence, funcid)
        if hasattr(toplevel, attr):
            delattr(toplevel, attr)
    try:
        toplevel.unbind_class(_MAC_KEY_GUARD_TAG, "<KeyPress>")
    except tk.TclError:
        pass
    toplevel._tkwry_mac_key_guard = False
    if hasattr(toplevel, "_tkwry_mac_bind_root"):
        delattr(toplevel, "_tkwry_mac_bind_root")


def _set_mac_webviews_input_active(
    toplevel: tk.Misc, active_web: WebView | None
) -> None:
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None:
            native.set_mac_web_input_active(web is active_web)
    _sync_mac_web_input_cache(toplevel)


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
            _teardown_mac_key_guard(toplevel)
            for attr in ("_tkwry_mac_webviews", "_tkwry_mac_web_input_active"):
                if hasattr(toplevel, attr):
                    delattr(toplevel, attr)
