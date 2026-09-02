"""Trust-boundary API: untrusted mode, bridge origins, app navigation lock."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tkwry import NewWindowResponse, TkwrySecurityWarning, WebSession, WebView
from tkwry._origin import APP_ORIGINS, HTML_BRIDGE_ORIGINS


def _app_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<p>app</p>", encoding="utf-8")
    return tmp_path


def test_html_infers_inline_bridge_origins(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    assert web.untrusted is False
    assert web.bridge_origins == HTML_BRIDGE_ORIGINS
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
    assert web._invoke_navigation_handler("http://tkwry.localhost/x") is True
    assert web._invoke_navigation_handler("https://evil.example/") is False
    assert web._invoke_navigation_handler("file:///tmp/secret") is False
    assert web._invoke_navigation_handler("data:text/html,<p>x</p>") is False
    assert (
        web._invoke_new_window_handler("https://evil.example/")
        is NewWindowResponse.Deny
    )
    web.set_on_navigation(lambda url: url.startswith("https://"))
    assert web._invoke_navigation_handler("https://allowed.example/") is True
    web.destroy()
    frame.destroy()


def test_navigation_allow_and_open_external(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "tkwry._origin.webbrowser.open",
        lambda url, new=2: opened.append(url) or True,
    )
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        app=_app_dir(tmp_path),
        navigation_allow=["https://docs.example.com/app"],
        open_external=True,
    )
    assert web.navigation_allow == frozenset({"https://docs.example.com/app"})
    assert web.open_external is True
    assert web._invoke_navigation_handler("tkwry://localhost/index.html") is True
    assert web._invoke_navigation_handler("https://docs.example.com/app/x") is True
    assert web._invoke_navigation_handler("https://docs.example.com/other") is False
    tk_root.update()
    assert opened == ["https://docs.example.com/other"]
    opened.clear()
    assert (
        web._invoke_new_window_handler("https://evil.example/")
        is NewWindowResponse.Deny
    )
    tk_root.update()
    assert opened == ["https://evil.example/"]
    opened.clear()
    assert web._invoke_navigation_handler("file:///tmp/secret") is False
    tk_root.update()
    assert opened == []
    web.destroy()
    frame.destroy()


def test_open_external_only_opens_new_windows(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "tkwry._origin.webbrowser.open",
        lambda url, new=2: opened.append(url) or True,
    )
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", open_external=True)
    assert web._invoke_navigation_handler("https://anywhere.example/") is True
    tk_root.update()
    assert opened == []
    assert (
        web._invoke_new_window_handler("https://anywhere.example/")
        is NewWindowResponse.Deny
    )
    tk_root.update()
    assert opened == ["https://anywhere.example/"]
    web.destroy()
    frame.destroy()


def test_custom_navigation_handler_skips_open_external(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "tkwry._origin.webbrowser.open",
        lambda url, new=2: opened.append(url) or True,
    )
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        app=_app_dir(tmp_path),
        open_external=True,
        on_navigation=lambda _url: False,
    )
    assert web._invoke_navigation_handler("https://evil.example/") is False
    tk_root.update()
    assert opened == []
    web.destroy()
    frame.destroy()


def test_navigation_allow_without_app(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        url="https://example.com/",
        navigation_allow=["https://example.com"],
    )
    assert web._invoke_navigation_handler("https://example.com/x") is True
    assert web._invoke_navigation_handler("about:blank") is True
    assert web._invoke_navigation_handler("https://other.example/") is False
    web.destroy()
    frame.destroy()


def test_untrusted_rejects_bridge_and_forces_ephemeral(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>view</p>", untrusted=True)
    assert web.untrusted is True
    assert web.javascript_enabled is True
    assert web.default_context_menus is True
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
    assert web._invoke_navigation_handler("https://tkwry.localhost/") is False
    assert web._invoke_navigation_handler("http://tkwry.localhost/") is False
    assert web._invoke_navigation_handler("file:///tmp/x") is False
    # html= + untrusted still allows NavigateToString (data: → null origin).
    assert web._invoke_navigation_handler("data:text/html,<p>x</p>") is True
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
    with pytest.raises(ValueError, match="untrusted"):
        WebView(frame, html="<p>x</p>", untrusted=True, bridge_origins="*")
    with pytest.raises(ValueError, match="untrusted"):
        WebView(
            frame,
            html="<p>x</p>",
            untrusted=True,
            bridge_origins=["https://example.com"],
        )
    with pytest.raises(ValueError, match="untrusted"):
        WebView(frame, html="<p>x</p>", untrusted=True, bridge_allow=lambda _u: True)
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
    native.drain_window_ipc_messages.return_value = [
        (
            "https://evil.example/",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == []
    native.eval_js.assert_not_called()
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
    native.drain_window_ipc_messages.return_value = [
        (
            "about:blank",
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == ["ping"]
    web.destroy()
    frame.destroy()


@pytest.mark.parametrize(
    "source_url",
    [
        "data:text/html,<script>window.tkwry.call('ping')</script>",
        "blob:https://example.com/uuid",
        "about:srcdoc",
    ],
)
def test_opaque_document_rpc_is_rejected(tk_root, source_url: str) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>rpc</p>")
    called: list[str] = []

    @web.expose
    def ping() -> str:
        called.append("ping")
        return "ok"

    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        (
            source_url,
            json.dumps({"__tkwry": "rpc", "id": "r1", "method": "ping", "params": []}),
        )
    ]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == []
    native.eval_js.assert_not_called()
    web.destroy()
    frame.destroy()


def test_bridge_origins_star_allows_any_page(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.warns(TkwrySecurityWarning, match="bridge_origins"):
        web = WebView(frame, url="https://example.com/", bridge_origins="*")
    assert web.bridge_origins == "*"
    called: list[str] = []
    web.set_ipc_handler(called.append)
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [
        ("https://other.example/", "hello"),
    ]
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
    with pytest.warns(TkwrySecurityWarning, match="bridge_origins"):
        web.set_bridge_origins("*")
    assert web.bridge_origins == "*"
    web.destroy()
    frame.destroy()


def test_bridge_path_prefix_and_allow_callback(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        url="https://trusted.example/app",
        bridge_origins=["https://trusted.example/app"],
        bridge_allow=lambda url: "/ok" in url,
    )
    assert web.bridge_origins == frozenset({"https://trusted.example/app"})
    assert web._bridge_origin_allowed("https://trusted.example/app/ok") is True
    assert web._bridge_origin_allowed("https://trusted.example/app/no") is False
    assert web._bridge_origin_allowed("https://trusted.example/other/ok") is False
    web.set_bridge_allow(None)
    assert web._bridge_origin_allowed("https://trusted.example/app/no") is True
    web.destroy()
    frame.destroy()


def test_ipc_empty_source_normalizes_for_html_bridge(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    called: list[str] = []
    web.set_ipc_handler(called.append)
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [("", "hello")]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == ["hello"]
    web.destroy()
    frame.destroy()


def test_ipc_empty_source_stays_denied_for_url_bridge(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, url="https://example.com/")
    called: list[str] = []
    web.set_ipc_handler(called.append)
    native = MagicMock()
    native.drain_window_ipc_messages.return_value = [("", "hello")]
    web._webview = native
    web._deliver_ipc_messages()
    assert called == []
    web.destroy()
    frame.destroy()


def test_expose_star_requires_allow_any_origin(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.warns(TkwrySecurityWarning, match="bridge_origins"):
        web = WebView(frame, html="<p>x</p>", bridge_origins="*")
    with pytest.raises(ValueError, match="allow_any_origin"):

        @web.expose
        def ping() -> str:
            return "ok"

    @web.expose(allow_any_origin=True)
    def ping() -> str:
        return "ok"

    web.destroy()
    frame.destroy()


def test_set_bridge_origins_star_requires_allow_any_origin(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.expose
    def ping() -> str:
        return "ok"

    with pytest.raises(ValueError, match="allow_any_origin"):
        web.set_bridge_origins("*")
    web.unexpose("ping")
    with pytest.warns(TkwrySecurityWarning, match="bridge_origins"):
        web.set_bridge_origins("*")
    web.destroy()
    frame.destroy()


def test_devtools_star_emits_extra_warning(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.warns(TkwrySecurityWarning) as caught:
        web = WebView(
            frame,
            url="https://example.com/",
            bridge_origins="*",
            devtools=True,
        )
    messages = [str(item.message) for item in caught]
    assert any("bridge_origins" in msg for msg in messages)
    assert any("devtools" in msg for msg in messages)
    web.destroy()
    frame.destroy()
