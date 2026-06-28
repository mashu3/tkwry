"""Event-loop helpers for Gtk/WebKitGTK on Linux."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk


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
        from tkwry._core import ensure_gtk_init, pump_events

        ensure_gtk_init()
        self._active = True
        self._root.bind("<Destroy>", self._on_destroy, add="+")
        self._root.after(0, lambda: self._pump(pump_events))

    def _on_destroy(self, event) -> None:
        if event.widget is not self._root:
            return
        self._active = False
        self._by_root_id.pop(self._root_id, None)

    def _pump(self, pump_events) -> None:
        if not self._active:
            return
        pump_events()
        self._root.after(10, lambda: self._pump(pump_events))
