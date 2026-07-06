"""Native bounds synchronization with Tk layout."""

from __future__ import annotations

import sys

import pytest
from support.layout import attach_bounds_recorder, bounds_close, expected_bounds
from support.tk import pump, skip_linux_layout, wait_until

from tkwry import WebView

pytestmark = skip_linux_layout


def test_bounds_synced_when_web_packed_before_create(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html="<p>pack-first</p>")
    web.pack(fill="both", expand=True)

    assert wait_until(tk_root, lambda: web.native is not None)
    records = attach_bounds_recorder(web)
    web._sync_bounds()
    pump(tk_root, steps=40)

    expected = expected_bounds(host)
    assert host.winfo_width() > 100 and host.winfo_height() > 100
    assert bounds_close(records, expected), (
        f"expected bounds {expected}, "
        f"last set_bounds={records[-1] if records else None}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    host.destroy()


def test_bounds_synced_after_host_packed_late(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html="<p>late-host-pack</p>")
    records = attach_bounds_recorder(web)

    assert wait_until(tk_root, lambda: web.native is not None, steps=20) is False

    host.pack(fill="both", expand=True)
    pump(tk_root, steps=80)

    assert wait_until(tk_root, lambda: web.native is not None)
    host.update_idletasks()
    expected = expected_bounds(host)
    assert host.winfo_width() > 100 and host.winfo_height() > 100
    assert bounds_close(records, expected), (
        f"expected bounds {expected}, records={records[-3:]}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    host.destroy()


def test_bounds_skip_1x1_during_late_host_pack(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    host = tk.Frame(tk_root, bg="#222")
    web = WebView(host, html="<p>no-1x1-sync</p>")
    records = attach_bounds_recorder(web)

    host.pack(fill="both", expand=True)
    pump(tk_root, steps=80)
    assert wait_until(tk_root, lambda: web.ready)

    sizes = [(int(w), int(h)) for _x, _y, w, h in records]
    assert sizes, "expected at least one applied bounds sync"
    assert all(w > 1 and h > 1 for w, h in sizes), (
        f"bounds sync should never apply 1x1 geometry: {sizes}"
    )

    web.destroy()
    host.destroy()


def test_pack_schedules_bounds_sync_without_configure(tk_root, monkeypatch) -> None:
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
        if callback == web._deferred_sync_bounds:
            scheduled.append(True)
        return original_after_idle(callback)

    monkeypatch.setattr(host, "after_idle", track_after_idle)

    web.pack(fill="both", expand=True)
    pump(tk_root, steps=10)

    assert scheduled, "pack() should schedule an idle bounds sync"

    web.destroy()
    host.destroy()


def test_bounds_shrink_when_sibling_packed_after_create(tk_root) -> None:
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
        f"host height should shrink after header pack: "
        f"before={before_h} after={after_h}"
    )

    records = attach_bounds_recorder(web)
    web._sync_bounds()
    pump(tk_root, steps=20)

    expected = expected_bounds(host)
    assert bounds_close(records, expected), (
        f"expected bounds {expected}, last={records[-1] if records else None}, "
        f"host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    body.destroy()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows WebView2 layout",
)
def test_bounds_without_manual_sync_after_sibling_pack(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("520x380")
    body = tk.Frame(tk_root)
    body.pack(fill="both", expand=True)

    host = tk.Frame(body, bg="#222")
    host.pack(fill="both", expand=True)

    web = WebView(host, html="<p>auto-sync</p>")
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=30)

    records = attach_bounds_recorder(web)
    records.clear()

    header = tk.Frame(body, height=48, bg="#444")
    header.pack(side="top", fill="x", before=host)
    header.pack_propagate(False)
    pump(tk_root, steps=80)

    host.update_idletasks()
    expected = expected_bounds(host)
    assert host.winfo_height() > 50
    assert bounds_close(records, expected), (
        f"set_bounds not auto-synced after sibling pack: expected {expected}, "
        f"records={records[-5:]}, host=({host.winfo_width()}x{host.winfo_height()})"
    )

    web.destroy()
    body.destroy()


def test_bounds_after_fixed_size_frame_without_propagate(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("480x320")
    host = tk.Frame(tk_root, width=360, height=220, bg="#222")
    host.pack_propagate(False)
    host.pack(padx=20, pady=20)

    web = WebView(
        host,
        html="<body style='margin:0;display:grid;place-items:center'>x</body>",
    )
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=40)

    records = attach_bounds_recorder(web)
    web._sync_bounds()

    expected = expected_bounds(host)
    assert abs(expected[2] - 360) <= 4
    assert abs(expected[3] - 220) <= 4
    assert bounds_close(records, expected), (
        f"expected {expected}, got {records[-1] if records else None}"
    )

    web.destroy()
    host.destroy()


def test_sync_bounds_public_api(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("480x320")
    host = tk.Frame(tk_root, width=360, height=220, bg="#222")
    host.pack_propagate(False)
    host.pack(padx=20, pady=20)

    web = WebView(
        host,
        html="<body style='margin:0;display:grid;place-items:center'>x</body>",
    )
    assert wait_until(tk_root, lambda: web.native is not None)
    pump(tk_root, steps=40)

    records = attach_bounds_recorder(web)
    web.sync_bounds()

    expected = expected_bounds(host)
    assert bounds_close(records, expected), (
        f"expected {expected}, got {records[-1] if records else None}"
    )

    web.destroy()
    host.destroy()
