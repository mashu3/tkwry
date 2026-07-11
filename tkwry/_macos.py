"""macOS-specific focus management and wakeup pipe utilities."""

from __future__ import annotations

import os
import threading
import tkinter as tk
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tkwry._core import WebView as NativeWebViewType
    from tkwry.webview import WebView

_MAC_TEXT_CLASSES = frozenset(
    {
        "Entry",
        "TEntry",
        "Text",
        "Spinbox",
        "TSpinbox",
        "TCombobox",
        "Listbox",
        "Treeview",
        "TTreeview",
    }
)
_MAC_TEXT_CLASS_SUFFIXES = (
    "Entry",
    "Textbox",
    "Text",
    "Spinbox",
    "Combobox",
    "Edit",
)
_MAC_KEY_GUARD_TAG = "TkwryMacWebKeyGuard"


def _toplevel_alive(toplevel: tk.Misc) -> bool:
    try:
        return bool(toplevel.winfo_exists())
    except tk.TclError:
        return False


def _widget_takefocus_enabled(widget: tk.Misc) -> bool:
    try:
        takefocus = widget.cget("takefocus")
    except tk.TclError:
        return True
    return str(takefocus).lower() not in ("0", "false", "no")


def _widget_has_text_input_capability(widget: tk.Misc) -> bool:
    insert = getattr(widget, "insert", None)
    if not callable(insert):
        return False
    if not (
        callable(getattr(widget, "get", None))
        or callable(getattr(widget, "index", None))
    ):
        return False
    return _widget_takefocus_enabled(widget)


def _widget_accepts_tk_keys(widget: tk.Misc) -> bool:
    try:
        cls = widget.winfo_class()
    except tk.TclError:
        return False
    if cls in _MAC_TEXT_CLASSES:
        return True
    if cls.endswith(_MAC_TEXT_CLASS_SUFFIXES):
        return _widget_takefocus_enabled(widget)
    return _widget_has_text_input_capability(widget)


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
    alive: list[WebView] = []
    survivors: list[WebView | weakref.ReferenceType[WebView]] = []
    for entry in registered:
        web = entry() if isinstance(entry, weakref.ReferenceType) else entry
        if web is None:
            continue
        survivors.append(entry)
        if not web.destroyed and web.native is not None:
            alive.append(web)
    if survivors != registered:
        toplevel._tkwry_mac_webviews = survivors
    return alive


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


def _mac_after(toplevel: tk.Misc, delay: int, callback, *args) -> None:
    if not _toplevel_alive(toplevel):
        return
    try:
        toplevel.after(delay, callback, *args)
    except tk.TclError:
        pass


def _mac_pump_tick(toplevel: tk.Misc) -> None:
    if not _toplevel_alive(toplevel):
        return
    if not _mac_webviews(toplevel):
        toplevel._tkwry_mac_pump_active = False
        return
    _mac_service_wakeup(toplevel)
    if not _toplevel_alive(toplevel):
        return
    if _mac_unfocus_pending(toplevel) or _mac_pipe_readable(toplevel):
        delay = 1
    elif _mac_web_input_active(toplevel):
        delay = 16
    else:
        delay = 200
    _mac_after(toplevel, delay, _mac_pump_tick, toplevel)


def _ensure_mac_pump(toplevel: tk.Misc) -> None:
    if not _toplevel_alive(toplevel):
        return
    if getattr(toplevel, "_tkwry_mac_pump_active", False):
        return
    toplevel._tkwry_mac_pump_active = True
    _mac_after(toplevel, 0, _mac_pump_tick, toplevel)


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


def _mac_focus_in_handler(event: tk.Event) -> None:
    """Tag editable widgets on focus and drop Tcl focus when web input is active."""
    widget = event.widget
    toplevel = widget.winfo_toplevel()
    if not getattr(toplevel, "_tkwry_mac_webviews", None):
        return
    if not _widget_accepts_tk_keys(widget):
        return
    _prepend_mac_key_guard(widget)
    if _mac_web_input_active(toplevel):
        _release_tk_keyboard_focus(toplevel)
        _mac_after(toplevel, 1, _mac_service_wakeup, toplevel)


def _mac_web_key_guard(event: tk.Event) -> str | None:
    toplevel = event.widget.winfo_toplevel()
    if _mac_web_input_active(toplevel):
        if _mac_unfocus_pending(toplevel):
            _mac_after(toplevel, 1, _mac_service_wakeup, toplevel)
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
    toplevel._tkwry_mac_focusin_bind_id = bind_root.bind_all(
        "<FocusIn>", _mac_focus_in_handler, add="+"
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
        ("<FocusIn>", "_tkwry_mac_focusin_bind_id"),
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


def _mac_toplevel_destroy(event: tk.Event) -> None:
    widget = event.widget
    try:
        toplevel = widget.winfo_toplevel()
    except tk.TclError:
        return
    if getattr(toplevel, "_tkwry_mac_torn_down", False):
        return
    if widget is not toplevel:
        return
    _teardown_macos_toplevel(toplevel)


def _teardown_macos_toplevel(toplevel: tk.Misc) -> None:
    if getattr(toplevel, "_tkwry_mac_torn_down", False):
        return
    toplevel._tkwry_mac_torn_down = True
    _teardown_mac_wakeup_pipe(toplevel)
    _teardown_mac_key_guard(toplevel)
    for attr in (
        "_tkwry_mac_webviews",
        "_tkwry_mac_web_input_active",
    ):
        if hasattr(toplevel, attr):
            delattr(toplevel, attr)


def _set_mac_webviews_input_active(
    toplevel: tk.Misc, active_web: WebView | None
) -> None:
    for web in _mac_webviews(toplevel):
        native = web.native
        if native is not None:
            native.set_mac_web_input_active(web is active_web)
    _sync_mac_web_input_cache(toplevel)


_TABBING_PATCH_ATTR = "_tkwry_tabbing_patched"
_tabbing_disable_done = False


def install_automatic_window_tabbing_disable() -> None:
    """Disable macOS automatic window tabbing on the AppKit main thread.

    Called at package import. When import happens off the main thread (before
    ``Tk()``), ``tk.Tk.__init__`` is patched so the process-wide opt-out still
    runs on the main thread before the first root window is created.
    """
    global _tabbing_disable_done

    def _disable_once() -> None:
        global _tabbing_disable_done
        if _tabbing_disable_done:
            return
        _tabbing_disable_done = True
        from tkwry._core import disable_macos_automatic_window_tabbing

        disable_macos_automatic_window_tabbing()

    if threading.current_thread() is threading.main_thread():
        _disable_once()
        return

    if getattr(tk.Tk.__init__, _TABBING_PATCH_ATTR, False):
        return

    orig_init = tk.Tk.__init__

    def _tk_init_with_tabbing_disabled(self, *args, **kwargs):
        _disable_once()
        return orig_init(self, *args, **kwargs)

    setattr(_tk_init_with_tabbing_disabled, _TABBING_PATCH_ATTR, True)
    tk.Tk.__init__ = _tk_init_with_tabbing_disabled  # type: ignore[method-assign]


def _ensure_mac_window_tabbing_disabled(toplevel: tk.Misc) -> None:
    if getattr(toplevel, "_tkwry_mac_window_tabbing", False):
        return
    toplevel._tkwry_mac_window_tabbing = True
    try:
        from tkwry._core import disable_macos_window_tabbing
        from tkwry._parent import tk_parent_handle

        toplevel.update_idletasks()
        disable_macos_window_tabbing(tk_parent_handle(toplevel))
    except Exception:
        pass


def _register_macos_webview(web: WebView) -> None:
    try:
        toplevel = web._frame.winfo_toplevel()
    except tk.TclError:
        return
    web._macos_toplevel = toplevel
    views: list[WebView] | None = getattr(toplevel, "_tkwry_mac_webviews", None)
    if views is None:
        if getattr(toplevel, "_tkwry_mac_torn_down", False):
            delattr(toplevel, "_tkwry_mac_torn_down")
        views = []
        toplevel._tkwry_mac_webviews = views
        toplevel._tkwry_mac_web_input_active = False
        _ensure_mac_key_guard(toplevel)
        if not getattr(toplevel, "_tkwry_mac_destroy_bind_id", None):
            toplevel._tkwry_mac_destroy_bind_id = toplevel.bind(
                "<Destroy>", _mac_toplevel_destroy, add="+"
            )
    _ensure_mac_window_tabbing_disabled(toplevel)
    views.append(weakref.ref(web))


def _unregister_macos_webview(web: WebView) -> None:
    toplevel = getattr(web, "_macos_toplevel", None)
    if hasattr(web, "_macos_toplevel"):
        delattr(web, "_macos_toplevel")
    if toplevel is None:
        try:
            toplevel = web._frame.winfo_toplevel()
        except tk.TclError:
            return
    views = getattr(toplevel, "_tkwry_mac_webviews", None)
    if not views:
        return
    views[:] = [
        entry
        for entry in views
        if (entry() if isinstance(entry, weakref.ReferenceType) else entry) is not web
    ]
    if not views:
        _teardown_macos_toplevel(toplevel)
