"""Tests for ``WebView(app=...)`` path resolution and ``watch_app`` scanning."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry._app import (
    DEFAULT_CSP,
    WATCH_DEFAULT_IGNORE_DIRS,
    app_url,
    resolve_app,
    resolve_app_csp,
    scan_app_mtime,
    validate_app_isolation,
)


def test_app_url_default() -> None:
    assert app_url() == "tkwry://localhost/index.html"
    assert app_url("assets/main.js") == "tkwry://localhost/assets/main.js"


def test_resolve_app_directory(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    root, url = resolve_app(tmp_path)
    assert root == str(tmp_path.absolute())
    assert url == "tkwry://localhost/index.html"


def test_resolve_app_html_file(tmp_path: Path) -> None:
    page = tmp_path / "editor.html"
    page.write_text("<p>editor</p>", encoding="utf-8")
    root, url = resolve_app(page)
    assert root == str(tmp_path.absolute())
    assert url == "tkwry://localhost/editor.html"


def test_resolve_app_missing_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="index.html"):
        resolve_app(tmp_path)


def test_resolve_app_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_app(tmp_path / "missing")


def test_resolve_app_rejects_non_html_file(tmp_path: Path) -> None:
    js = tmp_path / "main.js"
    js.write_text("1", encoding="utf-8")
    with pytest.raises(ValueError, match="HTML"):
        resolve_app(js)


def test_scan_app_mtime_filters_suffix_and_ignore_dirs(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("txt", encoding="utf-8")
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text("1", encoding="utf-8")
    (tmp_path / "skip.bin").write_text("x", encoding="utf-8")

    latest, seen, truncated = scan_app_mtime(
        tmp_path, suffixes=[".html", ".js"], ignore_dirs=WATCH_DEFAULT_IGNORE_DIRS
    )
    assert seen == 1
    assert truncated is False
    assert latest > 0.0

    latest_all, seen_all, _ = scan_app_mtime(
        tmp_path, suffixes=None, ignore_dirs=WATCH_DEFAULT_IGNORE_DIRS
    )
    assert seen_all == 3  # html + txt + bin; node_modules skipped
    assert latest_all >= latest


def test_watch_app_rejects_string_filters(tk_root, tmp_path: Path) -> None:
    import tkinter as tk

    from tkwry import WebView

    (tmp_path / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=tmp_path)
    with pytest.raises(ValueError, match="suffixes"):
        web.watch_app(suffixes=".js")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ignore_dirs"):
        web.watch_app(ignore_dirs="node_modules")  # type: ignore[arg-type]
    web.watch_app(suffixes=["html", ".js"], max_files=50)
    web.destroy()
    frame.destroy()


def test_scan_app_mtime_respects_max_files(tmp_path: Path) -> None:
    (tmp_path / "a.html").write_text("a", encoding="utf-8")
    (tmp_path / "b.html").write_text("b", encoding="utf-8")
    (tmp_path / "c.html").write_text("c", encoding="utf-8")
    latest, seen, truncated = scan_app_mtime(
        tmp_path, suffixes=[".html"], ignore_dirs=(), max_files=2
    )
    assert seen == 2
    assert truncated is True
    assert latest > 0.0


def test_resolve_app_csp_default_and_overrides() -> None:
    assert resolve_app_csp(None, has_app=True) == DEFAULT_CSP
    assert resolve_app_csp(None, has_app=False) is None
    assert resolve_app_csp(True, has_app=True) == DEFAULT_CSP
    assert resolve_app_csp(False, has_app=True) is None
    assert resolve_app_csp("default-src 'none'", has_app=True) == "default-src 'none'"
    with pytest.raises(ValueError, match="requires app"):
        resolve_app_csp(True, has_app=False)
    with pytest.raises(ValueError, match="requires app"):
        resolve_app_csp("default-src 'self'", has_app=False)
    with pytest.raises(ValueError, match="single-line"):
        resolve_app_csp("default-src 'self'\nimg-src *", has_app=True)
    with pytest.raises(TypeError):
        resolve_app_csp(1, has_app=True)  # type: ignore[arg-type]
    validate_app_isolation(coop=False, corp=False, has_app=False)
    with pytest.raises(ValueError, match="coop"):
        validate_app_isolation(coop=True, corp=False, has_app=False)


def test_app_webview_default_csp(tk_root, tmp_path: Path) -> None:
    import tkinter as tk

    from tkwry import WebView

    (tmp_path / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=tmp_path)
    assert web.csp == DEFAULT_CSP
    assert web.coop is False
    assert web.corp is False
    web.destroy()
    frame.destroy()


def test_app_webview_csp_false_and_isolation(tk_root, tmp_path: Path) -> None:
    import tkinter as tk

    from tkwry import WebView

    (tmp_path / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=tmp_path, csp=False, coop=True, corp=True)
    assert web.csp is None
    assert web.coop is True
    assert web.corp is True
    web.destroy()
    frame.destroy()


def test_csp_requires_app(tk_root) -> None:
    import tkinter as tk

    from tkwry import WebView

    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="csp"):
        WebView(frame, html="<p>x</p>", csp=True)
    with pytest.raises(ValueError, match="coop"):
        WebView(frame, html="<p>x</p>", coop=True)
    frame.destroy()
