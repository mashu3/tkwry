"""Demo: native drag-and-drop files onto the embedded WebView."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

try:
    from tkwry import DragDropEvent, WebView
except ImportError as exc:
    import tkwry

    raise SystemExit(
        "This demo needs the current tkwry from this repository.\n"
        f"  imported: {tkwry.__file__}\n"
        "  fix:      pip install -e ."
    ) from exc

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: system-ui, sans-serif;
      background: #1a1a2e;
      color: #eaeaea;
      display: grid;
      place-items: center;
      padding: 24px;
    }
    .dropzone {
      width: min(520px, 92vw);
      padding: 48px 32px;
      border: 2px dashed #4a6fa5;
      border-radius: 20px;
      text-align: center;
      background: rgba(255, 255, 255, 0.04);
    }
    .dropzone.active {
      border-color: #7ee787;
      background: rgba(126, 231, 135, 0.08);
    }
    h1 { margin: 0 0 12px; font-size: 1.4rem; }
    p { margin: 0; color: #a8b0c0; line-height: 1.5; }
    ul {
      list-style: none;
      padding: 0;
      margin: 20px 0 0;
      text-align: left;
      font-size: 0.92rem;
    }
    li {
      padding: 8px 12px;
      margin: 6px 0;
      border-radius: 8px;
      background: rgba(0, 0, 0, 0.25);
      word-break: break-all;
    }
  </style>
</head>
<body>
  <div class="dropzone" id="zone">
    <h1>Drop files here</h1>
    <p>Drag files from Finder onto this WebView.<br>
       Uses OS webview DnD (not tkinterdnd2).</p>
    <ul id="list"></ul>
  </div>
  <script>
    window.showPaths = function(paths, active) {
      const zone = document.getElementById('zone');
      zone.classList.toggle('active', !!active);
      const list = document.getElementById('list');
      list.innerHTML = '';
      paths.forEach(p => {
        const li = document.createElement('li');
        li.textContent = p;
        list.appendChild(li);
      });
    };
  </script>
</body>
</html>
"""


def main() -> None:
    root = tk.Tk()
    root.title("tkwry dnd demo")
    root.geometry("900x620")

    status = tk.StringVar(value="Waiting for drop…")
    ttk.Label(root, textvariable=status).pack(fill="x", padx=12, pady=(12, 0))

    frame = tk.Frame(root, bg="#111")
    frame.pack(fill="both", expand=True, padx=12, pady=12)

    web: WebView

    def on_drag_drop(
        event: DragDropEvent, paths: list[str], position: tuple[int, int]
    ) -> None:
        # Runs on the Tk main thread (queued by WebView). Notify-only.
        if event == DragDropEvent.Enter:
            status.set(f"Over WebView: {len(paths)} file(s) @ {position}")
            web.eval_js("window.showPaths([], true)")
            return
        if event == DragDropEvent.Leave:
            status.set("Waiting for drop…")
            web.eval_js("window.showPaths([], false)")
            return
        if event == DragDropEvent.Drop:
            status.set(f"Dropped {len(paths)} file(s)")
            payload = json.dumps(paths)
            web.eval_js(f"window.showPaths({payload}, false)")
            return

    web = WebView(
        frame,
        html=HTML,
        background_color=(26, 26, 46, 255),
        drag_drop_handler=on_drag_drop,
    )

    root.mainloop()


if __name__ == "__main__":
    main()
