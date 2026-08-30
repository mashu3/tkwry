"""Ctor vs setter equivalence for dual handler APIs (Beta B3 partial)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from support.linux import noop_linux_runtime

from tkwry import NewWindowResponse, PageLoadEvent, WebView


@pytest.fixture(autouse=True)
def _noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    noop_linux_runtime(monkeypatch)


def _make_frame(tk_root):
    import tkinter as tk

    return tk.Frame(tk_root)


def _snapshot(web: WebView) -> dict[str, Any]:
    return {
        "needs_poll": web._needs_event_poll(),
        "download_policy": web._download_policy_active(),
        "on_navigation": web._on_navigation is not None,
        "on_page_load": web._on_page_load is not None,
        "on_title_changed": web._on_title_changed is not None,
        "on_new_window": web._on_new_window is not None,
        "on_download": web._on_download is not None,
        "on_download_complete": web._on_download_complete is not None,
        "drag_drop": web._drag_drop_handler is not None,
    }


def _equivalence_case(
    ctor_kw: str,
    setter: str,
    handler: object,
) -> tuple[str, str, object]:
    return (ctor_kw, setter, handler)


@pytest.mark.parametrize(
    ("ctor_kw", "setter", "handler"),
    [
        _equivalence_case("on_navigation", "set_on_navigation", lambda _url: True),
        _equivalence_case(
            "on_page_load",
            "set_on_page_load",
            lambda _evt, _url: None,
        ),
        _equivalence_case(
            "on_title_changed", "set_on_title_changed", lambda _title: None
        ),
        _equivalence_case(
            "on_new_window",
            "set_on_new_window",
            lambda _url: NewWindowResponse.Deny,
        ),
        _equivalence_case("on_download", "set_on_download", lambda _url, _dest: True),
        _equivalence_case(
            "on_download_complete",
            "set_on_download_complete",
            lambda _url, _dest, _ok: None,
        ),
        _equivalence_case(
            "drag_drop_handler",
            "set_drag_drop_handler",
            lambda _evt, _paths, _pos: None,
        ),
    ],
)
def test_handler_ctor_matches_setter(
    tk_root,
    ctor_kw: str,
    setter: str,
    handler: object,
) -> None:
    frame_ctor = _make_frame(tk_root)
    frame_set = _make_frame(tk_root)
    try:
        web_ctor = WebView(frame_ctor, html="<p>ctor</p>", **{ctor_kw: handler})
        web_set = WebView(frame_set, html="<p>setter</p>")
        getattr(web_set, setter)(handler)
        assert _snapshot(web_ctor) == _snapshot(web_set)
    finally:
        web_ctor.destroy()
        web_set.destroy()


@pytest.mark.parametrize(
    ("setter", "register"),
    [
        ("set_on_navigation", lambda w: w.set_on_navigation(lambda _u: True)),
        (
            "set_on_page_load",
            lambda w: w.set_on_page_load(lambda _e, _u: None),
        ),
        (
            "set_on_title_changed",
            lambda w: w.set_on_title_changed(lambda _t: None),
        ),
        (
            "set_on_new_window",
            lambda w: w.set_on_new_window(lambda _u: NewWindowResponse.Deny),
        ),
        ("set_on_download", lambda w: w.set_on_download(lambda _u, _d: True)),
        (
            "set_on_download_complete",
            lambda w: w.set_on_download_complete(lambda *_a: None),
        ),
        (
            "set_drag_drop_handler",
            lambda w: w.set_drag_drop_handler(lambda *_a: None),
        ),
    ],
)
def test_clearing_setter_restores_default(
    tk_root, setter: str, register: Callable[[WebView], None]
) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    try:
        baseline = _snapshot(web)
        register(web)
        assert _snapshot(web) != baseline
        getattr(web, setter)(None)
        assert _snapshot(web) == baseline
    finally:
        web.destroy()


def test_setter_after_ctor_kw_last_wins(tk_root) -> None:
    first: list[str] = []
    second: list[str] = []

    def handler_a(_evt: PageLoadEvent, url: str) -> None:
        first.append(url)

    def handler_b(_evt: PageLoadEvent, url: str) -> None:
        second.append(url)

    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>", on_page_load=handler_a)
    try:
        assert web._on_page_load is handler_a
        web.set_on_page_load(handler_b)
        assert web._on_page_load is handler_b
        assert web._on_page_load is not handler_a
    finally:
        web.destroy()


def test_on_callback_error_ctor_matches_setter(tk_root) -> None:
    handler = lambda _exc, _kind: None  # noqa: E731
    frame_ctor = _make_frame(tk_root)
    frame_set = _make_frame(tk_root)
    web_ctor = WebView(frame_ctor, html="<p>ctor</p>", on_callback_error=handler)
    web_set = WebView(frame_set, html="<p>setter</p>")
    try:
        web_set.set_on_callback_error(handler)
        assert web_ctor._on_callback_error is handler
        assert web_set._on_callback_error is handler
    finally:
        web_ctor.destroy()
        web_set.destroy()


def test_clear_on_callback_error_restores_default(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    try:
        assert web._on_callback_error is None
        web.set_on_callback_error(lambda _e, _k: None)
        assert web._on_callback_error is not None
        web.set_on_callback_error(None)
        assert web._on_callback_error is None
    finally:
        web.destroy()
