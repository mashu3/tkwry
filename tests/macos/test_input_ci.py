"""Pinned macOS CI probe for keyboard ownership (no CGEvent).

GitHub Actions macOS runners cannot inject CGEvent / Accessibility clicks
reliably. This module is the **non-CGEvent** probe that stays on the
``tests/macos/`` CI job: API ``focus`` / ``focus_parent`` handoff timing plus
hit-test separation of chrome vs WebView.

Declared budgets (documented in ``docs/platforms.md``):

- CI gate: focus / focus_parent handoff ≤ 1000 ms (GHA VM)
- Local sample (``scripts/measure_macos_input.py``): focus median ~2 ms,
  focus_parent median ~10 ms, Tcl unfocus median ~1 ms

IME composition latency is **not** measured here (OS / first-responder
contract only — not Safari parity).
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pytest

from tkwry import WebView

if sys.platform == "darwin":
    from support.macos_input import activate_window, pump, wait_until, wry_point

# Generous VM budget — state the number even when GHA is slow (honesty over
# aspirational Safari-class latency). Local hardware is typically << 20 ms.
_CI_HANDOFF_BUDGET_S = 1.0


@pytest.fixture
def url_demo_layout(tk_root):
    tk_root.geometry("640x480")
    toolbar = ttk.Frame(tk_root)
    toolbar.pack(fill="x", padx=8, pady=(8, 0))
    url_entry = ttk.Entry(toolbar)
    url_entry.pack(side="left", fill="x", expand=True)
    web_frame = tk.Frame(tk_root, bg="#1e1e1e")
    web_frame.pack(fill="both", expand=True, padx=8, pady=8)
    tk_root.update_idletasks()
    return SimpleNamespace(
        root=tk_root,
        toolbar=toolbar,
        url_entry=url_entry,
        web_frame=web_frame,
    )


def _wait_native(web: WebView, root: tk.Misc) -> None:
    assert wait_until(root, lambda: web.native is not None), (
        "native WebView not created"
    )


def _until_active(web: WebView, root: tk.Misc, *, want: bool) -> float:
    start = time.monotonic()
    ok = wait_until(
        root,
        lambda: web.native is not None and web.native.mac_web_input_active() is want,
        timeout=_CI_HANDOFF_BUDGET_S,
    )
    elapsed = time.monotonic() - start
    assert ok, (
        f"mac_web_input_active() did not become {want} within "
        f"{_CI_HANDOFF_BUDGET_S * 1000:.0f}ms (elapsed {elapsed * 1000:.0f}ms)"
    )
    return elapsed


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_ci_keyboard_ownership_handoff_within_budget(url_demo_layout) -> None:
    """Pinned CI probe: API focus ownership handoff without CGEvent."""
    web = WebView(url_demo_layout.web_frame, html="<p>ci-probe</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        activate_window(url_demo_layout.root)
        native = web.native
        assert native is not None

        url_demo_layout.url_entry.focus_force()
        url_demo_layout.root.update()
        web.focus_parent()
        pump(url_demo_layout.root, seconds=0.05)
        assert not native.mac_web_input_active()

        web.focus()
        activate_s = _until_active(web, url_demo_layout.root, want=True)
        assert activate_s <= _CI_HANDOFF_BUDGET_S

        web.focus_parent()
        resign_s = _until_active(web, url_demo_layout.root, want=False)
        assert resign_s <= _CI_HANDOFF_BUDGET_S

        # Surface the sample in CI logs (budget is the gate; this is the measure).
        print(
            f"macOS input handoff sample: "
            f"focus={activate_s * 1000:.1f}ms "
            f"focus_parent={resign_s * 1000:.1f}ms "
            f"(budget={_CI_HANDOFF_BUDGET_S * 1000:.0f}ms)",
            flush=True,
        )
    finally:
        web.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_ci_hit_test_separates_chrome_from_web(url_demo_layout) -> None:
    """Pinned CI probe: chrome Entry vs WebView hit-test (no CGEvent)."""
    web = WebView(url_demo_layout.web_frame, html="<p>hit</p>")
    try:
        _wait_native(web, url_demo_layout.root)
        native = web.native
        assert native is not None
        root = url_demo_layout.root

        ex, ey = wry_point(root, url_demo_layout.url_entry)
        wx, wy = wry_point(root, url_demo_layout.web_frame)
        assert not native.mac_hit_test_wry_point(ex, ey), (
            f"URL bar ({ex:.0f},{ey:.0f}) must not hit webview"
        )
        assert native.mac_hit_test_wry_point(wx, wy), (
            f"web frame ({wx:.0f},{wy:.0f}) must hit webview"
        )
    finally:
        web.destroy()
