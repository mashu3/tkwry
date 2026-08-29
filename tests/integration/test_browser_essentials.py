"""Browser-essentials integration: headers, cookies, zoom, permission, clipboard.

Uses a local HTTP server (D20). Keep this module in its own CI pytest process
on Linux/Windows so WebView create/destroy fatigue does not cascade.
"""

from __future__ import annotations

import json
import sys

import pytest
from support.http_server import LocalHttpServer
from support.tk import host_frame, wait_until

from tkwry import (
    Cookie,
    PermissionKind,
    PermissionResponse,
    WebView,
    WebViewDestroyedError,
)


def _decode_eval_payload(raw: str) -> object:
    data: object = raw
    for _ in range(2):
        if not isinstance(data, str):
            break
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            break
    return data


def _eval_js_value(
    web: WebView, root, script: str, *, steps: int = 200
) -> object | None:
    results: list[object] = []

    def callback(raw: str) -> None:
        results.append(_decode_eval_payload(raw))

    per_try = max(steps // 10, 20)
    for _ in range(10):
        web.eval_js_with_callback(script, callback)
        if wait_until(root, lambda: len(results) > 0, steps=per_try):
            return results[-1]
    return None


def _wait_marker(web: WebView, root, marker: str) -> None:
    assert wait_until(root, lambda: web.ready, steps=200)

    def present() -> bool:
        got = _eval_js_value(
            web,
            root,
            "document.getElementById('t') && document.getElementById('t').textContent",
            steps=40,
        )
        return got == marker

    assert wait_until(root, present, steps=300), f"expected page marker #t={marker!r}"


def _cookie_names(web: WebView, url: str) -> set[str]:
    """Names from ``cookies_for_url``, falling back to ``cookies()``.

    On macOS / WKWebView, wry ``cookies_for_url`` often returns ``[]`` even
    when ``cookies()`` lists host cookies for the same jar — still call the
    URL filter first so platforms that implement it are covered.
    """
    filtered = {c.name for c in web.cookies_for_url(url)}
    if filtered:
        return filtered
    return {c.name for c in web.cookies()}


def _http_host(base_url: str) -> str:
    # http://127.0.0.1:1234 → 127.0.0.1
    return base_url.split("://", 1)[1].split(":", 1)[0]


@pytest.fixture
def http_server() -> LocalHttpServer:
    with LocalHttpServer() as server:
        yield server


def test_load_url_custom_header_reaches_server(
    tk_root, http_server: LocalHttpServer
) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, ephemeral=True, html="<p id='t'>boot</p>")
    try:
        assert wait_until(tk_root, lambda: web.ready, steps=200)
        http_server.clear_requests()
        web.load_url(
            http_server.url("/headers"),
            headers={"X-Tkwry-Test": "d20-header"},
        )
        _wait_marker(web, tk_root, "headers")

        def header_seen() -> bool:
            last = http_server.last_request("/headers")
            if last is None:
                return False
            # Header names are case-insensitive; BaseHTTPRequestHandler
            # normalizes to the form the client sent or title-case.
            return any(
                k.lower() == "x-tkwry-test" and v == "d20-header"
                for k, v in last.headers.items()
            )

        assert wait_until(tk_root, header_seen, steps=50), (
            f"expected X-Tkwry-Test on /headers; requests={http_server.requests!r}"
        )
    finally:
        web.destroy()
        frame.destroy()


def test_cookie_set_cookie_delete_and_clear(
    tk_root, http_server: LocalHttpServer
) -> None:
    frame = host_frame(tk_root)
    base = http_server.base_url
    web = WebView(frame, ephemeral=True, html="<p id='t'>boot</p>")
    try:
        assert wait_until(tk_root, lambda: web.ready, steps=200)

        web.load_url(http_server.url("/set-cookie"))
        _wait_marker(web, tk_root, "set-cookie")

        def server_cookie_present() -> bool:
            return "tkwry_sid" in _cookie_names(web, base)

        assert wait_until(tk_root, server_cookie_present, steps=200), (
            f"expected Set-Cookie tkwry_sid via cookies API; "
            f"cookies_for_url={[c.name for c in web.cookies_for_url(base)]!r} "
            f"cookies={[c.name for c in web.cookies()]!r}"
        )

        # Host-only cookies need an explicit domain on some engines (WKWebView).
        web.set_cookie(
            Cookie(
                "tkwry_py",
                "from-python",
                domain=_http_host(base),
                path="/",
            )
        )

        def py_cookie_present() -> bool:
            return "tkwry_py" in _cookie_names(web, base)

        assert wait_until(tk_root, py_cookie_present, steps=200), (
            "expected set_cookie(tkwry_py) visible via cookies API"
        )

        web.load_url(http_server.url("/show"))
        _wait_marker(web, tk_root, "show")

        def js_sees_py_cookie() -> bool:
            doc = _eval_js_value(
                web,
                tk_root,
                "document.getElementById('doc-cookie') && "
                "document.getElementById('doc-cookie').textContent",
                steps=40,
            )
            return isinstance(doc, str) and "tkwry_py=from-python" in doc

        assert wait_until(tk_root, js_sees_py_cookie, steps=200), (
            "expected document.cookie to include tkwry_py after set_cookie"
        )

        jar = list(web.cookies_for_url(base)) or list(web.cookies())
        py = next(c for c in jar if c.name == "tkwry_py")
        web.delete_cookie(py)

        def py_cookie_gone() -> bool:
            return "tkwry_py" not in _cookie_names(web, base)

        assert wait_until(tk_root, py_cookie_gone, steps=200), (
            "expected delete_cookie to remove tkwry_py"
        )

        web.clear_all_browsing_data()

        def jar_empty() -> bool:
            return _cookie_names(web, base) == set()

        assert wait_until(tk_root, jar_empty, steps=200), (
            f"expected clear_all_browsing_data to empty jar; "
            f"cookies_for_url={[c.name for c in web.cookies_for_url(base)]!r} "
            f"cookies={[c.name for c in web.cookies()]!r}"
        )
    finally:
        web.destroy()
        frame.destroy()


def test_zoom_permission_clipboard_and_destroy(
    tk_root, http_server: LocalHttpServer
) -> None:
    seen: list[PermissionKind] = []

    def handler(kind: PermissionKind) -> PermissionResponse:
        seen.append(kind)
        return PermissionResponse.Deny

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        ephemeral=True,
        clipboard=True,
        permission_handler=handler,
        html="<p id='t'>boot</p>",
    )
    try:
        assert web.clipboard is True
        assert wait_until(tk_root, lambda: web.ready, steps=200)

        web.load_url(http_server.url("/ok"))
        _wait_marker(web, tk_root, "ok")

        web.set_zoom(1.25)
        web.reset_zoom()

        assert (
            web._native_permission(PermissionKind.Notifications)
            is PermissionResponse.Deny
        )
        assert PermissionKind.Notifications in seen

        if sys.platform != "darwin":
            # Opt-in flag is the contract; navigator.clipboard presence varies.
            assert web.clipboard is True
    finally:
        web.destroy()
        frame.destroy()

    with pytest.raises(WebViewDestroyedError):
        web.cookies_for_url(http_server.base_url)
    with pytest.raises(WebViewDestroyedError):
        web.set_zoom(1.0)
    with pytest.raises(WebViewDestroyedError):
        web.clear_all_browsing_data()
