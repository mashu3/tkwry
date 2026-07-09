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

    def __init__(self, root: tk.Misc) -> None:
        self._root = root.winfo_toplevel()
        self._root_id = self._root.winfo_id()
        self._active = False
        self._refcount = 0
        self._destroy_bind_id: str | None = None
        self._tick_after_id: str | None = None

    def _schedule_tick(self, delay_ms: int) -> None:
        self._cancel_tick()
        self._tick_after_id = self._root.after(
            delay_ms, lambda rid=self._root_id: _gtk_pump_tick(rid)
        )

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
    def attach(cls, widget: tk.Misc) -> None:
        if sys.platform != "linux":
            return
        root = widget.winfo_toplevel()
        root_id = root.winfo_id()
        pump = cls._by_root_id.get(root_id)
        if pump is None:
            pump = cls(root)
            cls._by_root_id[root_id] = pump
        pump._refcount += 1
        pump.start()

    @classmethod
    def detach(cls, widget: tk.Misc) -> None:
        """Drop one WebView attachment; stop pumping when none remain."""
        if sys.platform != "linux":
            return
        try:
            root = widget.winfo_toplevel()
            root_id = root.winfo_id()
        except Exception:
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
        self._refcount = 0
        bind_id = self._destroy_bind_id
        self._destroy_bind_id = None
        if bind_id is not None:
            try:
                self._root.unbind("<Destroy>", bind_id)
            except Exception:
                pass
        self._by_root_id.pop(self._root_id, None)

    def _on_destroy(self, event) -> None:
        if event.widget is not self._root:
            return
        self.stop()
