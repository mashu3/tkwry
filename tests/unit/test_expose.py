"""Unit tests for WebView.expose registration rules."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import WebView


def test_expose_rejects_duplicate_names(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    @web.expose
    def greet() -> str:
        return "hi"

    with pytest.raises(ValueError, match="already exposed"):

        @web.expose
        def greet() -> str:  # noqa: F811
            return "bye"

    @web.expose(replace=True)
    def greet() -> str:  # noqa: F811
        return "ok"

    assert web.unexpose("greet") is True
    assert web.unexpose("greet") is False

    web.destroy()
    frame.destroy()


def test_expose_thread_conflicts_with_main(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")

    with pytest.raises(ValueError, match="conflicts"):

        @web.expose(thread=True, run_in="main")
        def bad() -> None:
            return None

    web.destroy()
    frame.destroy()
