"""Trust-boundary API: untrusted mode, bridge origins, app navigation lock."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tkwry import NewWindowResponse, WebSession, WebView
from tkwry._origin import APP_ORIGINS, INLINE_ORIGINS


def _app_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<p>app</p>", encoding="utf-8")
    return tmp_path


def test_html_infers_inline_bridge_origins(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web.untrusted is False
    assert web.bridge_origins == INLINE_ORIGINS
    web.destroy()
    frame.destroy()


def test_url_infers_page_origin(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com/app")
    assert web.bridge_origins == frozenset({"https://example.com"})
    web.destroy()
    frame.destroy()


def test_app_locks_navigation_and_new_window(tk_root, tmp_path: Path) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, app=_app_dir(tmp_path))
    assert web.bridge_origins == APP_ORIGINS
    assert web._invoke_navigation_handler("tkwry://localhost/index.html") is True
    assert web._invoke_navigation_handler("https://tkwry.localhost/x") is True
    assert web._invoke_navigation_handler("https://evil.example/") is False
    assert web._invoke_navigation_handler("file:///tmp/secret") is False
    assert (
        web._invoke_new_window_handler("https://evil.example/")
        is NewWindowResponse.Deny
    )
    web.set_on_navigation(lambda url: url.startswith("https://"))
    assert web._invoke_navigation_handler("https://allowed.example/") is True
    web.destroy()
    frame.destroy()


def test_untrusted_rejects_bridge_and_forces_ephemeral(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>view</p>", untrusted=True)
    assert web.untrusted is True
    assert web._session is not None
    assert web._session.ephemeral is True
    with pytest.raises(ValueError, match="untrusted"):
        web.set_ipc_handler(lambda _msg: None)
    with pytest.raises(ValueError, match="untrusted"):

        @web.expose
        def ping() -> str:
            return "pong"

    with pytest.raises(ValueError, match="untrusted"):
        web.emit("x", {})
    assert web._invoke_navigation_handler("https://example.com/") is True
    assert web._invoke_navigation_handler("tkwry://localhost/") is False
    assert web._invoke_navigation_handler("file:///tmp/x") is False
    assert (
        web._invoke_new_window_handler("https://example.com/") is NewWindowResponse.Deny
    )
    web.destroy()
    frame.destroy()


def test_untrusted_rejects_conflicting_constructor_args(
    tk_root, tmp_path: Path
) -> None:
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="untrusted"):
        WebView(frame, html="<p>x</p>", untrusted=True, ipc_handler=lambda _m: None)
    with pytest.raises(ValueError, match="untrusted"):
        WebView(frame, app=_app_dir(tmp_path), untrusted=True)
    with pytest.raises(ValueError, match="untrusted"):
        WebView(frame, html="<p>x</p>", untrusted=True, data_directory=tmp_path / "p")
    with pytest.raises(ValueError, match="untrusted"):
        WebView(
            frame,
            html="<p>x</p>",
            untrusted=True,
            session=WebSession(data_directory=tmp_path / "p"),
        )
    frame.destroy()


def test_foreign_origin_rpc_is_rejected(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    called: list[str] = []

    @web.expose
    def ping() -> str:
        called.append("ping")
        return "ok"

    native = MagicMock()
    native.drain_rpc_messages.return_value = [
        (
            "https://evil.example/",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    native.drain_ipc_messages.return_value = []
    web._webview = native
    web._deliver_ipc_messages()
    assert called == []
    native.eval_js.assert_called()
    script = native.eval_js.call_args[0][0]
    assert "RpcOriginError" in script
    web.destroy()
    frame.destroy()


def test_inline_origin_rpc_is_allowed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    called: list[str] = []

    @web.expose
    def ping() -> str:
        called.append("ping")
        return "ok"

    native = MagicMock()
    native.drain_rpc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    native.drain_ipc_messages.return_value = []
    web._webview = native
    web._deliver_ipc_messages()
    assert called == ["ping"]
    web.destroy()
    frame.destroy()


def test_bridge_origins_star_allows_any_page(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com/", bridge_origins="*")
    assert web.bridge_origins == "*"
    called: list[str] = []
    web.set_ipc_handler(called.append)
    native = MagicMock()
    native.drain_rpc_messages.return_value = []
    native.drain_ipc_messages.return_value = [("https://other.example/", "hello")]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == ["hello"]
    web.destroy()
    frame.destroy()


def test_set_bridge_origins_updates_allowlist(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_bridge_origins(["https://trusted.example"])
    assert web.bridge_origins == frozenset({"https://trusted.example"})
    web.set_bridge_origins("*")
    assert web.bridge_origins == "*"
    web.destroy()
    frame.destroy()
