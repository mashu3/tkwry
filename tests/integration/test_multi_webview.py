"""Multiple WebViews in one Tk toplevel (pack/grid layouts)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from support.layout import attach_bounds_recorder, bounds_close, expected_bounds
from support.tk import pump, skip_linux_ci, wait_until

from tkwry import PageLoadEvent, WebView


def _two_pane_row(root):
    import tkinter as tk

    root.geometry("820x420")
    root.update_idletasks()
    root.update()
    row = tk.Frame(root)
    row.pack(fill="both", expand=True, padx=4, pady=4)
    row.columnconfigure(0, weight=1, minsize=200)
    row.columnconfigure(1, weight=1, minsize=200)
    row.rowconfigure(0, weight=1, minsize=200)

    left = tk.Frame(row, width=380, height=300, bg="#111")
    right = tk.Frame(row, width=380, height=300, bg="#222")
    left.grid_propagate(False)
    right.grid_propagate(False)
    left.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
    right.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
    root.update_idletasks()
    root.update()
    # Ensure both hosts are mapped with real geometry before creating WebViews.
    # Xvfb: winfo_viewable() can stay false while geometry is valid.
    for _ in range(50):
        if (
            left.winfo_width() > 1
            and right.winfo_width() > 1
            and left.winfo_height() > 1
            and right.winfo_height() > 1
            and (
                sys.platform == "linux"
                or (left.winfo_viewable() and right.winfo_viewable())
            )
        ):
            break
        root.update_idletasks()
        root.update()
        root.after(20)
        root.update()
    return row, left, right


def _json_text(raw: str) -> str | None:
    """Decode eval_js JSON; WebKitGTK may double-encode string results."""
    try:
        data = json.loads(raw)
        if isinstance(data, str):
            try:
                nested = json.loads(data)
                if isinstance(nested, str):
                    return nested
            except (json.JSONDecodeError, TypeError):
                pass
            return data
        return None
    except (json.JSONDecodeError, TypeError):
        return None


@skip_linux_ci
def test_two_webviews_both_ready_and_independent_eval(tk_root) -> None:
    """Two panes ready + independent eval (skipped on Linux CI / GITHUB_ACTIONS).

    Dual WebViews with page_load_listening stall ``pump_events`` under Xvfb.
    """
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

    assert wait_until(tk_root, lambda: web_a.ready and web_b.ready, steps=200), (
        f"expected both panes ready; "
        f"sizes=({left.winfo_width()}x{left.winfo_height()}, "
        f"{right.winfo_width()}x{right.winfo_height()})"
    )
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

    # Prefer sequential evals — concurrent callbacks can stall WebKitGTK.
    web_a.eval_js_with_callback(
        "document.getElementById('pane-a').textContent",
        lambda value: results.update({"a": value}),
    )
    assert wait_until(
        tk_root, lambda: _json_text(results.get("a", "")) == "A", steps=200
    ), f"expected pane A eval, got {results!r}"

    web_b.eval_js_with_callback(
        "document.getElementById('pane-b').textContent",
        lambda value: results.update({"b": value}),
    )
    assert wait_until(
        tk_root, lambda: _json_text(results.get("b", "")) == "B", steps=200
    ), f"expected pane B eval, got {results!r}"

    web_a.destroy()
    web_b.destroy()
    row.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_two_webviews_url_independent(tk_root, tmp_path: Path) -> None:
    row, left, right = _two_pane_row(tk_root)

    page_a = tmp_path / "pane-a.html"
    page_b = tmp_path / "pane-b.html"
    page_a.write_text("<p>a</p>", encoding="utf-8")
    page_b.write_text("<p>b</p>", encoding="utf-8")

    web_a = WebView(left, html="<p>a</p>")
    web_b = WebView(right, html="<p>b</p>")

    assert wait_until(tk_root, lambda: web_a.ready and web_b.ready, steps=200)

    web_a.load_url(str(page_a))
    web_b.load_url(str(page_b))
    pump(tk_root, steps=60)

    assert web_a.native is not None and web_b.native is not None

    def urls_match() -> bool:
        try:
            return (
                web_a.url == page_a.absolute().as_uri()
                and web_b.url == page_b.absolute().as_uri()
            )
        except Exception:
            return False

    assert wait_until(tk_root, urls_match, steps=300), (
        f"expected independent document URLs, got {web_a.url!r} and {web_b.url!r}"
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
    reason="WebKitGTK headless CI only; macOS and Windows run this in CI",
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
