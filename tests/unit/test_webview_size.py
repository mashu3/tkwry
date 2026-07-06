"""Tests for WebView creation-size heuristics (no native WebView required)."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _isolate_from_native_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """Size heuristics only — never build WebKitGTK in headless Linux CI."""
    monkeypatch.setattr("tkwry._runtime.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr("tkwry._core.pump_events", lambda: None, raising=False)
    monkeypatch.setattr(WebView, "_try_create", lambda self: None)


def test_partial_explicit_width_does_not_default_height(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)

    assert web._init_width == 300
    assert web._init_height is None


def test_partial_explicit_height_does_not_default_width(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, height=240)

    assert web._init_width is None
    assert web._init_height == 240


def test_creation_size_waits_for_missing_frame_height(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() is None


def test_creation_size_uses_known_frame_width_with_explicit_height(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, height=240)
    monkeypatch.setattr(frame, "winfo_width", lambda: 500)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() == (500, 240)


def test_creation_size_uses_known_frame_height_with_explicit_width(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 200)

    assert web._creation_size() == (300, 200)


def test_creation_size_uses_both_explicit_dimensions_before_layout(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=800, height=600)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() == (800, 600)


def test_creation_size_prefers_laid_out_frame(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=240)
    monkeypatch.setattr(frame, "winfo_width", lambda: 640)
    monkeypatch.setattr(frame, "winfo_height", lambda: 480)

    assert web._creation_size() == (640, 480)


def test_layout_ready_false_before_frame_geometry(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is False


def test_layout_ready_true_when_frame_is_laid_out(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is True


def test_sync_bounds_uses_explicit_size_before_layout(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    bounds: list[tuple[float, float, float, float]] = []

    class _Native:
        def set_bounds(self, x, y, width, height) -> None:
            bounds.append((x, y, width, height))

        def set_visible(self, _visible: bool) -> None:
            return None

        def destroy(self) -> None:
            return None

    web._webview = _Native()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)
    monkeypatch.setattr(frame, "update_idletasks", lambda: None)
    monkeypatch.setattr(
        "tkwry.webview.tk_embed_origin", lambda *_args, **_kwargs: (0, 0)
    )
    monkeypatch.setattr(web, "_frame_should_show", lambda: True)

    web._sync_bounds()
    assert bounds == [(0.0, 0.0, 300.0, 200.0)]
