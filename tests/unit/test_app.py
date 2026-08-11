"""Tests for ``WebView(app=...)`` path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry._app import app_url, resolve_app


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
