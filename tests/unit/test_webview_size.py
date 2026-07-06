"""Tests for WebView creation-size heuristics (no native WebView required)."""

from __future__ import annotations

import tkinter as tk

from tkwry import WebView


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
