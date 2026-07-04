"""Multiple WebViews in one Tk toplevel (pack/grid layouts)."""

from __future__ import annotations

import json
import sys

import pytest
from support.layout import attach_bounds_recorder, bounds_close, expected_bounds
from support.tk import pump, skip_linux_ci, wait_until

from tkwry import PageLoadEvent, WebView

pytestmark = skip_linux_ci


def _two_pane_row(root):
    import tkinter as tk

    root.geometry("820x420")
    row = tk.Frame(root)
    row.pack(fill="both", expand=True, padx=4, pady=4)

    left = tk.Frame(row, width=380, height=300, bg="#111")
    right = tk.Frame(row, width=380, height=300, bg="#222")
    left.pack(side="left", fill="both", expand=True, padx=4)
    right.pack(side="right", fill="both", expand=True, padx=4)
    left.pack_propagate(False)
    right.pack_propagate(False)
    root.update_idletasks()
    return row, left, right


def test_two_webviews_both_ready_and_independent_eval(tk_root) -> None:
    row, left, right = _two_pane_row(tk_root)

    load_events: dict[str, list[PageLoadEvent]] = {"a": [], "b": []}
    web_a = WebView(
        left,
        html="<p id='pane-a'>A</p>",
        on_page_load=lambda evt, _url: load_events["a"].append(evt),
    )
    web_b = WebView(
        right,
        html="<p id='pane-b'>B</p>",
        on_page_load=lambda evt, _url: load_events["b"].append(evt),
    )

    assert wait_until(tk_root, lambda: web_a.ready and web_b.ready, steps=200)
    assert wait_until(
        tk_root,
        lambda: (
            any(evt == PageLoadEvent.Finished for evt in load_events["a"])
            and any(evt == PageLoadEvent.Finished for evt in load_events["b"])
        ),
        steps=300,
    )
    pump(tk_root, steps=30)
    assert web_a.native is not None and web_b.native is not None
    assert web_a.native is not web_b.native

    results: dict[str, str] = {}

    web_a.eval_js_with_callback(
        "document.getElementById('pane-a').textContent",
        lambda value: results.update({"a": value}),
    )
    web_b.eval_js_with_callback(
        "document.getElementById('pane-b').textContent",
        lambda value: results.update({"b": value}),
    )

    def both_evaluated() -> bool:
        try:
            return json.loads(results["a"]) == "A" and json.loads(results["b"]) == "B"
        except KeyError:
            return False

    assert wait_until(tk_root, both_evaluated, steps=200), (
        f"expected independent eval results, got {results!r}"
    )

    web_a.destroy()
    web_b.destroy()
    row.destroy()


def test_two_webviews_sync_bounds_independently(tk_root) -> None:
    row, left, right = _two_pane_row(tk_root)

    web_a = WebView(left, html="<p>a</p>")
    web_b = WebView(right, html="<p>b</p>")

    assert wait_until(tk_root, lambda: web_a.native and web_b.native, steps=200)

    records_a = attach_bounds_recorder(web_a)
    records_b = attach_bounds_recorder(web_b)
    web_a._sync_bounds()
    web_b._sync_bounds()
    pump(tk_root, steps=40)

    expected_a = expected_bounds(left)
    expected_b = expected_bounds(right)
    assert left.winfo_width() > 100 and right.winfo_width() > 100
    assert bounds_close(records_a, expected_a), (
        f"pane A bounds mismatch: expected {expected_a}, records={records_a[-3:]}"
    )
    assert bounds_close(records_b, expected_b), (
        f"pane B bounds mismatch: expected {expected_b}, records={records_b[-3:]}"
    )

    web_a.destroy()
    web_b.destroy()
    row.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="WebKitGTK headless CI: grid layout timing unreliable",
)
def test_grid_four_webviews_all_ready(tk_root) -> None:
    import tkinter as tk

    tk_root.geometry("720x520")
    grid = tk.Frame(tk_root)
    grid.pack(fill="both", expand=True, padx=4, pady=4)
    for row in range(2):
        grid.rowconfigure(row, weight=1)
    for col in range(2):
        grid.columnconfigure(col, weight=1)

    webs: list[WebView] = []
    for index in range(4):
        pane = tk.Frame(grid, bg="#0d0d0d")
        pane.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
        webs.append(WebView(pane, html=f"<p id='w{index}'>{index}</p>"))

    assert wait_until(
        tk_root,
        lambda: all(web.ready for web in webs),
        steps=300,
    ), "expected all four WebViews to become ready"

    for web in webs:
        assert web.native is not None

    for web in webs:
        web.destroy()
    grid.destroy()
