"""Unit tests for ``examples/tkwry_browser.py`` (no live WebView)."""

from __future__ import annotations

import importlib.util
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
_BROWSER_PATH = ROOT / "examples" / "tkwry_browser.py"


@pytest.fixture(scope="module")
def browser():
    name = "tkwry_browser_example"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _BROWSER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_required_version_is_at_least_0_1_8(browser) -> None:
    assert browser._version_tuple(browser.REQUIRED_TKWRY) >= (0, 1, 8)


def test_version_tuple_parses_and_rejects_garbage(browser) -> None:
    assert browser._version_tuple("0.1.8") == (0, 1, 8)
    assert browser._version_tuple("1.2.3.dev0") == (1, 2, 3)
    assert browser._version_tuple("") == (0, 0, 0)
    assert browser._version_tuple("nope") == (0, 0, 0)


def test_require_tkwry_rejects_older_version(browser, monkeypatch) -> None:
    fake = SimpleNamespace(__version__="0.1.7", __file__="/tmp/fake/tkwry")
    monkeypatch.setitem(sys.modules, "tkwry", fake)
    with pytest.raises(SystemExit, match=r"tkwry >= 0\.1\.8"):
        browser._require_tkwry()


def test_is_ntp_url(browser) -> None:
    assert browser._is_ntp_url(None)
    assert browser._is_ntp_url("")
    assert browser._is_ntp_url("about:blank")
    assert browser._is_ntp_url("  ABOUT:BLANK  ")
    assert not browser._is_ntp_url("https://example.com")


def test_normalize_input(browser) -> None:
    def search(q: str) -> str:
        return f"https://search.example/?q={q}"

    assert (
        browser.normalize_input("", home="https://home.test", search_url=search)
        == "https://home.test"
    )
    assert (
        browser.normalize_input("https://a.test/x", home="h", search_url=search)
        == "https://a.test/x"
    )
    assert (
        browser.normalize_input("//cdn.test/a", home="h", search_url=search)
        == "https://cdn.test/a"
    )
    assert (
        browser.normalize_input("example.com", home="h", search_url=search)
        == "https://example.com"
    )
    assert browser.normalize_input(
        "hello world", home="h", search_url=search
    ).startswith("https://search.example/")


def test_security_indicator(browser) -> None:
    assert browser.security_indicator("https://x")[0] == "secure"
    assert browser.security_indicator("http://x")[0] == "insecure"
    assert browser.security_indicator("file:///tmp/a")[0] == "local"
    assert browser.security_indicator("tkwry://localhost/")[0] == "local"
    assert browser.security_indicator("about:blank")[0] == "blank"
    assert browser.security_indicator(None)[0] == "unknown"


def test_tab_label_and_favicon(browser) -> None:
    assert browser.tab_label("") == "New Tab"
    assert browser.tab_label("x" * 40).endswith("…")
    assert "example.com" in browser._favicon_url("https://example.com/a")
    assert browser._favicon_url("about:blank") == ""


def test_sanitize_profile_name(browser) -> None:
    assert browser.sanitize_profile_name("  ok  ") == "ok"
    assert browser.sanitize_profile_name("a/b:c") == "a_b_c"
    assert browser.sanitize_profile_name("   ") == browser.DEFAULT_PROFILE


def test_blank_tab_html_has_brand_and_wry_favicon(browser) -> None:
    html = browser._blank_tab_html(dark=False)
    assert "<title>New Tab</title>" in html
    assert 'rel="icon"' in html
    assert browser.WRY_TAB_ICON in html
    assert "tkwry" in html
    dark = browser._blank_tab_html(dark=True)
    assert 'data-theme="dark"' in dark


def test_ui_asset_dirs_materialize_bundles(browser) -> None:
    chrome, side, settings = browser._ui_asset_dirs()
    assert (chrome / "index.html").is_file()
    assert (side / "app.js").is_file()
    assert (settings / "styles.css").is_file()
    # Cached: same paths on second call.
    again = browser._ui_asset_dirs()
    assert again == (chrome, side, settings)


def test_tab_icon_settings_and_ntp(browser) -> None:
    settings_tab = browser.Tab(frame=MagicMock(), kind="settings")
    assert browser.tab_icon(browser.SETTINGS_TAB_ID, settings_tab) == "settings"

    ntp = browser.Tab(frame=MagicMock(), kind="ntp")
    assert browser.tab_icon("tab-1", ntp) == browser.WRY_TAB_ICON

    content = browser.Tab(frame=MagicMock(), kind="content")
    content.web = None
    assert browser.tab_icon("tab-2", content) is None

    content.web = SimpleNamespace(destroyed=False, url="https://example.com/")
    icon = browser.tab_icon("tab-2", content)
    assert icon and "example.com" in icon


def test_shortcut_bind_skips_unsupported_keysym(browser) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        calls: list[str] = []

        def handler(_event: tk.Event) -> None:
            return None

        real_bind_class = root.bind_class

        def bind_class(tag: str, sequence: str, func=None, add=None):  # noqa: ANN001
            calls.append(sequence)
            if "NotARealKeysymXYZ" in sequence:
                raise tk.TclError("bad event type or keysym")
            return real_bind_class(tag, sequence, func, add)

        root.bind_class = bind_class  # type: ignore[method-assign]
        browser.BrowserShortcutBindings._bind_sequence(
            root, "<Control-NotARealKeysymXYZ>", handler
        )
        browser.BrowserShortcutBindings._bind_sequence(root, "<Control-t>", handler)
        assert "<Control-NotARealKeysymXYZ>" in calls
        assert "<Control-t>" in calls
        # Survived the bad keysym; Control-t should be bound on the tag.
        assert root.bind_class(browser.BrowserShortcutBindings.TAG, "<Control-t>")
    finally:
        root.destroy()


def test_history_day_label(browser) -> None:
    from datetime import date, timedelta

    today = date(2026, 9, 5)
    assert browser._history_day_label(today, today=today) == "Today"
    assert (
        browser._history_day_label(today - timedelta(days=1), today=today)
        == "Yesterday"
    )
    # Within the last week → weekday name.
    assert (
        browser._history_day_label(today - timedelta(days=4), today=today) == "Tuesday"
    )
    older = browser._history_day_label(today - timedelta(days=10), today=today)
    assert "2026" in older


def test_default_bookmarks_flatten(browser) -> None:
    nodes = browser._default_bookmarks()
    flat = browser._flatten_bookmark_links(nodes, limit=8)
    urls = {item["url"] for item in flat}
    assert "https://github.com/mashu3/tkwry" in urls
    assert len(flat) <= 8
