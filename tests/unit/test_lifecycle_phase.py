"""Derived WebViewPhase snapshot tests (no contract changes)."""

from __future__ import annotations

import sys
import tkinter as tk

import pytest

from tkwry import WebView, WebViewPhase


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)


def test_phase_pre_create_before_native(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    assert web.phase is WebViewPhase.PRE_CREATE
    assert web.ready is False
    web.destroy()


def test_phase_create_failed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web._creation_error = RuntimeError("boom")
    assert web.phase is WebViewPhase.CREATE_FAILED
    assert web.ready is False
    web.destroy()


def test_phase_native_before_layout(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web._webview = object()  # type: ignore[assignment]
    monkeypatch.setattr(web, "_layout_ready", lambda: False)
    assert web.phase is WebViewPhase.NATIVE
    assert web.ready is False
    web._webview = None
    web.destroy()


def test_phase_ready_when_laid_out_and_viewable(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web._webview = object()  # type: ignore[assignment]
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    monkeypatch.setattr(web, "_frame_should_show", lambda: True)
    assert web.phase is WebViewPhase.READY
    assert web.ready is True
    web._webview = None
    web.destroy()


def test_phase_hidden_when_ready_but_not_shown(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web._webview = object()  # type: ignore[assignment]
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    monkeypatch.setattr(web, "_frame_should_show", lambda: False)
    assert web.phase is WebViewPhase.HIDDEN
    assert web.ready is True
    web._webview = None
    web.destroy()


@pytest.mark.skipif(
    sys.platform == "linux",
    reason="Linux destroy() flushes native teardown synchronously",
)
def test_phase_tearing_down_while_native_pending(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)

    class _Native:
        def set_visible(self, _visible: bool) -> None:
            return None

        def is_alive(self) -> bool:
            return True

        def destroy(self) -> None:
            return None

    web._webview = _Native()  # type: ignore[assignment]
    web.destroy()
    assert web.phase is WebViewPhase.TEARING_DOWN
    assert web.destroyed is True
    web._native_teardown_pending = None
    assert web.phase is WebViewPhase.DESTROYED


def test_phase_destroyed(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web.destroy()
    assert web.phase is WebViewPhase.DESTROYED


def test_maybe_fire_ready_does_not_rearm_when_hidden(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ready↔map axes stay independent: HIDDEN keeps ready, does not re-fire."""
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=200, height=100)
    web._webview = object()  # type: ignore[assignment]
    web._ready_delivered = True
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    monkeypatch.setattr(web, "_frame_should_show", lambda: False)

    firings: list[bool] = []
    monkeypatch.setattr(web, "_fire_ready", lambda: firings.append(True))

    assert web.phase is WebViewPhase.HIDDEN
    assert web.ready is True
    web._maybe_fire_ready()
    assert firings == []
    assert web._ready_delivered is True

    web._webview = None
    web.destroy()
