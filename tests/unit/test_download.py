"""Download allow/deny policy and complete delivery (no native download)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.linux import noop_linux_runtime

from tkwry import WebView, unique_download_path
from tkwry._origin import normalize_download_allow


@pytest.fixture(autouse=True)
def _noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    noop_linux_runtime(monkeypatch)


def _make_web(tk_root, **kwargs: object) -> WebView:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    return WebView(frame, **kwargs)


def test_trusted_allows_download_by_default(tk_root) -> None:
    web = _make_web(tk_root)
    try:
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (True, None)
        assert web._native_download_started(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (True, None)
    finally:
        web.destroy()


def test_untrusted_denies_download_by_default(tk_root) -> None:
    web = _make_web(tk_root, untrusted=True, url="https://example.com")
    try:
        assert web.download_allow is None
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (False, None)
    finally:
        web.destroy()


def test_download_allow_filters_urls(tk_root) -> None:
    web = _make_web(
        tk_root,
        download_allow=["https://cdn.example.com"],
    )
    try:
        assert web.download_allow == frozenset({"https://cdn.example.com"})
        assert web._invoke_download_handler(
            "https://cdn.example.com/a.zip", "/tmp/a.zip"
        ) == (True, None)
        assert web._invoke_download_handler(
            "https://evil.example/a.zip", "/tmp/a.zip"
        ) == (False, None)
    finally:
        web.destroy()


def test_on_download_can_set_absolute_dest(tk_root, tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    web = _make_web(tk_root, on_download=lambda _url, _suggested: dest)
    try:
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (True, str(dest))
    finally:
        web.destroy()


def test_on_download_false_cancels(tk_root) -> None:
    web = _make_web(tk_root, on_download=lambda _url, _dest: False)
    try:
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (False, None)
    finally:
        web.destroy()


def test_untrusted_on_download_can_allow(tk_root) -> None:
    web = _make_web(
        tk_root,
        untrusted=True,
        url="https://example.com",
        on_download=lambda _url, _dest: True,
    )
    try:
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (True, None)
    finally:
        web.destroy()


def test_relative_download_dest_is_denied(tk_root) -> None:
    web = _make_web(tk_root, on_download=lambda _url, _dest: "relative.bin")
    try:
        assert web._invoke_download_handler(
            "https://example.com/a.zip", "/tmp/a.zip"
        ) == (False, None)
    finally:
        web.destroy()


def test_dangerous_download_schemes_denied(tk_root) -> None:
    web = _make_web(tk_root)
    try:
        assert web._invoke_download_handler("javascript:alert(1)", "/tmp/x") == (
            False,
            None,
        )
        assert web._invoke_download_handler("mailto:user@example.com", "/tmp/x") == (
            False,
            None,
        )
    finally:
        web.destroy()


def test_download_allow_rejects_star_string() -> None:
    with pytest.raises(TypeError, match="download_allow"):
        normalize_download_allow("https://cdn.example.com")


def test_download_allow_rejects_star_entry() -> None:
    with pytest.raises(ValueError, match=r"download_allow.*\*"):
        normalize_download_allow(["*"])


def test_set_on_download_toggles_poll(tk_root) -> None:
    web = _make_web(tk_root)
    try:
        assert web._needs_event_poll() is False
        web.set_on_download(lambda _url, _dest: True)
        assert web._needs_event_poll() is True
        web.set_on_download(None)
        assert web._needs_event_poll() is False
    finally:
        web.destroy()


def test_download_complete_handler_keeps_poll(tk_root) -> None:
    web = _make_web(tk_root)
    web._webview = MagicMock()
    try:
        assert web._needs_event_poll() is False
        web.set_on_download_complete(lambda *_args: None)
        assert web._needs_event_poll() is True
        web.set_on_download_complete(None)
        assert web._needs_event_poll() is False
    finally:
        web._webview = None
        web.destroy()


def test_download_complete_delivery(tk_root) -> None:
    web = _make_web(tk_root)
    events: list[tuple[str, str | None, bool]] = []
    native = MagicMock()
    native.drain_download_complete_events.return_value = [
        ("https://example.com/a.zip", "/tmp/a.zip", True)
    ]
    web._webview = native
    web.set_on_download_complete(
        lambda url, dest, success: events.append((url, dest, success))
    )
    try:
        web._deliver_download_complete_events()
        assert events == [("https://example.com/a.zip", "/tmp/a.zip", True)]
        assert web.last_download == ("https://example.com/a.zip", "/tmp/a.zip", True)
    finally:
        web._webview = None
        web.destroy()


def test_download_complete_virtual_events_without_handler(tk_root) -> None:
    web = _make_web(tk_root)
    native = MagicMock()
    native.drain_download_complete_events.return_value = [
        ("https://example.com/a.zip", "/tmp/a.zip", True)
    ]
    web._webview = native
    fired: list[str] = []
    web.bind("<<WebViewDownloadComplete>>", lambda _evt: fired.append("ok"))
    web.bind("<<WebViewDownloadFailed>>", lambda _evt: fired.append("fail"))
    try:
        assert web.last_download is None
        web._deliver_download_complete_events()
        assert fired == ["ok"]
        assert web.last_download == ("https://example.com/a.zip", "/tmp/a.zip", True)
    finally:
        web._webview = None
        web.destroy()


def test_download_failed_virtual_event(tk_root) -> None:
    web = _make_web(tk_root)
    native = MagicMock()
    native.drain_download_complete_events.return_value = [
        ("https://example.com/a.zip", None, False)
    ]
    web._webview = native
    fired: list[str] = []
    web.bind("<<WebViewDownloadFailed>>", lambda _evt: fired.append("fail"))
    try:
        web._deliver_download_complete_events()
        assert fired == ["fail"]
        assert web.last_download == ("https://example.com/a.zip", None, False)
    finally:
        web._webview = None
        web.destroy()


def test_unique_download_path_returns_unused(tmp_path: Path) -> None:
    dest = tmp_path / "report.pdf"
    assert unique_download_path(dest) == dest


def test_unique_download_path_inserts_number(tmp_path: Path) -> None:
    dest = tmp_path / "report.pdf"
    dest.write_bytes(b"x")
    assert unique_download_path(dest) == tmp_path / "report (1).pdf"
    (tmp_path / "report (1).pdf").write_bytes(b"x")
    assert unique_download_path(dest) == tmp_path / "report (2).pdf"


def test_unique_download_path_no_suffix(tmp_path: Path) -> None:
    dest = tmp_path / "README"
    dest.write_text("x")
    assert unique_download_path(dest) == tmp_path / "README (1)"


def test_unique_download_path_rejects_relative() -> None:
    with pytest.raises(ValueError, match="absolute"):
        unique_download_path("report.pdf")


def test_on_download_can_use_unique_download_path(tk_root, tmp_path: Path) -> None:
    dest = tmp_path / "a.zip"
    dest.write_bytes(b"x")
    web = _make_web(
        tk_root,
        on_download=lambda _url, suggested: unique_download_path(suggested),
    )
    try:
        allowed, path = web._invoke_download_handler(
            "https://example.com/a.zip", str(dest)
        )
        assert allowed is True
        assert path == str(tmp_path / "a (1).zip")
    finally:
        web.destroy()
