"""Live create-only options: user_agent, initialization_script, DevTools.

These are bug nests: regressions look like a healthy WebView with wrong
(or missing) native wiring. Unit tests only cover raise-after-create.
"""

from __future__ import annotations

import json

import pytest
from support.tk import bare_frame, host_frame, layout_bare_frame, wait_until

from tkwry import WebView

_UA_CTOR = "tkwry-test-ua-ctor/1.0"
_UA_SETTER = "tkwry-test-ua-setter/1.0"
_INIT_CTOR = "window.__tkwryInit = 'ctor';"
_INIT_SETTER = "window.__tkwryInit = 'setter';"
_PAGE = "<!DOCTYPE html><html><body><p id='t'>create-opts</p></body></html>"


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


def _eval_js_value(web: WebView, root, script: str, *, steps: int = 200) -> object | None:
    """Return the JS value from ``eval_js_with_callback`` (JSON-decoded)."""
    results: list[object] = []

    def callback(raw: str) -> None:
        results.append(_decode_eval_payload(raw))

    per_try = max(steps // 10, 20)
    for _ in range(10):
        web.eval_js_with_callback(script, callback)
        if wait_until(root, lambda: len(results) > 0, steps=per_try):
            return results[-1]
    return None


def _assert_ua_and_init(web: WebView, root, *, ua: str, init_token: str) -> None:
    assert wait_until(root, lambda: web.ready, steps=200)
    got_ua = _eval_js_value(web, root, "navigator.userAgent")
    assert isinstance(got_ua, str), f"expected UA string, got {got_ua!r}"
    assert ua in got_ua, f"expected {ua!r} in navigator.userAgent={got_ua!r}"

    got_init = _eval_js_value(web, root, "window.__tkwryInit")
    assert got_init == init_token, (
        f"expected init script token {init_token!r}, got {got_init!r}"
    )


def test_user_agent_and_init_script_via_constructor(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html=_PAGE,
        user_agent=_UA_CTOR,
        initialization_script=_INIT_CTOR,
    )
    try:
        _assert_ua_and_init(web, tk_root, ua=_UA_CTOR, init_token="ctor")
    finally:
        web.destroy()
        frame.destroy()


def test_user_agent_and_init_script_via_setters_before_create(tk_root) -> None:
    """Ctor kwargs and setters must be equivalent for create-only options."""
    frame = bare_frame(tk_root)
    web = WebView(frame, html=_PAGE)
    web.set_user_agent(_UA_SETTER)
    web.set_initialization_script(_INIT_SETTER)
    layout_bare_frame(frame, width=400, height=300)
    try:
        _assert_ua_and_init(web, tk_root, ua=_UA_SETTER, init_token="setter")
    finally:
        web.destroy()
        frame.destroy()


def test_devtools_open_close_roundtrip(tk_root) -> None:
    """Smoke: binder + platform DevTools open/close stay consistent.

    Native DevTools must be enabled at create (``devtools=True``); otherwise
    ``open_devtools()`` is a no-op on some platforms (observed on macOS).
    May open an inspector window briefly; always close in finally.
    """
    frame = host_frame(tk_root)
    web = WebView(frame, html=_PAGE, devtools=True)
    try:
        assert wait_until(tk_root, lambda: web.ready, steps=200)
        assert web.is_devtools_open() is False

        try:
            web.open_devtools()
        except Exception as exc:
            pytest.skip(f"DevTools unavailable on this platform/runtime: {exc}")

        assert wait_until(tk_root, lambda: web.is_devtools_open(), steps=100), (
            "expected is_devtools_open() after open_devtools()"
        )
        web.close_devtools()
        assert wait_until(
            tk_root,
            lambda: not web.is_devtools_open(),
            steps=100,
        ), "expected DevTools closed after close_devtools()"
    finally:
        try:
            if web.ready and not web.destroyed:
                web.close_devtools()
        except Exception:
            pass
        web.destroy()
        frame.destroy()
