"""Demo: thin RPC via ``@web.expose`` / ``window.tkwry.call``.

Low-level ``window.ipc.postMessage`` still works; this shows the Promise API.
"""

from __future__ import annotations

import tempfile
import tkinter as tk
from pathlib import Path

from tkwry import WebView

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font: 16px/1.5 ui-sans-serif, system-ui, sans-serif;
      background: #14181f; color: #e8eef7;
    }
    main { width: min(28rem, calc(100% - 2rem)); }
    button {
      margin-top: 0.75rem; border: 0; border-radius: 0.5rem;
      padding: 0.55rem 0.9rem; background: #5b9cff; color: #061018;
      font-weight: 600; cursor: pointer;
    }
    #out { margin-top: 1rem; opacity: 0.9; white-space: pre-wrap; }
  </style>
</head>
<body>
  <main>
    <h1>tkwry.call</h1>
    <p>JS awaits Python over the thin RPC layer.</p>
    <button id="go" type="button">call read_file</button>
    <button id="sum" type="button">call add(2, 3)</button>
    <pre id="out">ready</pre>
  </main>
  <script>
    var out = document.getElementById("out");
    function show(text) { out.textContent = text; }
    document.getElementById("go").onclick = async function () {
      try {
        var text = await window.tkwry.call("read_file");
        show(text);
      } catch (e) {
        show(String(e));
      }
    };
    document.getElementById("sum").onclick = async function () {
      try {
        var n = await window.tkwry.call("add", 2, 3);
        show("2 + 3 = " + n);
      } catch (e) {
        show(String(e));
      }
    };
  </script>
</body>
</html>
"""


def main() -> None:
    sample = Path(tempfile.gettempdir()) / "tkwry-rpc-demo-sample.txt"
    sample.write_text(
        "Hello from Python filesystem via window.tkwry.call('read_file').\n",
        encoding="utf-8",
    )

    root = tk.Tk()
    root.title("tkwry RPC demo")
    root.geometry("520x360")
    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)

    web = WebView(frame, html=HTML)

    @web.expose
    def read_file() -> str:
        return sample.read_text(encoding="utf-8")

    @web.expose
    def add(a: int, b: int) -> int:
        return int(a) + int(b)

    web.pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
