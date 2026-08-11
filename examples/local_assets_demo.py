"""Demo: load a local web app via ``WebView(app=...)`` (tkwry:// protocol).

No localhost HTTP server. Relative CSS/JS under the app root resolve offline.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from tkwry import WebView

APP_DIR = Path(__file__).resolve().parent / "local_assets"


def main() -> None:
    root = tk.Tk()
    root.title("tkwry local assets")
    root.geometry("520x360")
    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)

    def on_ipc(message: str) -> None:
        print(f"ipc: {message}")

    web = WebView(frame, app=APP_DIR, ipc_handler=on_ipc)
    web.pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
