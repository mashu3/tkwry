"""Linux-specific event-loop helpers for Gtk/WebKitGTK.

**Pump policy (keep #7 / GtkPump changes aligned here):**

1. **One drain per toplevel** — ``GtkPump`` owns the scheduled tick while any
   WebView on that root is attached (``refcount > 0``).
2. **No nested busy pumps that starve Tk** — call sites outside the tick must
   use :func:`pump_gtk_unless_active` so a live GtkPump is not doubled from
   WebView poll / navigation / wait helpers. Prefer delivering async queues
   over forcing extra ``pump_events`` bursts.
3. **Allowed direct** :func:`pump_gtk_events` — GtkPump's own tick, create-time
   GTK bootstrap before ``NativeWebView``, synchronous destroy flush after
   detach, and tests.
4. **No timing-skip loops** — do not fix page_load / multi-WebView flakes by
   adding one-off delay constants; fix attach / yield / single-drain instead.
"""

from __future__ import annotations

import sys
import tkinter as tk
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_PUMP_ITERATIONS = 512
_PUMP_BURST_BASE = 1
_PUMP_BURST_MAX = 8
_PUMP_BACKLOG_MAX_PASSES = 8
_PUMP_TICK_IDLE_MS = 10
_PUMP_TICK_BUSY_MS = 0
# Cap delay=0 chains so nested Tk ``update()`` can progress when WebKitGTK
# keeps ``events_pending`` true under Xvfb (especially with multiple views).
_PUMP_MAX_CONSECUTIVE_BUSY = 2
_PUMP_ERROR_LIMIT = 5
_PUMP_ERROR_RECOVERY_BASE_MS = 10
_PUMP_ERROR_RECOVERY_MAX_MS = 200


def pump_gtk_events(
    *,
    bursts: int = 1,
    max_iterations: int | None = _PUMP_ITERATIONS,
    refcount: int = 1,
) -> bool:
    """Pump GTK with bounded bursts; return True when the queue still has work."""
    from tkwry._core import ensure_gtk_init, pump_events

    ensure_gtk_init()
    burst_count = min(_PUMP_BURST_MAX, _PUMP_BURST_BASE + max(0, refcount - 1))
    if bursts > 1:
        burst_count = min(_PUMP_BURST_MAX, burst_count + bursts - 1)
    backlog = False
    for _ in range(burst_count):
        if pump_events(max_iterations):
            backlog = True
        else:
            break
    return backlog


def pump_gtk_unless_active(
    widget: tk.Misc,
    *,
    bursts: int = 1,
    max_iterations: int | None = _PUMP_ITERATIONS,
    refcount: int = 1,
) -> bool:
    """Ad-hoc GTK pump that no-ops when ``GtkPump`` already drains *widget*'s root.

    Returns False when skipped (shared pump owns the drain) or when the queue
    went idle after pumping. Nested WebView call sites should use this instead
    of :func:`pump_gtk_events` so Xvfb multi-WebView paths do not starve Tk.
    """
    if GtkPump.is_active_for(widget):
        return False
    return pump_gtk_events(
        bursts=bursts,
        max_iterations=max_iterations,
        refcount=refcount,
    )


def drain_gtk_with_tk(root: tk.Misc, *, rounds: int = 32) -> None:
    """Interleave bounded GTK pumps with Tk updates (test / teardown isolation)."""
    from tkwry._core import ensure_gtk_init, pump_events

    ensure_gtk_init()
    for _ in range(rounds):
        try:
            root.update_idletasks()
            root.update()
        except tk.TclError:
            break
        pump_events()


def _gtk_pump_tick(root_key: int) -> None:
    """Run one GTK pump tick for *root_key* without holding a GtkPump reference."""
    pump = GtkPump._by_root_key.get(root_key)
    if pump is None or not pump._active:
        return
    root = pump._root
    try:
        if not root.winfo_exists():
            pump.stop()
            return
    except tk.TclError:
        pump.stop()
        return
    pump._clear_tick_after_id()
    backlog = False
    try:
        for _ in range(_PUMP_BACKLOG_MAX_PASSES):
            if pump_gtk_events(refcount=pump._refcount):
                backlog = True
                continue
            break
    except Exception:
        traceback.print_exc()
        pump._handle_pump_error()
        return
    pump._consecutive_errors = 0
    if not pump._active:
        return
    if backlog:
        pump._consecutive_busy += 1
    else:
        pump._consecutive_busy = 0
    use_busy = backlog and pump._consecutive_busy <= _PUMP_MAX_CONSECUTIVE_BUSY
    if backlog and not use_busy:
        # Yielded to Tk; allow a fresh busy burst on the next backlog streak.
        pump._consecutive_busy = 0
    delay = _PUMP_TICK_BUSY_MS if use_busy else _PUMP_TICK_IDLE_MS
    pump._schedule_tick(delay)


class GtkPump:
    """One scheduled GTK drain per Tk toplevel while WebViews are attached.

    See module docstring for the nesting / ``pump_gtk_unless_active`` rules.
    """

    _by_root_key: dict[int, GtkPump] = {}
    # widget id -> (root_key, attach count for that widget)
    _widget_attachments: dict[int, tuple[int, int]] = {}
    _pending_attach: set[int] = set()

    def __init__(self, root: tk.Misc, *, root_key: int | None = None) -> None:
        self._root = root.winfo_toplevel()
        self._root_key = root_key if root_key is not None else id(self._root)
        self._active = False
        self._refcount = 0
        self._consecutive_errors = 0
        self._consecutive_busy = 0
        self._recovery_pending = False
        self._destroy_bind_id: str | None = None
        self._tick_after_id: str | None = None

    def _schedule_callback(self, delay_ms: int, callback) -> str | None:
        try:
            return self._root.after(delay_ms, callback)
        except tk.TclError:
            return None

    def _schedule_idle(self, callback) -> bool:
        try:
            self._root.after_idle(callback)
            return True
        except tk.TclError:
            return False

    def _schedule_tick(self, delay_ms: int) -> None:
        self._cancel_tick()
        root_key = self._root_key

        def _tick() -> None:
            _gtk_pump_tick(root_key)

        after_id = self._schedule_callback(delay_ms, _tick)
        if after_id is None and delay_ms > 0:
            after_id = self._schedule_callback(0, _tick)
        if after_id is not None:
            self._tick_after_id = after_id
            return
        if self._schedule_idle(_tick):
            return
        try:
            if self._root.winfo_exists():
                _tick()
                return
        except tk.TclError:
            pass
        self._recovery_pending = True

    def _cancel_tick(self) -> None:
        after_id = self._tick_after_id
        self._tick_after_id = None
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except tk.TclError:
            pass

    def _clear_tick_after_id(self) -> None:
        self._tick_after_id = None

    def _handle_pump_error(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors < _PUMP_ERROR_LIMIT:
            if self._active:
                self._schedule_tick(_PUMP_TICK_IDLE_MS)
            return
        delay = min(
            _PUMP_ERROR_RECOVERY_MAX_MS,
            _PUMP_ERROR_RECOVERY_BASE_MS
            * (2 ** min(self._consecutive_errors - _PUMP_ERROR_LIMIT, 4)),
        )
        print(
            f"tkwry: GTK event pump failed {self._consecutive_errors} "
            f"times; retrying in {delay}ms",
            file=sys.stderr,
        )
        if self._active:
            self._schedule_tick(delay)

    def _resume_if_recovery_pending(self) -> None:
        if not self._recovery_pending or self._refcount <= 0:
            return
        self._recovery_pending = False
        self._consecutive_errors = 0
        if not self._active:
            self.start()
        else:
            self._schedule_tick(0)

    @classmethod
    def reset_all(cls) -> None:
        """Drop all pumps (test isolation / stale X11 window ids)."""
        for pump in list(cls._by_root_key.values()):
            pump.stop()
        cls._by_root_key.clear()
        cls._widget_attachments.clear()
        cls._pending_attach.clear()

    @classmethod
    def _resolve_root_key(cls, widget: tk.Misc) -> int | None:
        try:
            return id(widget.winfo_toplevel())
        except tk.TclError:
            return None

    @classmethod
    def _purge_stale_pump(cls, root_key: int) -> None:
        pump = cls._by_root_key.get(root_key)
        if pump is None:
            return
        try:
            alive = pump._root.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            pump.stop()

    @classmethod
    def _get_or_create_pump(cls, widget: tk.Misc, root_key: int) -> GtkPump:
        cls._purge_stale_pump(root_key)
        pump = cls._by_root_key.get(root_key)
        if pump is None:
            pump = GtkPump(widget, root_key=root_key)
            cls._by_root_key[root_key] = pump
        return pump

    @classmethod
    def _increment_root_refcount(
        cls, widget: tk.Misc, root_key: int, count: int
    ) -> None:
        pump = cls._get_or_create_pump(widget, root_key)
        pump._refcount += count
        pump.start()

    @classmethod
    def _decrement_root_refcount(cls, root_key: int, count: int) -> None:
        pump = cls._by_root_key.get(root_key)
        if pump is None:
            return
        pump._refcount = max(0, pump._refcount - count)
        if pump._refcount == 0:
            pump.stop()

    @classmethod
    def _transfer_widget_to_root(cls, widget: tk.Misc, new_root_key: int) -> None:
        widget_id = id(widget)
        entry = cls._widget_attachments.get(widget_id)
        if entry is None:
            return
        old_root_key, count = entry
        if old_root_key == new_root_key:
            return
        cls._decrement_root_refcount(old_root_key, count)
        cls._widget_attachments[widget_id] = (new_root_key, count)
        cls._increment_root_refcount(widget, new_root_key, count)

    @classmethod
    def _schedule_attach_retry(cls, widget: tk.Misc) -> None:
        widget_id = id(widget)
        if widget_id in cls._pending_attach:
            return
        cls._pending_attach.add(widget_id)
        try:
            root = widget.winfo_toplevel()
        except tk.TclError:
            cls._pending_attach.discard(widget_id)
            return

        def _retry() -> None:
            cls._pending_attach.discard(widget_id)
            try:
                if not widget.winfo_exists():
                    return
            except tk.TclError:
                return
            cls.attach(widget)

        try:
            root.after_idle(_retry)
        except tk.TclError:
            cls._pending_attach.discard(widget_id)

    @classmethod
    def is_active_for(cls, widget: tk.Misc) -> bool:
        """True when a GtkPump is already draining this toplevel."""
        if sys.platform != "linux":
            return False
        root_key = cls._resolve_root_key(widget)
        if root_key is None:
            return False
        pump = cls._by_root_key.get(root_key)
        return pump is not None and pump._active and pump._refcount > 0

    @classmethod
    def ensure_attached(cls, widget: tk.Misc) -> None:
        """Attach *widget* once; retry when the toplevel is not mapped yet."""
        if sys.platform != "linux":
            return
        cls.attach(widget)

    @classmethod
    def attach(cls, widget: tk.Misc) -> None:
        if sys.platform != "linux":
            return
        try:
            cls._attach_impl(widget)
        except tk.TclError:
            cls._schedule_attach_retry(widget)

    @classmethod
    def _attach_impl(cls, widget: tk.Misc) -> None:
        root_key = cls._resolve_root_key(widget)
        if root_key is None:
            cls._schedule_attach_retry(widget)
            return
        cls._pending_attach.discard(id(widget))
        widget_id = id(widget)
        entry = cls._widget_attachments.get(widget_id)
        if entry is not None:
            if entry[0] != root_key:
                cls._transfer_widget_to_root(widget, root_key)
            pump = cls._by_root_key.get(root_key)
            if pump is not None:
                pump._resume_if_recovery_pending()
                if not pump._active and pump._refcount > 0:
                    pump._consecutive_errors = 0
                    pump.start()
            return
        cls._widget_attachments[widget_id] = (root_key, 1)
        try:
            cls._increment_root_refcount(widget, root_key, 1)
        except tk.TclError:
            cls._widget_attachments.pop(widget_id, None)
            raise

    @classmethod
    def detach(cls, widget: tk.Misc) -> None:
        """Drop one WebView attachment; stop pumping when none remain."""
        if sys.platform != "linux":
            return
        cls._pending_attach.discard(id(widget))
        widget_id = id(widget)
        entry = cls._widget_attachments.pop(widget_id, None)
        if entry is None:
            return
        root_key, count = entry
        cls._decrement_root_refcount(root_key, count)

    def start(self) -> None:
        if self._active:
            return
        from tkwry._core import ensure_gtk_init

        ensure_gtk_init()
        self._consecutive_errors = 0
        self._consecutive_busy = 0
        self._recovery_pending = False
        try:
            self._destroy_bind_id = self._root.bind(
                "<Destroy>", self._on_destroy, add="+"
            )
        except tk.TclError:
            self._recovery_pending = True
            return
        self._active = True
        self._schedule_tick(0)

    def stop(self) -> None:
        self._active = False
        self._consecutive_busy = 0
        self._cancel_tick()
        bind_id = self._destroy_bind_id
        self._destroy_bind_id = None
        if bind_id is not None:
            try:
                self._root.unbind("<Destroy>", bind_id)
            except tk.TclError:
                pass
        self._by_root_key.pop(self._root_key, None)

    def _on_destroy(self, event) -> None:
        if event.widget is not self._root:
            return
        self.stop()
