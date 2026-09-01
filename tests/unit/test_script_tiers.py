"""F20: inject_script / execute_script / add_init_script tiers."""

from __future__ import annotations

import tkinter as tk

import pytest

from tkwry import PageLoadEvent, WebView, WebViewDestroyedError


def test_add_init_script_merges_into_effective_init(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(
        frame,
        html="<p>x</p>",
        initialization_script="window.__a = 1;",
    )
    web.add_init_script("window.__b = 2;")
    script = web._effective_initialization_script()
    assert script is not None
    assert "window.__a = 1;" in script
    assert "window.__b = 2;" in script
    web.destroy()
    frame.destroy()


def test_inject_before_create_is_add_init(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.inject_script("window.__inj = 1;")
    assert web._init_scripts == ["window.__inj = 1;"]
    assert web._inject_scripts == []
    script = web._effective_initialization_script()
    assert script is not None
    assert "window.__inj = 1;" in script
    web.destroy()
    frame.destroy()


def test_add_init_script_rejects_empty_and_after_create(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    with pytest.raises(ValueError, match="non-empty"):
        web.add_init_script("   ")
    web._webview = object()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="after the native"):
        web.add_init_script("void 0")
    web.destroy()
    frame.destroy()


def test_execute_script_aliases_eval_js(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    calls: list[tuple[str, object]] = []

    def fake_eval(script: str, *, on_error=None) -> None:
        calls.append((script, on_error))

    monkeypatch.setattr(web, "eval_js", fake_eval)
    web.execute_script("1+1")
    assert calls == [("1+1", None)]
    web.destroy()
    frame.destroy()


def test_inject_after_ready_stores_and_evals(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    evals: list[str] = []
    listening: list[bool] = []

    class _Native:
        def set_page_load_listening(self, wanted: bool) -> None:
            listening.append(wanted)

    monkeypatch.setattr(web, "eval_js", lambda s, *, on_error=None: evals.append(s))
    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    web._webview = _Native()  # type: ignore[assignment]
    web.inject_script("window.__p = 1;")
    assert web._inject_scripts == ["window.__p = 1;"]
    assert evals == ["window.__p = 1;"]
    assert listening == [True]
    assert web._page_load_listening_wanted() is True
    web.destroy()
    frame.destroy()


def test_inject_scripts_rerun_on_page_load_started(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    evals: list[str] = []

    class _Native:
        def drain_page_load_events(self):
            return [(PageLoadEvent.Started, "https://example.com/")]

    monkeypatch.setattr(web, "_run_eval_js", lambda s, on_error=None: evals.append(s))
    web._webview = _Native()  # type: ignore[assignment]
    web._inject_scripts = ["window.__p = 1;"]
    web._deliver_page_load_events()
    assert evals == ["window.__p = 1;"]
    web.destroy()
    frame.destroy()


def test_inject_scripts_rerun_all_on_page_load_started(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    evals: list[str] = []

    class _Native:
        def drain_page_load_events(self):
            return [(PageLoadEvent.Started, "https://example.com/")]

    monkeypatch.setattr(web, "_run_eval_js", lambda s, on_error=None: evals.append(s))
    web._webview = _Native()  # type: ignore[assignment]
    web._inject_scripts = ["window.__a = 1;", "window.__b = 2;"]
    web._deliver_page_load_events()
    assert evals == ["window.__a = 1;", "window.__b = 2;"]
    web.destroy()
    frame.destroy()


def test_script_tiers_raise_after_destroy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.destroy()
    with pytest.raises(WebViewDestroyedError, match="add_init_script"):
        web.add_init_script("void 0")
    with pytest.raises(WebViewDestroyedError, match="inject_script"):
        web.inject_script("void 0")
    with pytest.raises(WebViewDestroyedError, match="execute_script"):
        web.execute_script("1")
    frame.destroy()
