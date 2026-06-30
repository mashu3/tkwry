"""Layout synchronization tests for embedded WebView bounds."""

from __future__ import annotations

import sys

import pytest
from helpers import pump, wait_until

from tkwry import WebView
from tkwry._parent import tk_embed_origin, tk_embed_parent

pytestmark = pytest.mark.integration

# Tk frame chrome and timing can differ slightly across platforms.
_BOUNDS_TOLERANCE = 4


def _expected_bounds(frame) -> tuple[float, float, float, float]:
    frame.update_idletasks()
    embed = tk_embed_parent(frame)
    x, y = tk_embed_origin(frame, root_relative=embed.root_relative)
    width = max(frame.winfo_width(), 1)
    height = max(frame.winfo_height(), 1)
    return (x, y, width, height)


def _close(records: list[tuple[float, float, float, float]], expected) -> bool:
    if not records:
        return False
    actual = records[-1]
    return all(abs(a - e) <= _BOUNDS_TOLERANCE for a, e in zip(actual, expected))


def _attach_sync_recorder(web: WebView) -> list[tuple[float, float, float, float]]:
    """Record geometry each time ``_sync_bounds`` runs."""
    records: list[tuple[float, float, float, float]] = []
    original = web._sync_bounds

    def record() -> None:
        web._frame.update_idletasks()
        records.append(_expected_bounds(web._frame))
        original()

    web._sync_bounds = record  # type: ignore[method-assign]
    return records


def test_bounds_synced_when_web_packed_before_create(tk_root) -> None:
    """pack() on WebView before native creation must end with correct bounds."""
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html="<p>pack-first</p>")
    web.pack(fill="both", expand=True)

    assert wait_until(tk_root, lambda: web.native is not None)
    records = _attach_sync_recorder(web)
    web._sync_bounds()
    pump(tk_root, steps=40)

    expected = _expected_bounds(host)
    assert host.winfo_width() > 100 and host.winfo_height() > 100
    assert _close(records, expected), (
        f"expected bounds {expected}, last set_bounds={records[-1] if records else None}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    host.destroy()


def test_bounds_synced_after_host_packed_late(tk_root) -> None:
    """Host frame packed after WebView() must still sync to final geometry."""
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html="<p>late-host-pack</p>")
    records = _attach_sync_recorder(web)

    assert wait_until(tk_root, lambda: web.native is not None, steps=20) is False

    host.pack(fill="both", expand=True)
    pump(tk_root, steps=80)

    assert wait_until(tk_root, lambda: web.native is not None)
    host.update_idletasks()
    expected = _expected_bounds(host)
    assert host.winfo_width() > 100 and host.winfo_height() > 100
    assert _close(records, expected), (
        f"expected bounds {expected}, records={records[-3:]}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    host.destroy()


def test_pack_schedules_bounds_sync_without_configure(tk_root, monkeypatch) -> None:
    """web.pack() must schedule _sync_bounds even if <Configure> does not fire."""
    import tkinter as tk

    tk_root.geometry("400x300")
    host = tk.Frame(tk_root, width=320, height=200, bg="#222")
    host.pack_propagate(False)
    host.pack()

    web = WebView(host, html="<p>pack-sync</p>")
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=20)

    scheduled: list[bool] = []
    original_after_idle = host.after_idle

    def track_after_idle(callback):
        if callback == web._sync_bounds:
            scheduled.append(True)
        return original_after_idle(callback)

    monkeypatch.setattr(host, "after_idle", track_after_idle)

    # Re-pack with identical options: Tk often skips <Configure>.
    web.pack(fill="both", expand=True)
    pump(tk_root, steps=10)

    assert scheduled, "pack() should schedule an idle bounds sync"

    web.destroy()
    host.destroy()


def test_bounds_shrink_when_sibling_packed_after_create(tk_root) -> None:
    """Packing another frame after WebView exists must update native bounds."""
    import tkinter as tk

    tk_root.geometry("520x380")
    body = tk.Frame(tk_root)
    body.pack(fill="both", expand=True)

    host = tk.Frame(body, bg="#222")
    host.pack(fill="both", expand=True)

    web = WebView(host, html="<p>sibling-pack</p>")
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=30)

    before_h = host.winfo_height()
    assert before_h > 80

    header = tk.Frame(body, height=48, bg="#444")
    header.pack(side="top", fill="x", before=host)
    header.pack_propagate(False)
    pump(tk_root, steps=60)

    host.update_idletasks()
    after_h = host.winfo_height()
    assert after_h < before_h - 30, (
        f"host height should shrink after header pack: before={before_h} after={after_h}"
    )

    records = _attach_sync_recorder(web)
    web._sync_bounds()
    pump(tk_root, steps=20)

    expected = _expected_bounds(host)
    assert _close(records, expected), (
        f"expected bounds {expected}, last={records[-1] if records else None}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    body.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows WebView2 layout regression")
def test_bounds_without_manual_sync_after_sibling_pack(tk_root) -> None:
    """Sibling pack must trigger bounds sync without calling _sync_bounds manually."""
    import tkinter as tk

    tk_root.geometry("520x380")
    body = tk.Frame(tk_root)
    body.pack(fill="both", expand=True)

    host = tk.Frame(body, bg="#222")
    host.pack(fill="both", expand=True)

    web = WebView(host, html="<p>auto-sync</p>")
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=30)

    records = _attach_sync_recorder(web)
    records.clear()

    header = tk.Frame(body, height=48, bg="#444")
    header.pack(side="top", fill="x", before=host)
    header.pack_propagate(False)
    pump(tk_root, steps=80)

    host.update_idletasks()
    expected = _expected_bounds(host)
    assert host.winfo_height() > 50
    assert _close(records, expected), (
        f"set_bounds not auto-synced after sibling pack: expected {expected}, "
        f"records={records[-5:]}, host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    body.destroy()


def test_bounds_after_fixed_size_frame_without_propagate(tk_root) -> None:
    """Initial layout on a fixed-size frame must match frame geometry."""
    import tkinter as tk

    tk_root.geometry("480x320")
    host = tk.Frame(tk_root, width=360, height=220, bg="#222")
    host.pack_propagate(False)
    host.pack(padx=20, pady=20)

    web = WebView(host, html="<body style='margin:0;display:grid;place-items:center'>x</body>")
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=40)

    records = _attach_sync_recorder(web)
    web._sync_bounds()

    expected = _expected_bounds(host)
    assert abs(expected[2] - 360) <= _BOUNDS_TOLERANCE
    assert abs(expected[3] - 220) <= _BOUNDS_TOLERANCE
    assert _close(records, expected), (
        f"expected {expected}, got {records[-1] if records else None}"
    )

    web.destroy()
    host.destroy()
