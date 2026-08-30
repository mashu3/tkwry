"""Unit coverage for create-time proxy=."""

from __future__ import annotations

import sys
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tkwry import WebView
from tkwry.webview import _normalize_proxy


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_normalize_proxy_none() -> None:
    assert _normalize_proxy(None) is None


def test_normalize_proxy_http_host_port() -> None:
    public, native = _normalize_proxy({"http": "127.0.0.1:8080"})
    assert public == {"http": "127.0.0.1:8080"}
    assert native == ("http", "127.0.0.1", "8080")


def test_normalize_proxy_socks5_url() -> None:
    public, native = _normalize_proxy({"socks5": "socks5://127.0.0.1:1080"})
    assert public == {"socks5": "127.0.0.1:1080"}
    assert native == ("socks5", "127.0.0.1", "1080")


def test_normalize_proxy_ipv6() -> None:
    public, native = _normalize_proxy({"http": "[::1]:3128"})
    assert public == {"http": "[::1]:3128"}
    assert native == ("http", "::1", "3128")


def test_normalize_proxy_rejects_both_keys() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _normalize_proxy({"http": "127.0.0.1:1", "socks5": "127.0.0.1:2"})


def test_normalize_proxy_rejects_credentials_without_echo() -> None:
    secret = "super-secret-password-xyz"
    with pytest.raises(ValueError, match="must not include credentials") as exc:
        _normalize_proxy({"http": f"http://user:{secret}@127.0.0.1:8080"})
    assert secret not in str(exc.value)


def test_normalize_proxy_rejects_bad_port() -> None:
    with pytest.raises(ValueError, match="1-65535"):
        _normalize_proxy({"http": "127.0.0.1:0"})


def test_proxy_default_none(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>p</p>")
    assert web.proxy is None
    web.destroy()
    assert web.proxy is None
    frame.destroy()


def test_proxy_readable_after_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>p</p>", proxy={"http": "127.0.0.1:9"})
    assert web.proxy == {"http": "127.0.0.1:9"}
    web.destroy()
    assert web.proxy == {"http": "127.0.0.1:9"}
    frame.destroy()


def test_proxy_property_returns_copy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>p</p>", proxy={"socks5": "127.0.0.1:1080"})
    first = web.proxy
    assert first is not None
    first["socks5"] = "mutated"
    assert web.proxy == {"socks5": "127.0.0.1:1080"}
    web.destroy()
    frame.destroy()


def test_try_create_passes_proxy(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)

    web = WebView(
        frame, html="<p>p</p>", width=200, height=150, proxy={"http": "127.0.0.1:9"}
    )
    captured: dict[str, object] = {}

    def fake_native(*_args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        native = MagicMock()
        native.is_alive.return_value = False
        return native

    monkeypatch.setattr("tkwry.webview.NativeWebView", fake_native)
    monkeypatch.setattr(web, "_sync_bounds", lambda: True)
    monkeypatch.setattr(web, "_maybe_fire_ready", lambda: None)
    monkeypatch.setattr(web, "_schedule_initial_load", lambda: None)
    monkeypatch.setattr(web, "_ensure_event_poll", lambda: None)
    monkeypatch.setattr(web, "_needs_event_poll", lambda: False)
    if sys.platform == "darwin":
        monkeypatch.setattr("tkwry.webview._ensure_mac_wakeup_pipe", lambda *_a: None)
        monkeypatch.setattr("tkwry.webview._ensure_mac_pump", lambda *_a: None)

    web._try_create()
    assert captured.get("proxy") == ("http", "127.0.0.1", "9")
    web.destroy()
    frame.destroy()
