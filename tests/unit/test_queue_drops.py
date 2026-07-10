"""Tests for queue overflow drop reporting."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView
from tkwry.exceptions import WebViewDestroyedError


def test_take_queue_drop_counts_before_native_returns_zeros(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    assert web.take_queue_drop_counts() == (0, 0, 0, 0, 0)

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_delegates_to_native(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.take_queue_drop_counts.return_value = (1, 2, 3, 4, 5)
    web._webview = native

    assert web.take_queue_drop_counts() == (1, 2, 3, 4, 5)
    native.take_queue_drop_counts.assert_called_once_with()

    web.destroy()
    frame.destroy()


def test_take_queue_drop_counts_rejects_destroyed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.destroy()

    with pytest.raises(WebViewDestroyedError):
        web.take_queue_drop_counts()

    frame.destroy()
