"""Event-loop helpers for Gtk/WebKitGTK on Linux."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk


def _gtk_pump_tick(root_id: int) -> None:
    """Run one GTK pump tick for *root_id* without holding a GtkPump reference."""
    pump = GtkPump._by_root_id.get(root_id)
    if pump is None or not pump._active:
        return
    root = pump._root
    try:
        if not root.winfo_exists():
            pump.stop()
            return
    except Exception:
        pump.stop()
        return
    pump._clear_tick_after_id()
    from tkwry._core import pump_events

    pump_events()
    if pump._active:
        pump._schedule_tick(10)


class GtkPump:
    """Pump GTK events via ``tkwry._core.pump_events`` while Tk runs."""

    _by_root_id: dict[int, GtkPump] = {}
    # widget id -> (root_id, attach count for that widget)
    _widget_attachments: dict[int, tuple[int, int]] = {}

    def __init__(self, root: tk.Misc) -> None:
        self._root = root.winfo_toplevel()
        self._root_id = self._root.winfo_id()
        self._active = False
        self._refcount = 0
        self._destroy_bind_id: str | None = None
        self._tick_after_id: str | None = None

    def _schedule_tick(self, delay_ms: int) -> None:
        self._cancel_tick()
        root_id = self._root_id

        def _tick() -> None:
            _gtk_pump_tick(root_id)

        self._tick_after_id = self._root.after(delay_ms, _tick)

    def _cancel_tick(self) -> None:
        after_id = self._tick_after_id
        self._tick_after_id = None
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except Exception:
            pass

    def _clear_tick_after_id(self) -> None:
        self._tick_after_id = None

    @classmethod
    def _resolve_root_id(cls, widget: tk.Misc) -> int | None:
        try:
            return widget.winfo_toplevel().winfo_id()
        except Exception:
            entry = cls._widget_attachments.get(id(widget))
            if entry is None:
                return None
            return entry[0]

    @classmethod
    def _record_widget_attach(cls, widget: tk.Misc, root_id: int) -> None:
        widget_id = id(widget)
        root_id_for_widget, count = cls._widget_attachments.get(widget_id, (root_id, 0))
        cls._widget_attachments[widget_id] = (root_id_for_widget, count + 1)

    @classmethod
    def _release_widget_attach(cls, widget: tk.Misc) -> int | None:
        widget_id = id(widget)
        entry = cls._widget_attachments.get(widget_id)
        if entry is None:
            return None
        root_id, count = entry
        if count <= 1:
            del cls._widget_attachments[widget_id]
        else:
            cls._widget_attachments[widget_id] = (root_id, count - 1)
        return root_id

    @classmethod
    def _purge_widget_attachments(cls, root_id: int) -> None:
        stale = [
            widget_id
            for widget_id, (attached_root_id, _) in cls._widget_attachments.items()
            if attached_root_id == root_id
        ]
        for widget_id in stale:
            del cls._widget_attachments[widget_id]

    @classmethod
    def attach(cls, widget: tk.Misc) -> None:
        if sys.platform != "linux":
            return
        root_id = cls._resolve_root_id(widget)
        if root_id is None:
            return
        pump = cls._by_root_id.get(root_id)
        if pump is None:
            pump = cls(widget)
            cls._by_root_id[root_id] = pump
        cls._record_widget_attach(widget, root_id)
        pump._refcount += 1
        pump.start()

    @classmethod
    def detach(cls, widget: tk.Misc) -> None:
        """Drop one WebView attachment; stop pumping when none remain."""
        if sys.platform != "linux":
            return
        root_id = cls._release_widget_attach(widget)
        if root_id is None:
            return
        pump = cls._by_root_id.get(root_id)
        if pump is None:
            return
        pump._refcount = max(0, pump._refcount - 1)
        if pump._refcount == 0:
            pump.stop()

    def start(self) -> None:
        if self._active:
            return
        from tkwry._core import ensure_gtk_init

        ensure_gtk_init()
        self._active = True
        self._destroy_bind_id = self._root.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule_tick(0)

    def stop(self) -> None:
        self._active = False
        self._cancel_tick()
        bind_id = self._destroy_bind_id
        self._destroy_bind_id = None
        if bind_id is not None:
            try:
                self._root.unbind("<Destroy>", bind_id)
            except Exception:
                pass
        self._purge_widget_attachments(self._root_id)
        self._by_root_id.pop(self._root_id, None)

    def _on_destroy(self, event) -> None:
        if event.widget is not self._root:
            return
        self.stop()
