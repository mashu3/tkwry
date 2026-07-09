"""Tests for WebView creation-size heuristics (no native WebView required)."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import WebView


@pytest.fixture(autouse=True)
def _isolate_from_native_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """Size heuristics only — never build WebKitGTK in headless Linux CI."""
    monkeypatch.setattr("tkwry._runtime.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr("tkwry._core.pump_events", lambda: None, raising=False)
    monkeypatch.setattr(WebView, "_try_create", lambda self: None)


def test_partial_explicit_width_does_not_default_height(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)

    assert web._init_width == 300
    assert web._init_height is None


def test_partial_explicit_height_does_not_default_width(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, height=240)

    assert web._init_width is None
    assert web._init_height == 240


def test_creation_size_waits_for_missing_frame_height(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() is None


def test_creation_size_uses_known_frame_width_with_explicit_height(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, height=240)
    monkeypatch.setattr(frame, "winfo_width", lambda: 500)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() == (500, 240)


def test_creation_size_uses_known_frame_height_with_explicit_width(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 200)

    assert web._creation_size() == (300, 200)


def test_creation_size_uses_both_explicit_dimensions_before_layout(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=800, height=600)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._creation_size() == (800, 600)


def test_creation_size_prefers_laid_out_frame(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=240)
    monkeypatch.setattr(frame, "winfo_width", lambda: 640)
    monkeypatch.setattr(frame, "winfo_height", lambda: 480)

    assert web._creation_size() == (640, 480)


def test_layout_ready_false_before_frame_geometry(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is False


def test_layout_ready_true_when_frame_is_laid_out(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is True


def test_bounds_size_none_when_geometry_unknown(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._bounds_size() is None


def test_bounds_size_uses_explicit_dimensions_before_layout(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._bounds_size() == (300, 200)


def test_bounds_size_uses_frame_width_with_explicit_height(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, height=240)
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 500)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)

    assert web._bounds_size() == (500, 240)


def test_sync_bounds_uses_explicit_size_before_layout(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    bounds: list[tuple[float, float, float, float]] = []

    class _Native:
        def set_bounds(self, x, y, width, height) -> None:
            bounds.append((x, y, width, height))

        def set_visible(self, _visible: bool) -> None:
            return None

        def destroy(self) -> None:
            return None

    web._webview = _Native()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)
    monkeypatch.setattr(frame, "update_idletasks", lambda: None)
    monkeypatch.setattr(
        "tkwry.webview.tk_embed_origin", lambda *_args, **_kwargs: (0, 0)
    )

    assert web._sync_bounds() is True
    assert bounds == [(0.0, 0.0, 300.0, 200.0)]


def test_sync_bounds_skips_1x1_without_explicit_size(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame)
    bounds: list[tuple[float, float, float, float]] = []

    class _Native:
        def set_bounds(self, x, y, width, height) -> None:
            bounds.append((x, y, width, height))

        def set_visible(self, _visible: bool) -> None:
            return None

        def destroy(self) -> None:
            return None

    web._webview = _Native()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)
    monkeypatch.setattr(frame, "update_idletasks", lambda: None)

    assert web._sync_bounds() is False
    assert bounds == []


def test_run_initial_load_reschedules_when_frame_not_ready(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>retry</p>")
    web._webview = object()
    web._initial_load = ("html", "<p>retry</p>")
    scheduled: list[int] = []
    monkeypatch.setattr(
        web, "_frame_ready_for_initial_load", lambda: False, raising=False
    )
    monkeypatch.setattr(
        web,
        "_schedule_initial_load",
        lambda: scheduled.append(1),
        raising=False,
    )

    web._run_initial_load()

    assert scheduled == [1]
    assert web._initial_load == ("html", "<p>retry</p>")


def test_run_initial_load_reschedules_after_exception_until_attempts_exhausted(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>retry</p>")
    native = MagicMock()
    native.load_html.side_effect = RuntimeError("boom")
    web._webview = native
    web._initial_load = ("html", "<p>retry</p>")
    web._initial_load_attempt = 0
    scheduled: list[int] = []
    monkeypatch.setattr(web._frame, "after_idle", lambda _fn: None)
    monkeypatch.setattr(
        web, "_frame_ready_for_initial_load", lambda: True, raising=False
    )
    monkeypatch.setattr(
        web,
        "_schedule_initial_load",
        lambda: scheduled.append(1),
        raising=False,
    )
    monkeypatch.setattr(web, "_sync_bounds", lambda: None, raising=False)
    monkeypatch.setattr(web, "_service_linux_events", lambda **_k: None, raising=False)
    monkeypatch.setattr(web, "_initial_load_attempts", lambda: 2, raising=False)

    web._run_initial_load()
    assert scheduled == [1]
    assert web._initial_load == ("html", "<p>retry</p>")
    assert web._initial_load_attempt == 1

    scheduled.clear()
    web._run_initial_load()
    assert scheduled == []
    assert web._initial_load is None
    assert web._initial_load_attempt == 2


def test_fire_ready_defers_webview_ready_until_idle(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")
    web._webview = object()
    fired_during_maybe_fire: list[bool] = []
    firing = [False]

    original_event_generate = web._frame.event_generate

    def track_event_generate(sequence: str, **kwargs: object) -> None:
        fired_during_maybe_fire.append(firing[0])
        original_event_generate(sequence, **kwargs)

    monkeypatch.setattr(web._frame, "event_generate", track_event_generate)
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    firing[0] = True
    web._maybe_fire_ready()
    firing[0] = False

    assert fired_during_maybe_fire == []
    tk_root.update_idletasks()
    assert fired_during_maybe_fire == [False]


def test_fire_ready_delivers_bind_before_when_ready_callbacks(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")
    web._webview = object()
    order: list[str] = []
    web.bind("<<WebViewReady>>", lambda _evt: order.append("bind"))
    web.when_ready(lambda: order.append("when_ready_1"))
    web.when_ready(lambda: order.append("when_ready_2"))
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    web._maybe_fire_ready()
    tk_root.update_idletasks()

    assert order == ["bind", "when_ready_1", "when_ready_2"]


def test_fire_ready_skips_delivery_if_destroyed_before_idle(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")

    class _Native:
        def destroy(self) -> None:
            return None

    web._webview = _Native()
    bind_fired: list[bool] = []
    when_ready_fired: list[bool] = []
    web.bind("<<WebViewReady>>", lambda _evt: bind_fired.append(True))
    web.when_ready(lambda: when_ready_fired.append(True))
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    web._maybe_fire_ready()
    web.destroy()
    tk_root.update_idletasks()

    assert bind_fired == []
    assert when_ready_fired == []


def test_destroy_clears_native_when_native_destroy_fails(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    class _Native:
        def destroy(self) -> None:
            raise RuntimeError("lock poison")

    web._webview = _Native()
    web.destroy()

    assert web.destroyed is True
    assert web.native is None


def test_reload_cancels_deferred_initial_load(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>A</p>")
    native = MagicMock()
    web._webview = native
    web._initial_load = ("html", "<p>A</p>")
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        web, "_frame_ready_for_initial_load", lambda: True, raising=False
    )
    monkeypatch.setattr(web, "_sync_bounds", lambda: None, raising=False)
    monkeypatch.setattr(web, "_service_linux_events", lambda **_k: None, raising=False)

    web.reload()

    assert web._initial_load is None
    web._run_initial_load()
    native.load_html.assert_not_called()
