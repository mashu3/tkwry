"""Boundary-value and invalid-input validation tests."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import WebView
from tkwry.exceptions import WebViewDestroyedError


class TestBackgroundColorValidation:
    def test_valid_color_accepted(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        web = WebView(frame, background_color=(0, 128, 255, 255))
        assert web._background_color == (0, 128, 255, 255)
        web.destroy()
        frame.destroy()

    def test_rejects_value_above_255(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="0-255"):
            WebView(frame, background_color=(256, 0, 0, 0))
        frame.destroy()

    def test_rejects_negative_value(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="0-255"):
            WebView(frame, background_color=(0, -1, 0, 0))
        frame.destroy()

    def test_rejects_wrong_tuple_length(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="4 ints"):
            WebView(frame, background_color=(255, 255, 255))  # type: ignore[arg-type]
        frame.destroy()

    def test_rejects_non_int_component(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(TypeError, match="must be an int"):
            WebView(frame, background_color=(1.0, 0, 0, 0))  # type: ignore[arg-type]
        frame.destroy()

    def test_rejects_bool_component(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(TypeError, match="must be an int"):
            WebView(frame, background_color=(True, 0, 0, 255))  # type: ignore[arg-type]
        frame.destroy()

    def test_rejects_non_tuple(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="4 ints"):
            WebView(frame, background_color=[0, 0, 0, 0])  # type: ignore[arg-type]
        frame.destroy()


class TestConstructorUrlValidation:
    def test_rejects_invalid_url_at_construction(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="scheme"):
            WebView(frame, url="javascript:alert(1)")
        frame.destroy()

    def test_normalizes_constructor_url(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        web = WebView(frame, url="example.com")
        assert web._pending_url == "https://example.com"
        web.destroy()
        frame.destroy()


class TestConstructorDimensionValidation:
    def test_rejects_negative_width(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="width must be > 0"):
            WebView(frame, width=-10, height=300)
        frame.destroy()

    def test_rejects_zero_height(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        with pytest.raises(ValueError, match="height must be > 0"):
            WebView(frame, width=300, height=0)
        frame.destroy()


class TestDestroyedSetterValidation:
    def test_set_ipc_handler_raises_after_destroy(self, tk_root) -> None:
        frame = tk.Frame(tk_root)
        web = WebView(frame)
        web.destroy()
        with pytest.raises(WebViewDestroyedError):
            web.set_ipc_handler(lambda _msg: None)
        frame.destroy()
