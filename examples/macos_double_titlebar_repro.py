"""Minimal repro: macOS double titlebar when AppKit starts before tkwry.

Run on macOS and compare the two windows:

    python examples/macos_double_titlebar_repro.py

The **bad** window (left) imports ``AppKit`` and touches ``NSApplication`` before
``tkwry``. macOS may reserve automatic window-tab chrome, producing a double
titlebar strip above the normal Tk title.

The **good** window (right) imports ``tkwry`` first so process-level automatic
window tabbing is disabled before full AppKit startup.
"""

from __future__ import annotations

import sys

if sys.platform != "darwin":
    print("macOS only")
    raise SystemExit(0)


def _bad_window() -> None:
    # Third-party pattern: pyobjc AppKit before tkwry (too late for tabbing opt-out).
    from AppKit import NSApplication

    NSApplication.sharedApplication()

    import tkinter as tk

    from tkwry import WebView

    root = tk.Tk()
    root.title("BAD — AppKit before tkwry")
    root.geometry("420x320+40+40")
    frame = tk.Frame(root, width=400, height=280)
    frame.pack(fill="both", expand=True)
    frame.pack_propagate(False)
    web = WebView(frame, width=400, height=280, html="<h2>Bad import order</h2>")
    root.after(8000, lambda: (web.destroy(), root.destroy()))
    root.mainloop()


def _good_window() -> None:
    import tkinter as tk

    from tkwry import WebView

    root = tk.Tk()
    root.title("GOOD — tkwry before AppKit")
    root.geometry("420x320+480+40")
    frame = tk.Frame(root, width=400, height=280)
    frame.pack(fill="both", expand=True)
    frame.pack_propagate(False)
    web = WebView(frame, width=400, height=280, html="<h2>Good import order</h2>")
    root.after(8000, lambda: (web.destroy(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing

    bad = multiprocessing.Process(target=_bad_window)
    good = multiprocessing.Process(target=_good_window)
    bad.start()
    good.start()
    bad.join()
    good.join()
