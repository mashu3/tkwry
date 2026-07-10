"""Tests for navigation/new-window sync hooks dispatched on the Tk thread."""

from __future__ import annotations

import threading

import pytest

from tkwry import NewWindowResponse, WebView


@pytest.fixture(autouse=True)
def _noop_gtk_pumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tkwry._core.pump_events", lambda: None, raising=False)
    monkeypatch.setattr("tkwry._runtime.GtkPump.attach", lambda _widget: None)


def _make_web(tk_root):
    import tkinter as tk

    frame = tk.Frame(tk_root)
    return frame, WebView(frame)


def test_native_navigation_runs_handler_on_tk_thread(tk_root) -> None:
    _frame, web = _make_web(tk_root)
    seen: list[int] = []

    def handler(url: str) -> bool:
        seen.append(threading.get_ident())
        return url.startswith("https://")

    web.set_on_navigation(handler)
    web._ensure_tk_wakeup_pipe()
    web._ensure_event_poll()

    result_holder: list[bool] = []
    error_holder: list[BaseException] = []

    def worker() -> None:
        try:
            result_holder.append(web._native_navigation("https://example.com/"))
        except BaseException as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(200):
        tk_root.update_idletasks()
        tk_root.update()
        web._poll_events()
        if not thread.is_alive():
            break

    thread.join(timeout=2.0)
    assert not error_holder
    assert result_holder == [True]
    assert seen == [web._tk_thread_id]


def test_native_new_window_runs_handler_on_tk_thread(tk_root) -> None:
    _frame, web = _make_web(tk_root)
    seen: list[int] = []

    def handler(url: str) -> NewWindowResponse:
        seen.append(threading.get_ident())
        return NewWindowResponse.Deny

    web.set_on_new_window(handler)
    web._ensure_tk_wakeup_pipe()
    web._ensure_event_poll()

    result_holder: list[NewWindowResponse] = []

    def worker() -> None:
        result_holder.append(web._native_new_window("https://example.com/popup"))

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(200):
        tk_root.update_idletasks()
        tk_root.update()
        web._poll_events()
        if not thread.is_alive():
            break

    thread.join(timeout=2.0)
    assert result_holder == [NewWindowResponse.Deny]
    assert seen == [web._tk_thread_id]


def test_needs_event_poll_when_navigation_handler_set(tk_root) -> None:
    _frame, web = _make_web(tk_root)
    assert web._needs_event_poll() is False
    web.set_on_navigation(lambda _url: True)
    assert web._needs_event_poll() is True
