"""Demo: interactive Plotly charts embedded in Tkinter via tkwry."""

from __future__ import annotations

import json
import math
import random
import tkinter as tk
from tkinter import ttk

import plotly.graph_objects as go

from tkwry import WebView

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; background: #141414; color: #e5e7eb; }
    body {
      font-family: system-ui, sans-serif;
      display: flex;
      flex-direction: column;
      min-height: 100%;
    }
    #plot {
      flex: 1;
      min-height: 0;
      width: 100%;
    }
    #status {
      padding: 8px 12px;
      font-size: 0.85rem;
      color: #9ca3af;
      border-top: 1px solid #2d2d2d;
      background: #1a1a1a;
    }
  </style>
</head>
<body>
  <div id="plot"></div>
  <div id="status">Click a point on the chart to send data back to Tkinter.</div>
  <script>
    window.renderPlot = function (fig) {
      var cfg = Object.assign(
        { responsive: true, displayModeBar: true },
        fig.config || {}
      );
      return Plotly.react("plot", fig.data, fig.layout, cfg).then(function () {
        window.installClickHandler();
      });
    };
    window.installClickHandler = function () {
      var el = document.getElementById("plot");
      if (!el || el._tkwryClickBound) return;
      el._tkwryClickBound = true;
      el.on("plotly_click", function (ev) {
        if (!ev.points || !ev.points.length) return;
        var p = ev.points[0];
        window.ipc.postMessage(JSON.stringify({
          action: "point_click",
          x: p.x,
          y: p.y,
          curve: p.curveNumber,
          label: p.data.name || ""
        }));
      });
    };
    window.setStatus = function (text) {
      document.getElementById("status").textContent = text;
    };
  </script>
</body>
</html>
"""

CHART_KINDS = ("line", "bar", "scatter")


def build_figure(kind: str, points: int, *, dark: bool) -> go.Figure:
    n = max(3, min(points, 200))
    template = "plotly_dark" if dark else "plotly_white"
    layout = go.Layout(
        template=template,
        title=f"Plotly / Tkinter demo — {kind}",
        margin=dict(l=48, r=24, t=48, b=48),
        paper_bgcolor="#141414" if dark else "#ffffff",
        plot_bgcolor="#1e1e1e" if dark else "#f8fafc",
    )

    if kind == "line":
        xs = list(range(n))
        ys = [math.sin(i / 4) + random.uniform(-0.08, 0.08) for i in xs]
        trace = go.Scatter(x=xs, y=ys, mode="lines+markers", name="sin(x/4)")
    elif kind == "bar":
        xs = [f"Cat {i + 1}" for i in range(min(n, 12))]
        ys = [random.randint(5, 100) for _ in xs]
        trace = go.Bar(x=xs, y=ys, name="random")
    else:
        xs = [random.gauss(0, 1) for _ in range(n)]
        ys = [random.gauss(0, 1) for _ in range(n)]
        trace = go.Scatter(
            x=xs, y=ys, mode="markers", name="scatter", marker=dict(size=9)
        )

    return go.Figure(data=[trace], layout=layout)


def main() -> None:
    root = tk.Tk()
    root.title("tkwry Plotly demo")
    root.geometry("1100x720")
    root.minsize(900, 560)

    panel = ttk.Frame(root, padding=16, width=260)
    panel.pack(side="left", fill="y")
    panel.pack_propagate(False)

    ttk.Label(panel, text="Chart controls", font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(
        panel,
        text="Tkinter updates the Plotly figure.\nClick points to send IPC back.",
        wraplength=220,
        foreground="#555",
    ).pack(anchor="w", pady=(4, 16))

    kind_var = tk.StringVar(value="line")
    ttk.Label(panel, text="Chart type").pack(anchor="w")
    kind_box = ttk.Combobox(
        panel,
        textvariable=kind_var,
        values=CHART_KINDS,
        state="readonly",
    )
    kind_box.pack(fill="x", pady=(0, 12))

    points_var = tk.IntVar(value=24)
    ttk.Label(panel, text="Points").pack(anchor="w")
    points_scale = ttk.Scale(
        panel,
        from_=6,
        to=120,
        orient="horizontal",
        variable=points_var,
    )
    points_scale.pack(fill="x", pady=(0, 4))
    points_label = ttk.Label(panel, textvariable=points_var, foreground="#555")
    points_label.pack(anchor="e", pady=(0, 12))

    dark_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(panel, text="Dark theme", variable=dark_var).pack(
        anchor="w", pady=(0, 12)
    )

    ipc_status = tk.StringVar(value="No point selected yet.")
    ttk.Label(panel, text="Last click (from chart)").pack(anchor="w")
    ttk.Label(panel, textvariable=ipc_status, wraplength=220, foreground="#333").pack(
        anchor="w", pady=(0, 16)
    )

    web_frame = tk.Frame(root, bg="#141414")
    web_frame.pack(side="right", fill="both", expand=True, padx=(0, 8), pady=8)

    web: WebView

    def on_ipc(message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("action") != "point_click":
            return

        def show() -> None:
            x, y = data.get("x"), data.get("y")
            label = data.get("label") or f"series {data.get('curve', 0)}"
            ipc_status.set(f"{label}: x={x!r}, y={y!r}")
            msg = json.dumps(f"Tk received click: {label}")
            web.eval_js(f"window.setStatus({msg});")

        root.after_idle(show)

    web = WebView(web_frame, html=HTML, ipc_handler=on_ipc)

    def push_figure() -> None:
        fig = build_figure(kind_var.get(), points_var.get(), dark=dark_var.get())
        payload = json.dumps(fig.to_plotly_json())
        # eval_js coalesces rapid calls (last-wins); one script for render + status.
        web.eval_js(
            f"window.renderPlot({payload}).then(function () {{"
            'window.setStatus("Chart updated from Tkinter.");'
            "});"
        )

    btn_row = ttk.Frame(panel)
    btn_row.pack(fill="x", pady=(0, 8))
    ttk.Button(btn_row, text="Update chart", command=push_figure).pack(
        side="left", fill="x", expand=True
    )

    def randomize_points() -> None:
        points_var.set(random.randint(8, 80))
        push_figure()

    ttk.Button(btn_row, text="Randomize", command=randomize_points).pack(
        side="left", padx=(8, 0)
    )

    kind_box.bind("<<ComboboxSelected>>", lambda _e: push_figure())
    points_scale.bind("<ButtonRelease-1>", lambda _e: push_figure())
    dark_var.trace_add("write", lambda *_: push_figure())

    web.when_ready(push_figure)
    root.mainloop()


if __name__ == "__main__":
    main()
