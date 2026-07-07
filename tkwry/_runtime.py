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
    from tkwry._core import pump_events

    pump_events()
    root.after(10, lambda rid=root_id: _gtk_pump_tick(rid))


class GtkPump:
    """Pump GTK events via ``tkwry._core.pump_events`` while Tk runs."""

    _by_root_id: dict[int, GtkPump] = {}

    def __init__(self, root: tk.Misc) -> None:
        self._root = root.winfo_toplevel()
        self._root_id = self._root.winfo_id()
        self._active = False

    @classmethod
    def attach(cls, widget: tk.Misc) -> None:
        if sys.platform != "linux":
            return
        root = widget.winfo_toplevel()
        root_id = root.winfo_id()
        if root_id in cls._by_root_id:
            return
        pump = cls(root)
        cls._by_root_id[root_id] = pump
        pump.start()

    def start(self) -> None:
        if self._active:
            return
        from tkwry._core import ensure_gtk_init

        ensure_gtk_init()
        self._active = True
        self._root.bind("<Destroy>", self._on_destroy, add="+")
        self._root.after(0, lambda rid=self._root_id: _gtk_pump_tick(rid))

    def stop(self) -> None:
        self._active = False
        self._by_root_id.pop(self._root_id, None)

    def _on_destroy(self, event) -> None:
        if event.widget is not self._root:
            return
        self.stop()
