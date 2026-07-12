"""Tests for WebView creation-size heuristics (no native WebView required)."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import WebView
from tkwry._linux import GtkPump
from tkwry.exceptions import WebViewCreationError, WebViewDestroyedError
from tkwry.webview import _CREATE_MAX_ATTEMPTS, _FLUSH_LOAD_MAX_ATTEMPTS

_real_try_create = WebView._try_create
_real_gtk_attach = GtkPump.attach


@pytest.fixture(autouse=True)
def _isolate_from_native_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """Size heuristics only — never build WebKitGTK in headless Linux CI."""
    monkeypatch.setattr("tkwry._linux.GtkPump.attach", lambda _widget: None)
    monkeypatch.setattr(
        "tkwry._core.pump_events", lambda max_iterations=None: False, raising=False
    )
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


def test_creation_size_rejects_unit_explicit_width(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="width must be >="):
        WebView(frame, width=1, height=200)
    frame.destroy()


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
    web = WebView(frame)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is False


def test_layout_ready_true_with_init_size_before_frame_layout(
    tk_root, monkeypatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 1)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._layout_ready() is True


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
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    monkeypatch.setattr("tkwry.webview.sys.platform", "darwin")
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
    assert scheduled == [1]
    assert web._initial_load == ("html", "<p>retry</p>")
    assert web._initial_load_attempt == 0
    assert "initial load failed after 2 attempt(s); will retry" in (
        capsys.readouterr().err
    )


def test_destroy_rejects_layout_and_bind(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web.destroy()

    from tkwry.exceptions import WebViewDestroyedError

    with pytest.raises(WebViewDestroyedError, match="pack"):
        web.pack()
    with pytest.raises(WebViewDestroyedError, match="bind"):
        web.bind("<<WebViewReady>>", lambda _evt: None)


def test_macos_focused_true_defers_until_ready(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkwry._parent import EmbedParent

    frame = tk.Frame(tk_root)
    monkeypatch.setattr("tkwry.webview.sys.platform", "darwin")
    monkeypatch.setattr(
        "tkwry.webview.tk_embed_parent",
        lambda _frame: EmbedParent(1),
    )
    monkeypatch.setattr("tkwry.webview.GtkPump.ensure_attached", lambda _frame: None)
    monkeypatch.setattr(
        "tkwry.webview._register_macos_webview",
        lambda _web: None,
        raising=False,
    )

    web = WebView(frame, width=400, height=300, focused=True)

    assert web._focused is False
    assert web._focus_when_ready is True


def test_macos_focus_when_ready_calls_focus_on_idle(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    web._webview = native
    web._focus_when_ready = True
    focus_calls: list[bool] = []
    monkeypatch.setattr(web, "focus", lambda: focus_calls.append(True), raising=False)
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    web._maybe_fire_ready()
    tk_root.update_idletasks()

    assert focus_calls == [1]
    assert web._focus_when_ready is False


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


def test_fire_ready_delivers_all_when_ready_callbacks_despite_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300, html="<p>ready</p>")

    class _Native:
        def destroy(self) -> None:
            return None

    web._webview = _Native()
    order: list[int] = []
    web.when_ready(lambda: (order.append(1), web.destroy()))
    web.when_ready(lambda: order.append(2))
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    web._maybe_fire_ready()
    tk_root.update_idletasks()

    assert order == [1, 2]
    assert web.destroyed


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


def test_layout_ready_false_when_not_viewable(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: False)
    monkeypatch.setattr("tkwry.webview.sys.platform", "darwin")

    assert web._layout_ready() is False
    assert web.ready is False


def test_layout_ready_ignores_viewable_on_linux_headless(tk_root, monkeypatch) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=300, height=200)
    web._webview = object()
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: False)
    monkeypatch.setattr("tkwry.webview.sys.platform", "linux")

    assert web._layout_ready() is True
    assert web.ready is True


def test_frame_ready_for_initial_load_checks_geometry_on_linux(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._webview = object()
    monkeypatch.setattr("tkwry.webview.sys.platform", "linux")
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 1)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: True)

    assert web._frame_ready_for_initial_load() is False

    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: False)

    assert web._frame_ready_for_initial_load() is True


def test_second_webview_on_same_frame_raises(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    with pytest.raises(ValueError, match="one WebView per host frame"):
        WebView(frame, width=400, height=300)
    web.destroy()
    WebView(frame, width=400, height=300)


def test_maybe_fire_ready_fires_once_after_unmap_remap(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._webview = object()
    viewable = [True]
    monkeypatch.setattr(frame, "winfo_exists", lambda: True)
    monkeypatch.setattr(frame, "winfo_width", lambda: 400)
    monkeypatch.setattr(frame, "winfo_height", lambda: 300)
    monkeypatch.setattr(frame, "winfo_viewable", lambda: viewable[0])
    fired: list[int] = []
    monkeypatch.setattr(web, "_fire_ready", lambda: fired.append(1), raising=False)

    web._maybe_fire_ready()
    assert fired == [1]
    assert web._ready_pending
    assert not web._ready_delivered

    viewable[0] = False
    web._maybe_fire_ready()
    assert web._ready_pending
    assert fired == [1]

    viewable[0] = True
    web._maybe_fire_ready()
    assert fired == [1]
    assert web._ready_pending


def test_bind_after_ready_flag_before_idle_fires_once(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._webview = object()
    counts: list[int] = []
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)

    web._maybe_fire_ready()
    web.bind("<<WebViewReady>>", lambda _evt: counts.append(1))
    tk_root.update_idletasks()

    assert counts == [1]


def test_bind_after_ready_falls_back_when_probe_misses(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    web._webview = object()
    web._ready_delivered = True
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(web._frame, "event_generate", lambda *_a, **_k: None)

    events: list[tk.Event] = []
    web.bind("<<WebViewReady>>", lambda evt: events.append(evt))
    tk_root.update_idletasks()

    assert len(events) == 1
    assert events[0].widget is frame


def test_destroy_clears_native_when_native_destroy_fails(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    class _Native:
        def destroy(self) -> None:
            raise RuntimeError("lock poison")

    web._webview = _Native()
    web.destroy()

    assert web.destroyed is True
    with pytest.raises(WebViewDestroyedError):
        _ = web.native


def test_destroy_clears_native_when_native_destroy_deferred(
    tk_root, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    class _Native:
        def __init__(self) -> None:
            self.visible = True
            self.alive = True
            self.destroy_calls = 0

        def set_visible(self, visible: bool) -> None:
            self.visible = visible

        def is_alive(self) -> bool:
            return self.alive

        def destroy(self) -> None:
            self.destroy_calls += 1
            if self.destroy_calls == 1:
                return
            self.alive = False

    native = _Native()
    web._webview = native
    web.destroy()

    assert web.destroyed is True
    assert web._webview is None
    assert native.visible is False
    assert web._native_teardown_pending is native
    web._finish_native_teardown()
    assert web._native_teardown_pending is None
    assert native.alive is False
    with pytest.raises(WebViewDestroyedError):
        _ = web.native

    frame2 = tk.Frame(tk_root)
    web2 = WebView(frame2, width=400, height=300)

    class _NeverDiesNative:
        def __init__(self) -> None:
            self.visible = True
            self.destroy_calls = 0
            self.force_destroy_calls = 0

        def set_visible(self, visible: bool) -> None:
            self.visible = visible

        def is_alive(self) -> bool:
            return True

        def destroy(self) -> None:
            self.destroy_calls += 1

        def force_destroy(self) -> None:
            self.force_destroy_calls += 1

    stuck = _NeverDiesNative()
    web2._webview = stuck
    web2._event_poll_active = True
    web2.destroy()

    assert web2._native_teardown_pending is stuck
    for _ in range(100):
        web2._poll_events()
        if web2._native_teardown_pending is None:
            break

    assert web2._native_teardown_pending is None
    assert web2._event_poll_active is False
    assert stuck.force_destroy_calls == 1
    assert "native teardown timed out" in capsys.readouterr().err


def test_destroy_stops_event_poll_after_native_teardown(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)

    class _Native:
        def __init__(self) -> None:
            self.alive = True
            self.destroy_calls = 0

        def set_visible(self, _visible: bool) -> None:
            return None

        def is_alive(self) -> bool:
            return self.alive

        def destroy(self) -> None:
            self.destroy_calls += 1
            if self.destroy_calls >= 2:
                self.alive = False

    native = _Native()
    web._webview = native
    web._event_poll_active = True
    original_after = frame.after

    def after(delay, func=None, *args):
        if func is web._poll_events:
            return ""
        if func is None:
            return original_after(delay)
        return original_after(delay, func, *args)

    monkeypatch.setattr(frame, "after", after)
    web.destroy()

    assert web._webview is None
    assert web._native_teardown_pending is native

    for _ in range(5):
        web._poll_events()
        if web._native_teardown_pending is None:
            break

    assert web._native_teardown_pending is None
    assert web._event_poll_active is False


def test_unmap_does_not_detach_gtk_pump(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    if sys.platform != "linux":
        pytest.skip("Linux-only")

    import tkinter as tk

    from tkwry._linux import GtkPump

    detach_calls: list[object] = []
    monkeypatch.setattr(
        "tkwry.webview.GtkPump.detach",
        lambda widget: detach_calls.append(widget),
    )
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", lambda: None, raising=False)
    monkeypatch.setattr(tk_root, "after", lambda *_a, **_k: "after-id")
    monkeypatch.setattr(GtkPump, "attach", _real_gtk_attach)

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    GtkPump.attach(frame)
    assert id(tk_root) in GtkPump._by_root_key

    frame.event_generate("<Unmap>")
    tk_root.update_idletasks()

    assert detach_calls == []
    web.destroy()
    assert len(detach_calls) == 1
    GtkPump.detach(frame)


def test_reload_clears_pending_flush_load(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    web._webview = native
    web._pending_load = ("url", "https://example.com/stale")
    web._flush_load_scheduled = True
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(web, "_service_linux_events", lambda **_k: None, raising=False)

    web.reload()

    assert web._pending_load is None
    native.load_url.assert_not_called()
    web._flush_load()
    native.load_url.assert_not_called()


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


def test_try_create_stops_after_max_attempts(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)
    web = WebView(frame, width=400, height=300)
    scheduled: list[int] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("native create failed")

    monkeypatch.setattr("tkwry.webview.NativeWebView", boom)
    monkeypatch.setattr(WebView, "_try_create", _real_try_create)
    monkeypatch.setattr(
        web, "_schedule_try_create", lambda **_k: scheduled.append(1), raising=False
    )
    web._create_attempt = _CREATE_MAX_ATTEMPTS - 1

    web._try_create()

    assert web._webview is None
    assert scheduled == []
    assert web._create_attempt == _CREATE_MAX_ATTEMPTS
    assert web._creation_error is not None
    with pytest.raises(WebViewCreationError, match="native creation failed"):
        web._require_ready("eval_js")


def test_try_create_retries_after_native_failure(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)
    web = WebView(frame, width=400, height=300)
    scheduled: list[int] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("native create failed")

    monkeypatch.setattr("tkwry.webview.NativeWebView", boom)
    monkeypatch.setattr(WebView, "_try_create", _real_try_create)
    monkeypatch.setattr(web, "_schedule_try_create", lambda **_k: scheduled.append(1))
    scheduled.clear()

    web._try_create()

    assert web._webview is None
    assert scheduled == [1]


def test_flush_load_retries_without_clearing_pending_on_failure(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    frame = tk.Frame(tk_root)
    web = WebView(frame, width=400, height=300)
    native = MagicMock()
    native.load_url.side_effect = RuntimeError("boom")
    web._webview = native
    web._pending_load = ("url", "https://example.com")
    scheduled: list[int] = []
    monkeypatch.setattr(
        web, "_schedule_flush_load", lambda **_k: scheduled.append(1), raising=False
    )
    monkeypatch.setattr(web, "_sync_bounds", lambda: None, raising=False)
    monkeypatch.setattr(web, "_service_linux_events", lambda **_k: None, raising=False)

    web._flush_load()
    assert web._pending_load == ("url", "https://example.com")
    assert scheduled == [1]
    assert web._flush_load_attempt == 1

    web._flush_load_attempt = _FLUSH_LOAD_MAX_ATTEMPTS - 1
    web._flush_load()
    assert web._pending_load == ("url", "https://example.com")
    assert web._flush_load_attempt == 0


def _web_with_creation_failure(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> tuple[tk.Frame, WebView]:
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    tk_root.update_idletasks()
    monkeypatch.setattr(frame, "after_idle", lambda _fn: None)
    web = WebView(frame, width=400, height=300, url="https://example.com/initial")
    scheduled: list[int] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("native create failed")

    monkeypatch.setattr("tkwry.webview.NativeWebView", boom)
    monkeypatch.setattr(WebView, "_try_create", _real_try_create)
    monkeypatch.setattr(
        web, "_schedule_try_create", lambda **_k: scheduled.append(1), raising=False
    )
    web._create_attempt = _CREATE_MAX_ATTEMPTS - 1
    web._try_create()
    assert web._creation_error is not None
    assert web._webview is None
    return frame, web


def test_creation_failure_raises_on_load_url(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    with pytest.raises(WebViewCreationError, match="load_url"):
        web.load_url("https://example.com/new")
    assert web._pending_url == "https://example.com/initial"


def test_creation_failure_raises_on_load_html(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    with pytest.raises(WebViewCreationError, match="load_html"):
        web.load_html("<p>new</p>")
    assert web._pending_html is None


def test_creation_failure_raises_on_sync_bounds(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    with pytest.raises(WebViewCreationError, match="sync_bounds"):
        web.sync_bounds()


def test_creation_failure_raises_on_handler_setters(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    with pytest.raises(WebViewCreationError, match="set_ipc_handler"):
        web.set_ipc_handler(lambda _msg: None)
    with pytest.raises(WebViewCreationError, match="set_on_navigation"):
        web.set_on_navigation(lambda _url: True)
    with pytest.raises(WebViewCreationError, match="set_on_page_load"):
        web.set_on_page_load(lambda _evt, _url: None)
    with pytest.raises(WebViewCreationError, match="set_on_title_changed"):
        web.set_on_title_changed(lambda _title: None)
    with pytest.raises(WebViewCreationError, match="set_on_new_window"):
        web.set_on_new_window(lambda _url: None)
    with pytest.raises(WebViewCreationError, match="set_drag_drop_handler"):
        web.set_drag_drop_handler(lambda *_args: None)
    web.set_ipc_handler(None)
    web.set_on_navigation(None)


def test_creation_failed_public_api(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    assert web.creation_failed is True
    assert isinstance(web.creation_error, RuntimeError)
    assert str(web.creation_error) == "native create failed"


def test_url_property_reports_pending_html(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>hi</p>")
    assert web.url == "<html>"
    assert "<html>" in repr(web)


def test_configure_does_not_retry_after_creation_failure(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    tries: list[int] = []
    monkeypatch.setattr(web, "_try_create", lambda: tries.append(1), raising=False)
    event = tk.Event()
    event.widget = frame
    web._on_configure(event)
    tk_root.update_idletasks()
    assert tries == []


def test_creation_error_survives_destroy(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame, web = _web_with_creation_failure(tk_root, monkeypatch)
    err = web.creation_error
    web.destroy()
    assert web.creation_failed is True
    assert web.creation_error is err
