"""Demo: RPC (``expose`` / ``call``), worker handlers, and Python→JS ``emit``.

- IPC = fire-and-forget JS → Python (``window.ipc.postMessage``)
- RPC = request/response (``window.tkwry.call``)
- Emit = fire-and-forget Python → JS (``web.emit`` / ``window.tkwry.on``)
"""

from __future__ import annotations

import tempfile
import threading
import time
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
      margin-top: 0.75rem; margin-right: 0.5rem; border: 0; border-radius: 0.5rem;
      padding: 0.55rem 0.9rem; background: #5b9cff; color: #061018;
      font-weight: 600; cursor: pointer;
    }
    #out { margin-top: 1rem; opacity: 0.9; white-space: pre-wrap; }
  </style>
</head>
<body>
  <main>
    <h1>tkwry.call / emit</h1>
    <p>JS awaits Python; Python can push events back.</p>
    <button id="go" type="button">call read_file</button>
    <button id="sum" type="button">call add(2, 3)</button>
    <button id="heavy" type="button">call heavy (worker)</button>
    <pre id="out">ready</pre>
  </main>
  <script>
    var out = document.getElementById("out");
    function show(text) { out.textContent = text; }
    function boot() {
      if (!window.tkwry || !window.tkwry.on) return;
      window.tkwry.on("tick", function (payload) {
        show("emit tick: " + JSON.stringify(payload));
      });
    }
    boot();
    setInterval(boot, 100);
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
    document.getElementById("heavy").onclick = async function () {
      try {
        show("running heavy…");
        var n = await window.tkwry.call("heavy", { timeout: 10000 });
        show("heavy done on thread " + n);
      } catch (e) {
        show(e.name + ": " + e.message);
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
    root.geometry("520x400")
    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)

    web = WebView(frame, html=HTML, rpc_traceback=True)

    @web.expose
    def read_file() -> str:
        return sample.read_text(encoding="utf-8")

    @web.expose
    def add(a: int, b: int) -> int:
        return int(a) + int(b)

    @web.expose(thread=True, timeout=15.0)
    def heavy() -> int:
        time.sleep(0.4)
        return threading.get_ident()

    def _tick(count: list[int] = [0]) -> None:  # noqa: B006
        if web.destroyed:
            return
        count[0] += 1
        if web.ready:
            web.emit("tick", {"n": count[0]})
        root.after(2000, _tick)

    web.pack(fill="both", expand=True)
    root.after(1500, _tick)
    root.mainloop()


if __name__ == "__main__":
    main()
