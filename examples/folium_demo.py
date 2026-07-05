"""Demo: interactive Folium maps embedded in Tkinter via tkwry."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import folium
from folium import Element

from tkwry import WebView

LOCATIONS: dict[str, tuple[float, float]] = {
    "Tokyo": (35.689487, 139.691706),
    "New York": (40.712772, -74.006058),
    "London": (51.50750, 0.01611),
    "Paris": (48.856373, 2.353016),
    "Sydney": (-33.873210, 151.206208),
}


def build_map_html(city: str, zoom: int) -> str:
    lat, lon = LOCATIONS[city]
    m = folium.Map(
        location=[lat, lon],
        zoom_start=max(3, min(zoom, 18)),
        control_scale=True,
    )

    for name, (mlat, mlon) in LOCATIONS.items():
        folium.Marker(
            [mlat, mlon],
            popup=name,
            tooltip=name,
            icon=folium.Icon(color="red" if name == city else "blue"),
        ).add_to(m)

    map_name = m.get_name()
    m.get_root().html.add_child(
        Element(
            f"""
<script>
document.addEventListener("DOMContentLoaded", function () {{
  window._tkwryMap = {map_name};
  {map_name}.on("contextmenu", function (e) {{
    L.DomEvent.preventDefault(e);
    L.marker(e.latlng).addTo({map_name});
  }});
}});
</script>
"""
        )
    )

    return m.get_root().render()


def main() -> None:
    root = tk.Tk()
    root.title("tkwry Folium demo")
    root.geometry("1100x720")
    root.minsize(900, 560)

    panel = ttk.Frame(root, padding=16, width=260)
    panel.pack(side="left", fill="y")
    panel.pack_propagate(False)

    ttk.Label(panel, text="Map controls", font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(
        panel,
        text="Tkinter reloads the Folium map.\nRight-click the map to drop a pin.",
        wraplength=220,
        foreground="#555",
    ).pack(anchor="w", pady=(4, 16))

    city_var = tk.StringVar(value="Tokyo")
    ttk.Label(panel, text="Center city").pack(anchor="w")
    city_box = ttk.Combobox(
        panel,
        textvariable=city_var,
        values=tuple(LOCATIONS),
        state="readonly",
    )
    city_box.pack(fill="x", pady=(0, 12))

    zoom_var = tk.IntVar(value=11)
    ttk.Label(panel, text="Zoom").pack(anchor="w")
    zoom_scale = ttk.Scale(
        panel,
        from_=3,
        to=16,
        orient="horizontal",
        variable=zoom_var,
    )
    zoom_scale.pack(fill="x", pady=(0, 4))
    ttk.Label(panel, textvariable=zoom_var, foreground="#555").pack(
        anchor="e", pady=(0, 16)
    )

    web_frame = tk.Frame(root, bg="#1a1a1a")
    web_frame.pack(side="right", fill="both", expand=True, padx=(0, 8), pady=8)

    web = WebView(web_frame, html="<p>Loading map…</p>")

    def push_map() -> None:
        html = build_map_html(city_var.get(), zoom_var.get())
        web.load_html(html)

    ttk.Button(panel, text="Update map", command=push_map).pack(fill="x", pady=(0, 8))

    city_box.bind("<<ComboboxSelected>>", lambda _e: push_map())
    zoom_scale.bind("<ButtonRelease-1>", lambda _e: push_map())

    root.after(800, push_map)
    root.mainloop()


if __name__ == "__main__":
    main()
