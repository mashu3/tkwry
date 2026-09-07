#!/usr/bin/env python3
"""Measure macOS keyboard-ownership handoff (no CGEvent).

Prints median / p95 / max for:

- ``focus()`` → ``mac_web_input_active()`` true
- ``focus_parent()`` → inactive
- ``mac_request_tk_unfocus`` → Tcl focus leaves Entry (local probe)

Used to refresh the declared numbers in ``docs/platforms.md``.
Not a CI gate — CI keeps the generous budget assert in
``tests/macos/test_input_ci.py``.
"""

from __future__ import annotations

import statistics
import sys
import time
import tkinter as tk
from tkinter import ttk

if sys.platform != "darwin":
    raise SystemExit("macOS only")

from tkwry import WebView  # noqa: E402
from tkwry._macos import _mac_service_wakeup  # noqa: E402


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _wait_until(root: tk.Misc, pred, *, timeout: float = 2.0) -> float | None:
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        if pred():
            return time.monotonic() - start
        time.sleep(0.002)
    return None


def _pump(root: tk.Misc, seconds: float = 0.05) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        time.sleep(0.005)


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def _summarize(name: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    print(
        f"{name}: n={len(samples)}  "
        f"median={_fmt_ms(statistics.median(ordered))}  "
        f"p95={_fmt_ms(_percentile(ordered, 95))}  "
        f"max={_fmt_ms(max(ordered))}"
    )


def main() -> int:
    rounds = 20
    root = tk.Tk()
    root.geometry("640x480")
    root.title("tkwry input measure")

    toolbar = ttk.Frame(root)
    toolbar.pack(fill="x", padx=8, pady=(8, 0))
    entry = ttk.Entry(toolbar)
    entry.pack(side="left", fill="x", expand=True)
    frame = tk.Frame(root, bg="#1e1e1e")
    frame.pack(fill="both", expand=True, padx=8, pady=8)
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.focus_force()
    root.update()

    web = WebView(frame, html="<p>measure</p>")
    try:
        if _wait_until(root, lambda: web.native is not None, timeout=10.0) is None:
            print("native WebView did not become ready", file=sys.stderr)
            return 1
        native = web.native
        assert native is not None

        activate: list[float] = []
        resign: list[float] = []
        tcl_leave: list[float] = []

        for i in range(rounds):
            entry.focus_force()
            root.update()
            web.focus_parent()
            _pump(root, 0.05)
            if native.mac_web_input_active():
                print(f"round {i}: expected inactive before focus()", file=sys.stderr)
                return 1

            web.focus()
            elapsed = _wait_until(
                root,
                lambda: native.mac_web_input_active(),
                timeout=2.0,
            )
            if elapsed is None:
                print(f"round {i}: focus() handoff timed out", file=sys.stderr)
                return 1
            activate.append(elapsed)

            web.focus_parent()
            elapsed = _wait_until(
                root,
                lambda: not native.mac_web_input_active(),
                timeout=2.0,
            )
            if elapsed is None:
                print(f"round {i}: focus_parent() handoff timed out", file=sys.stderr)
                return 1
            resign.append(elapsed)

            entry.focus_force()
            entry.delete(0, tk.END)
            entry.insert(0, "https://example.com")
            root.update()
            native.mac_request_tk_unfocus()
            start = time.monotonic()
            left = False
            deadline = start + 0.25
            while time.monotonic() < deadline:
                _mac_service_wakeup(root)
                root.update_idletasks()
                root.update()
                if root.focus_get() is not entry:
                    left = True
                    break
                time.sleep(0.002)
            if not left:
                print(f"round {i}: Tcl unfocus timed out", file=sys.stderr)
                return 1
            tcl_leave.append(time.monotonic() - start)

        print(f"host: local macOS  rounds={rounds}")
        _summarize("focus() → web active", activate)
        _summarize("focus_parent() → inactive", resign)
        _summarize("mac_request_tk_unfocus → Tcl leave", tcl_leave)
        print(
            "note: IME composition latency is not measured "
            "(first-responder contract; not Safari parity)"
        )
        return 0
    finally:
        web.destroy()
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
