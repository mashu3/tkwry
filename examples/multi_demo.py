"""Demo: multiple WebViews with pack, grid, place, and PanedWindow layouts."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from tkwry import WebView

# Distinct pane colours so mis-aligned embeds are obvious at a glance.
PANES = (
    ("Pane A", "#2563eb", "A"),
    ("Pane B", "#7c3aed", "B"),
    ("Pane C", "#059669", "C"),
    ("Pane D", "#d97706", "D"),
)


def pane_html(title: str, accent: str, tag: str, layout: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: system-ui, sans-serif;
      background: #141414;
      color: #f3f4f6;
      display: grid;
      place-items: center;
      text-align: center;
    }}
    .badge {{
      display: inline-block;
      font-size: 3rem;
      font-weight: 800;
      width: 4.5rem;
      height: 4.5rem;
      line-height: 4.5rem;
      border-radius: 1rem;
      background: {accent};
      margin-bottom: 12px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 1.2rem; }}
    p {{ margin: 0; color: #9ca3af; font-size: 0.9rem; }}
    code {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <div>
    <div class="badge">{tag}</div>
    <h1>{title}</h1>
    <p>layout: <code>{layout}</code></p>
  </div>
</body>
</html>
"""


def make_pane(
    parent: tk.Misc,
    title: str,
    accent: str,
    tag: str,
    layout: str,
) -> tuple[ttk.Frame, WebView]:
    """Return a labelled container and its WebView."""
    container = ttk.Frame(parent, padding=4)
    ttk.Label(container, text=f"{title}  [{layout}]", font=("", 10, "bold")).pack(
        anchor="w", pady=(0, 4)
    )
    frame = tk.Frame(
        container,
        bg="#0d0d0d",
        highlightthickness=1,
        highlightbackground="#404040",
    )
    frame.pack(fill="both", expand=True)
    web = WebView(frame, html=pane_html(title, accent, tag, layout))
    return container, web


def build_pack_horizontal(parent: ttk.Frame, webs: list[WebView]) -> None:
    row = ttk.Frame(parent)
    row.pack(fill="both", expand=True, padx=4, pady=4)
    for title, accent, tag in PANES[:2]:
        container, web = make_pane(row, title, accent, tag, "pack (side=left)")
        container.pack(side="left", fill="both", expand=True, padx=4)
        webs.append(web)


def build_pack_vertical(parent: ttk.Frame, webs: list[WebView]) -> None:
    col = ttk.Frame(parent)
    col.pack(fill="both", expand=True, padx=4, pady=4)
    for title, accent, tag in PANES[:2]:
        container, web = make_pane(col, title, accent, tag, "pack (side=top)")
        container.pack(side="top", fill="both", expand=True, pady=4)
        webs.append(web)


def build_grid(parent: ttk.Frame, webs: list[WebView]) -> None:
    grid = ttk.Frame(parent)
    grid.pack(fill="both", expand=True, padx=4, pady=4)
    for row in range(2):
        grid.rowconfigure(row, weight=1)
    for col in range(2):
        grid.columnconfigure(col, weight=1)

    for index, (title, accent, tag) in enumerate(PANES):
        container, web = make_pane(
            grid, title, accent, tag, f"grid (row={index // 2}, col={index % 2})"
        )
        container.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
        webs.append(web)


def build_place(parent: ttk.Frame, webs: list[WebView]) -> None:
    ttk.Label(
        parent,
        text="place: top-left 48% + bottom-right 48% (overlap gap intentional)",
        foreground="#666",
    ).pack(anchor="w", padx=8, pady=(8, 0))

    canvas = tk.Frame(parent, bg="#2a2a2a")
    canvas.pack(fill="both", expand=True, padx=8, pady=8)

    specs = (
        (PANES[0], "place (relx=0, rely=0, 48%)", 0.0, 0.0),
        (PANES[1], "place (relx=0.52, rely=0.52, 48%)", 0.52, 0.52),
    )
    for (title, accent, tag), layout, rx, ry in specs:
        container, web = make_pane(canvas, title, accent, tag, layout)
        container.place(relx=rx, rely=ry, relwidth=0.48, relheight=0.48)
        webs.append(web)


def build_nested(parent: ttk.Frame, webs: list[WebView]) -> None:
    """Toolbar (pack) + 2-column body (grid) — typical app shell."""
    ttk.Label(
        parent,
        text=(
            "Without row/column weight in grid, child Frames stay 1px tall "
            "and WebViews stay hidden"
        ),
        foreground="#666",
        wraplength=900,
    ).pack(anchor="w", padx=8, pady=(8, 0))

    toolbar = ttk.Frame(parent, padding=(8, 8, 8, 0))
    toolbar.pack(fill="x")
    ttk.Label(toolbar, text="Nested: pack toolbar + grid body").pack(side="left")
    ttk.Button(toolbar, text="Stub action").pack(side="right")

    body = ttk.Frame(parent, padding=8)
    body.pack(fill="both", expand=True)
    body.rowconfigure(0, weight=1)
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=2)

    sidebar = ttk.Frame(body, padding=4)
    sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    sidebar_container, sidebar_web = make_pane(
        sidebar, "Sidebar", PANES[2][1], PANES[2][2], "grid sidebar (weight=1)"
    )
    sidebar_container.pack(fill="both", expand=True)
    webs.append(sidebar_web)

    main = ttk.Frame(body, padding=4)
    main.grid(row=0, column=1, sticky="nsew")
    main_container, main_web = make_pane(
        main, "Main", PANES[0][1], PANES[0][2], "grid main (weight=2)"
    )
    main_container.pack(fill="both", expand=True)
    webs.append(main_web)


def build_paned(parent: ttk.Frame, webs: list[WebView]) -> None:
    ttk.Label(
        parent,
        text="PanedWindow: drag the sash to resize",
        foreground="#666",
    ).pack(anchor="w", padx=8, pady=(8, 0))

    holder = ttk.Frame(parent, padding=8)
    holder.pack(fill="both", expand=True)

    paned = tk.PanedWindow(holder, orient=tk.HORIZONTAL, sashwidth=6, bg="#404040")
    paned.pack(fill="both", expand=True)

    for title, accent, tag in PANES[:2]:
        pane = tk.Frame(paned, bg="#0d0d0d")
        paned.add(pane, minsize=160, stretch="always")
        web = WebView(pane, html=pane_html(title, accent, tag, "PanedWindow"))
        webs.append(web)


def build_place_absolute(parent: ttk.Frame, webs: list[WebView]) -> None:
    """Fixed-pixel place — useful for checking resize behaviour."""
    ttk.Label(
        parent,
        text="place: absolute pixels (resize window to test Configure sync)",
        foreground="#666",
    ).pack(anchor="w", padx=8, pady=(8, 0))

    canvas = tk.Frame(parent, bg="#2a2a2a", height=360)
    canvas.pack(fill="both", expand=True, padx=8, pady=8)
    canvas.pack_propagate(False)

    specs = (
        (PANES[2], "place (x=8, y=8, 200×140)", 8, 8, 200, 140),
        (PANES[3], "place (x=220, y=8, 200×140)", 220, 8, 200, 140),
        (PANES[0], "place (x=8, y=160, 412×180)", 8, 160, 412, 180),
    )
    for (title, accent, tag), layout, x, y, w, h in specs:
        container, web = make_pane(canvas, title, accent, tag, layout)
        container.place(x=x, y=y, width=w, height=h)
        webs.append(web)


TABS: list[tuple[str, Callable[[ttk.Frame, list[WebView]], None]]] = [
    ("pack ↔", build_pack_horizontal),
    ("pack ↕", build_pack_vertical),
    ("grid 2×2", build_grid),
    ("place %", build_place),
    ("place px", build_place_absolute),
    ("nested", build_nested),
    ("PanedWindow", build_paned),
]


def main() -> None:
    root = tk.Tk()
    root.title("tkwry multi WebView layout demo")
    root.geometry("1024x720")
    root.minsize(800, 560)

    header = ttk.Frame(root, padding=(12, 10, 12, 0))
    header.pack(fill="x")
    ttk.Label(header, text="Multiple WebViews", font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(
        header,
        text=(
            "Switch tabs to exercise pack, grid, place, nested, "
            "and PanedWindow layouts."
        ),
        foreground="#555",
    ).pack(anchor="w")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    tab_webs: list[list[WebView]] = []

    for tab_name, builder in TABS:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text=tab_name)
        webs: list[WebView] = []
        builder(tab, webs)
        tab_webs.append(webs)

    status = tk.StringVar(
        value="Select a tab and resize the window to test bounds sync."
    )

    def refresh_status() -> None:
        index = notebook.index(notebook.select())
        name = TABS[index][0]
        webs = tab_webs[index]
        lines = [f"Tab: {name}  |  panes: {len(webs)}"]
        for web in webs:
            frame = web._frame  # demo-only introspection
            lines.append(
                f"  {frame.winfo_width()}×{frame.winfo_height()} @ "
                f"({frame.winfo_x()}, {frame.winfo_y()})"
            )
        status.set("\n".join(lines))

    def on_tab_changed(_event: tk.Event) -> None:
        root.update_idletasks()
        root.after(150, refresh_status)

    notebook.bind("<<NotebookTabChanged>>", on_tab_changed)

    footer = ttk.Frame(root, padding=(12, 4, 12, 10))
    footer.pack(fill="x")
    ttk.Label(footer, textvariable=status, justify="left", foreground="#444").pack(
        anchor="w"
    )
    ttk.Button(footer, text="Refresh geometry", command=refresh_status).pack(
        anchor="e", pady=(4, 0)
    )

    root.bind("<Configure>", lambda _e: root.after_idle(refresh_status))
    root.after(600, refresh_status)
    root.mainloop()


if __name__ == "__main__":
    main()
