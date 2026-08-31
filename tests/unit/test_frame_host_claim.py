"""Regression tests for frame-host claim ordering (D25 / T9)."""

from __future__ import annotations

import sys
import tkinter as tk

import pytest

from tkwry import WebView
from tkwry.session import WebSession


def test_duplicate_frame_ctor_schedules_no_create_after_update(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    try_create_calls: list[int] = []

    def _spy_try_create(self: WebView) -> None:
        try_create_calls.append(id(self))

    monkeypatch.setattr(WebView, "_try_create", _spy_try_create)

    web = WebView(frame, width=400, height=300)
    with pytest.raises(ValueError, match="one WebView per host frame"):
        WebView(frame, width=400, height=300)

    before = len(try_create_calls)
    tk_root.update()
    assert len(try_create_calls) == before
    assert try_create_calls == [id(web)]
    assert web._webview is None


def test_duplicate_frame_ctor_does_not_register_session_webview(tk_root) -> None:
    frame = tk.Frame(tk_root)
    session = WebSession()
    web = WebView(frame, width=400, height=300, session=session)
    with pytest.raises(ValueError, match="one WebView per host frame"):
        WebView(frame, width=400, height=300, session=session)

    assert list(session._webviews) == [web]
    tk_root.update()
    assert list(session._webviews) == [web]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS registry only")
def test_duplicate_frame_ctor_does_not_register_macos_webview(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    toplevel = frame.winfo_toplevel()
    views_before = list(getattr(toplevel, "_tkwry_mac_webviews", []))

    with pytest.raises(ValueError, match="one WebView per host frame"):
        WebView(frame, width=400, height=300)

    views_after = list(getattr(toplevel, "_tkwry_mac_webviews", []))
    assert len(views_after) == len(views_before)
    assert views_after[0]() is web
    tk_root.update()
    assert len(views_after) == 1
    assert views_after[0]() is web
