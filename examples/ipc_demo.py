"""Demo: JS ↔ Python bridge — IPC events, RPC calls, and ``emit``.

- **IPC** — fire-and-forget JS → Python (``window.ipc.postMessage``)
- **RPC** — request/response (``window.tkwry.call`` / ``@web.expose``)
- **Emit** — fire-and-forget Python → JS (``web.emit`` / ``window.tkwry.on``)
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from tkwry import WebView

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
      background: #1e1e1e;
      color: #e8e8e8;
      display: grid;
      place-items: center;
      transition: background 0.25s ease;
    }
    body.flash { background: #2d4a3e; }
    .card {
      width: min(440px, 92vw);
      padding: 28px;
      border-radius: 16px;
      background: #2a2a2a;
      border: 1px solid #3d3d3d;
      text-align: center;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
    }
    h1 { margin: 0 0 8px; font-size: 1.35rem; }
    p { margin: 0 0 16px; color: #a8a8a8; font-size: 0.95rem; }
    .count {
      font-size: 3rem;
      font-weight: 700;
      margin: 8px 0 16px;
      color: #7ee787;
    }
    .label {
      margin: 16px 0 8px;
      color: #9ca3af;
      font-size: 0.75rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .row { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
    button {
      border: none;
      border-radius: 10px;
      padding: 10px 16px;
      font-size: 0.95rem;
      cursor: pointer;
      background: #3b82f6;
      color: white;
    }
    button.secondary { background: #4b5563; }
    button.accent { background: #a855f7; }
    button.rpc { background: #5b9cff; color: #061018; font-weight: 600; }
    button:active { transform: translateY(1px); }
    #log {
      margin-top: 20px;
      padding: 12px;
      border-radius: 10px;
      background: #1a1a1a;
      color: #9ca3af;
      font-size: 0.85rem;
      min-height: 2.5em;
      text-align: left;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>JavaScript side</h1>
    <p>IPC posts events; RPC awaits a result; Python pushes with emit.</p>
    <div class="count" id="count">0</div>
    <div class="label">IPC — postMessage</div>
    <div class="row">
      <button onclick="send('increment')">+1 from JS</button>
      <button onclick="send('decrement')">−1 from JS</button>
    </div>
    <div class="row" style="margin-top: 12px">
      <button class="secondary" onclick="send('notify')">Notify Tk</button>
      <button class="accent" onclick="send('color')">Tint Tk panel</button>
    </div>
    <div class="label">RPC — tkwry.call</div>
    <div class="row">
      <button class="rpc" id="sum" type="button">add(2, 3)</button>
      <button class="rpc" id="greet" type="button">greet kwargs</button>
      <button class="rpc" id="heavy" type="button">heavy (worker)</button>
    </div>
    <div id="log">Waiting for messages…</div>
  </div>
  <script>
    function send(action) {
      window.ipc.postMessage(JSON.stringify({ action: action }));
    }
    function show(text) {
      document.getElementById("log").textContent = text;
    }
    function boot() {
      if (!window.tkwry || !window.tkwry.on) return;
      if (window._tkwryBooted) return;
      window._tkwryBooted = true;
      window.tkwry.on("count", function (n) {
        document.getElementById("count").textContent = String(n);
      });
      window.tkwry.on("log", function (text) {
        show(String(text));
      });
      window.tkwry.on("flash", function () {
        document.body.classList.add("flash");
        setTimeout(function () { document.body.classList.remove("flash"); }, 400);
      });
    }
    boot();
    setInterval(boot, 100);
    document.getElementById("sum").onclick = async function () {
      try {
        var n = await window.tkwry.call("add", 2, 3);
        show("RPC add(2, 3) = " + n);
      } catch (e) {
        show(String(e));
      }
    };
    document.getElementById("greet").onclick = async function () {
      try {
        var text = await window.tkwry.call("greet", "hi", { kwargs: { times: 3 } });
        show("RPC greet = " + text);
      } catch (e) {
        show(String(e));
      }
    };
    document.getElementById("heavy").onclick = async function () {
      try {
        show("RPC heavy running…");
        var n = await window.tkwry.call("heavy", { timeout: 10000 });
        show("RPC heavy done on thread " + n);
      } catch (e) {
        show(e.name + ": " + e.message);
      }
    };
  </script>
</body>
</html>
"""


def main() -> None:
    root = tk.Tk()
    root.title("tkwry IPC / RPC demo")
    root.geometry("960x640")
    root.minsize(720, 480)

    style = ttk.Style()
    style.configure("Panel.TFrame", background="#f5f5f5")

    panel = ttk.Frame(root, style="Panel.TFrame", padding=16, width=240)
    panel.pack(side="left", fill="y")
    panel.pack_propagate(False)

    ttk.Label(panel, text="Tkinter side", font=("", 16, "bold")).pack(anchor="w")
    ttk.Label(
        panel,
        text="IPC events update this panel.\nRPC returns values to JS.\n"
        "Emit pushes count / log / flash.",
        wraplength=200,
    ).pack(anchor="w", pady=(4, 16))

    counter = tk.IntVar(value=0)
    ttk.Label(panel, textvariable=counter, font=("", 36, "bold")).pack(pady=(0, 12))

    status = tk.StringVar(value="Ready")
    ttk.Label(panel, textvariable=status, wraplength=200, foreground="#555").pack(
        anchor="w", pady=(0, 16)
    )

    btn_row = ttk.Frame(panel)
    btn_row.pack(fill="x", pady=(0, 8))
    web_frame = tk.Frame(root, bg="#1e1e1e")
    web_frame.pack(side="right", fill="both", expand=True, padx=(0, 8), pady=8)

    web: WebView

    def push_to_js(*, log: str | None = None) -> None:
        n = counter.get()
        if web.destroyed or not web.ready:
            return
        web.emit("count", n)
        web.emit("log", log if log is not None else f"count = {n}")

    def on_ipc(message: str) -> None:
        try:
            data = json.loads(message)
            action = data.get("action", "")
        except json.JSONDecodeError:
            status.set(f"Bad JSON: {message[:40]}")
            return

        if action == "increment":
            counter.set(counter.get() + 1)
            status.set("JS incremented counter")
            push_to_js(log="JS IPC increment")
        elif action == "decrement":
            counter.set(counter.get() - 1)
            status.set("JS decremented counter")
            push_to_js(log="JS IPC decrement")
        elif action == "notify":

            def show_notify() -> None:
                messagebox.showinfo(
                    "Message from JavaScript",
                    f"Hello from the WebView!\nCurrent count: {counter.get()}",
                    parent=root,
                )
                status.set("JS sent notify")
                web.emit("log", "Tk showed a dialog")

            root.after_idle(show_notify)
        elif action == "color":
            colors = ("#dbeafe", "#fce7f3", "#dcfce7", "#fef9c3", "#ede9fe")
            idx = counter.get() % len(colors)
            style.configure("Panel.TFrame", background=colors[idx])
            status.set(f"JS tinted panel ({colors[idx]})")
            web.emit("log", f"Tk panel → {colors[idx]}")
        else:
            status.set(f"Unknown action: {action}")

    web = WebView(web_frame, html=HTML, ipc_handler=on_ipc, rpc_traceback=True)

    @web.expose
    def add(a: int, b: int) -> int:
        return int(a) + int(b)

    @web.expose
    def greet(message: str, times: int = 1) -> str:
        return str(message) * int(times)

    @web.expose(thread=True, timeout=15.0)
    def heavy() -> int:
        time.sleep(0.4)
        return threading.get_ident()

    def tk_increment(delta: int) -> None:
        counter.set(counter.get() + delta)
        status.set("Tk changed counter")
        push_to_js()

    def tk_flash() -> None:
        if web.destroyed or not web.ready:
            return
        web.emit("flash")
        status.set("Tk flashed the page")

    ttk.Button(btn_row, text="−", width=3, command=lambda: tk_increment(-1)).pack(
        side="left", padx=(0, 4)
    )
    ttk.Button(btn_row, text="+", width=3, command=lambda: tk_increment(1)).pack(
        side="left", padx=(0, 4)
    )

    def reset_counter() -> None:
        counter.set(0)
        push_to_js()

    ttk.Button(btn_row, text="Reset", command=reset_counter).pack(side="left")

    ttk.Button(panel, text="Flash page from Tk", command=tk_flash).pack(
        fill="x", pady=(8, 0)
    )

    web.when_ready(push_to_js)
    root.mainloop()


if __name__ == "__main__":
    main()
