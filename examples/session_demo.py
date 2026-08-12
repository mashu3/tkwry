"""Demo: share a WebSession (cookies / storage profile) across two WebViews."""

from __future__ import annotations

import tempfile
import tkinter as tk
from pathlib import Path

from tkwry import WebSession, WebView

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font: 15px/1.45 ui-sans-serif, system-ui, sans-serif;
      background: #14181f; color: #e8eef7;
    }
    main { width: min(22rem, calc(100% - 1.5rem)); }
    input, button {
      margin-top: 0.5rem; width: 100%; box-sizing: border-box;
      border-radius: 0.45rem; border: 0; padding: 0.45rem 0.65rem;
    }
    button { background: #5b9cff; color: #061018; font-weight: 600; cursor: pointer; }
    #out { margin-top: 0.75rem; opacity: 0.9; white-space: pre-wrap; }
  </style>
</head>
<body>
  <main>
    <h1>session storage</h1>
    <p>Write in one pane, read in the other (same WebSession).</p>
    <input id="val" type="text" value="hello from pane" />
    <button id="write" type="button">localStorage.setItem</button>
    <button id="read" type="button">localStorage.getItem</button>
    <pre id="out">ready</pre>
  </main>
  <script>
    var KEY = "tkwry.session.demo";
    var out = document.getElementById("out");
    document.getElementById("write").onclick = function () {
      var v = document.getElementById("val").value;
      localStorage.setItem(KEY, v);
      out.textContent = "wrote: " + v;
    };
    document.getElementById("read").onclick = function () {
      out.textContent = "read: " + (localStorage.getItem(KEY) || "(empty)");
    };
  </script>
</body>
</html>
"""


def main() -> None:
    profile = Path(tempfile.mkdtemp(prefix="tkwry-session-"))
    session = WebSession(data_directory=profile)

    root = tk.Tk()
    root.title("tkwry WebSession demo")
    root.geometry("900x420")

    left = tk.Frame(root)
    right = tk.Frame(root)
    left.pack(side="left", fill="both", expand=True)
    right.pack(side="left", fill="both", expand=True)

    WebView(left, html=HTML, session=session, width=440, height=400).pack(
        fill="both", expand=True
    )
    WebView(right, html=HTML, session=session, width=440, height=400).pack(
        fill="both", expand=True
    )

    root.mainloop()


if __name__ == "__main__":
    main()
