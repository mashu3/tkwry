"""Regression tests for html=/app=/url= constructor precedence (D31 / T14)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from tkwry import WebView
from tkwry._app import DEFAULT_CSP
from tkwry._origin import HTML_BRIDGE_ORIGINS


def test_html_wins_over_app_clears_app_side_effects(
    tk_root, tmp_path: Path, capsys
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<p>app</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>inline</p>", app=app_dir)
    try:
        assert "html= takes precedence" in capsys.readouterr().err
        assert web._app_root is None
        assert web._pending_html == "<p>inline</p>"
        assert web._pending_url is None
        assert not web._lock_app_navigation
        assert web._bridge_origins == HTML_BRIDGE_ORIGINS
        assert web.csp is None
        assert web._invoke_navigation_handler("https://example.com/") is True
    finally:
        web.destroy()
        frame.destroy()


def test_html_wins_over_app_and_url(tk_root, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<p>app</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        html="<p>inline</p>",
        app=app_dir,
        url="https://example.com/",
    )
    try:
        assert web._app_root is None
        assert web._pending_html == "<p>inline</p>"
        assert web._pending_url is None
    finally:
        web.destroy()
        frame.destroy()


def test_app_still_wins_over_url_without_html(tk_root, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>app</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=tmp_path, url="https://example.com/")
    try:
        assert web._app_root == str(tmp_path.absolute())
        assert web._pending_html is None
        assert web._pending_url == "tkwry://localhost/index.html"
        assert web.csp == DEFAULT_CSP
    finally:
        web.destroy()
        frame.destroy()
