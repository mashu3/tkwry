"""URL bar demo: Tkinter frame with embedded WebView."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from tkwry import WebView


def main() -> None:
    root = tk.Tk()
    root.title("tkwry demo")
    root.geometry("960x640")

    toolbar = ttk.Frame(root)
    toolbar.pack(fill="x", padx=8, pady=(8, 0))

    url_var = tk.StringVar(value="https://github.com")
    url_entry = ttk.Entry(toolbar, textvariable=url_var)
    url_entry.pack(side="left", fill="x", expand=True)

    frame = tk.Frame(root, bg="#1e1e1e")
    frame.pack(fill="both", expand=True, padx=8, pady=8)

    web_kwargs: dict = {"url": "https://github.com"}
    if sys.platform == "win32":
        # WebView2 takes keyboard focus on create when focused=True.
        web_kwargs["focused"] = False
    web = WebView(frame, **web_kwargs)

    def go() -> None:
        try:
            web.load_url(url_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid URL", str(exc), parent=root)

    ttk.Button(toolbar, text="Go", command=go).pack(side="left", padx=(8, 0))
    url_entry.bind("<Return>", lambda _e: go())
    url_entry.bind("<Button-1>", lambda _e: url_entry.icursor(tk.END), add="+")

    # Avoid starting with the URL bar caret active while the pointer is elsewhere.
    root.focus_set()

    root.mainloop()


if __name__ == "__main__":
    main()
