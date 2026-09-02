"""WebSession construction and validation (no native WebView required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry import WebSession, WebView


def test_session_creates_data_directory(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    assert not profile.exists()
    session = WebSession(data_directory=profile)
    assert profile.is_dir()
    assert session.data_directory == profile.resolve()
    assert session.ephemeral is False


def test_session_ephemeral_has_no_directory() -> None:
    session = WebSession(ephemeral=True)
    assert session.data_directory is None
    assert session.ephemeral is True


def test_session_rejects_ephemeral_with_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        WebSession(data_directory=tmp_path, ephemeral=True)


def test_session_incognito_is_ephemeral_alias() -> None:
    session = WebSession(incognito=True)
    assert session.ephemeral is True
    assert session.data_directory is None
    assert session.native is not None
    assert session.native.ephemeral is True


def test_session_rejects_incognito_with_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        WebSession(data_directory=tmp_path, incognito=True)


def test_webview_incognito_owns_ephemeral_session(tk_root) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", incognito=True)
    try:
        assert web._owned_session is not None
        assert web._session is web._owned_session
        assert web._session.ephemeral is True
    finally:
        web.destroy()
        frame.destroy()


def test_webview_rejects_incognito_with_session(tk_root) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="only one of"):
        WebView(frame, session=session, incognito=True)
    frame.destroy()


def test_native_session_exposed() -> None:
    session = WebSession(ephemeral=True)
    assert session.native is not None
    assert session.native.ephemeral is True


def test_session_bind_app_root_mismatch(tmp_path: Path) -> None:
    session = WebSession(data_directory=tmp_path / "profile")
    session._bind_app_root("/tmp/app-a")
    session._bind_app_root("/tmp/app-a")
    assert session.app_root == Path("/tmp/app-a")
    with pytest.raises(ValueError, match="same app="):
        session._bind_app_root("/tmp/app-b")


def test_ephemeral_session_allows_distinct_app_roots(tmp_path: Path) -> None:
    session = WebSession(ephemeral=True)
    session._bind_app_root(str(tmp_path / "a"))
    session._bind_app_root(str(tmp_path / "b"))
    assert session.app_root is None


def test_emit_all_broadcasts_to_registered_views(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    web_a = WebView(frame_a, html="<p>a</p>", session=session)
    web_b = WebView(frame_b, html="<p>b</p>", session=session)
    calls: list[tuple[object, str, object]] = []

    def fake_emit(self: WebView, event: str, data: object = None) -> None:
        calls.append((self, event, data))

    monkeypatch.setattr(WebView, "_emit_eligible", lambda self: True)
    monkeypatch.setattr(WebView, "emit", fake_emit)

    assert session.emit_all("ping", {"n": 1}) == 2
    assert sorted((id(web), event, data) for web, event, data in calls) == sorted(
        [
            (id(web_a), "ping", {"n": 1}),
            (id(web_b), "ping", {"n": 1}),
        ]
    )

    web_a.destroy()
    web_b.destroy()
    frame_a.destroy()
    frame_b.destroy()


def test_emit_all_skips_ineligible_and_destroyed(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    web_a = WebView(frame_a, html="<p>a</p>", session=session)
    web_b = WebView(frame_b, html="<p>b</p>", session=session)
    calls: list[object] = []

    def eligible(self: WebView) -> bool:
        return self is web_a

    monkeypatch.setattr(WebView, "_emit_eligible", eligible)
    monkeypatch.setattr(
        WebView, "emit", lambda self, event, data=None: calls.append(self)
    )

    assert session.emit_all("ping") == 1
    assert calls == [web_a]

    web_a.destroy()
    assert session.emit_all("ping") == 0

    web_b.destroy()
    frame_a.destroy()
    frame_b.destroy()


def test_emit_all_continues_after_sibling_emit_error(
    tk_root, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    frame_c = tk.Frame(tk_root)
    web_a = WebView(frame_a, html="<p>a</p>", session=session)
    web_b = WebView(frame_b, html="<p>b</p>", session=session)
    web_c = WebView(frame_c, html="<p>c</p>", session=session)
    calls: list[object] = []

    def emit(self: WebView, event: str, data: object = None) -> None:
        if self is web_b:
            raise RuntimeError("sibling boom")
        calls.append(self)

    monkeypatch.setattr(WebView, "_emit_eligible", lambda self: True)
    monkeypatch.setattr(WebView, "emit", emit)

    assert session.emit_all("ping") == 2
    assert set(calls) == {web_a, web_c}
    err = capsys.readouterr().err
    assert "sibling boom" in err

    web_a.destroy()
    web_b.destroy()
    web_c.destroy()
    frame_a.destroy()
    frame_b.destroy()
    frame_c.destroy()


def test_emit_all_rejects_empty_event_and_bad_payload(tk_root) -> None:
    import tkinter as tk

    from tkwry import RpcSerializationError

    session = WebSession(ephemeral=True)
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>a</p>", session=session)
    with pytest.raises(ValueError, match="non-empty"):
        session.emit_all("")
    with pytest.raises(RpcSerializationError):
        session.emit_all("x", object())
    web.destroy()
    frame.destroy()


def test_emit_eligible_respects_untrusted_and_origin(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>a</p>")
    web._webview = object()  # type: ignore[assignment]
    monkeypatch.setattr(web, "_layout_ready", lambda: True)
    assert web._emit_eligible() is True

    web._untrusted = True
    assert web._emit_eligible() is False
    web._untrusted = False

    web._bridge_origins = frozenset({"https://trusted.example"})
    web._webview = type(  # type: ignore[assignment]
        "N", (), {"url": lambda self: "https://evil.example/"}
    )()
    assert web._emit_eligible() is False
    web._webview = type(  # type: ignore[assignment]
        "N", (), {"url": lambda self: "https://trusted.example/"}
    )()
    assert web._emit_eligible() is True

    web.destroy()
    frame.destroy()


def test_webview_shared_session_rejects_mismatched_app(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    app_a = tmp_path / "a"
    app_b = tmp_path / "b"
    app_a.mkdir()
    app_b.mkdir()
    (app_a / "index.html").write_text("<p>a</p>", encoding="utf-8")
    (app_b / "index.html").write_text("<p>b</p>", encoding="utf-8")
    session = WebSession(data_directory=tmp_path / "profile")
    frame_a = tk.Frame(tk_root, width=400, height=300)
    frame_b = tk.Frame(tk_root, width=400, height=300)
    frame_a.pack_propagate(False)
    frame_b.pack_propagate(False)
    frame_a.pack()
    frame_b.pack()
    web_a = WebView(frame_a, width=400, height=300, app=app_a, session=session)

    class _Native:
        def destroy(self) -> None:
            pass

        def gc_companion(self) -> object:
            return object()

    def _fake_native(*args: object, **kwargs: object) -> _Native:
        return _Native()

    monkeypatch.setattr(web_a, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr("tkwry.webview.NativeWebView", _fake_native)
    monkeypatch.setattr(web_a, "_bind_native_gc", lambda _n: None, raising=False)
    monkeypatch.setattr(web_a, "_sync_bounds", lambda: True, raising=False)
    monkeypatch.setattr(web_a, "_maybe_fire_ready", lambda: None, raising=False)
    monkeypatch.setattr(web_a, "_schedule_initial_load", lambda: None, raising=False)
    monkeypatch.setattr(web_a, "_needs_event_poll", lambda: False, raising=False)
    web_a._try_create()
    assert session.app_root == app_a.resolve()

    web_b = WebView(frame_b, width=400, height=300, app=app_b, session=session)
    monkeypatch.setattr(web_b, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(web_b, "_sync_bounds", lambda: True, raising=False)
    monkeypatch.setattr(web_b, "_maybe_fire_ready", lambda: None, raising=False)
    monkeypatch.setattr(web_b, "_schedule_initial_load", lambda: None, raising=False)
    monkeypatch.setattr(web_b, "_needs_event_poll", lambda: False, raising=False)
    monkeypatch.setattr(web_b, "_bind_native_gc", lambda _n: None, raising=False)
    web_b._cancel_deferred_callbacks()
    with pytest.raises(ValueError, match="cannot use"):
        web_b._try_create()

    web_a.destroy()
    frame_a.destroy()
    frame_b.destroy()


def test_session_app_root_bind_deferred_until_create(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tkinter as tk

    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<p>x</p>", encoding="utf-8")
    session = WebSession(data_directory=tmp_path / "profile")
    frame = tk.Frame(tk_root, width=400, height=300)
    frame.pack_propagate(False)
    frame.pack()
    web = WebView(frame, width=400, height=300, app=app, session=session)
    assert session._app_root is None

    class _Native:
        def destroy(self) -> None:
            pass

        def gc_companion(self) -> object:
            return object()

    monkeypatch.setattr(web, "_layout_ready", lambda: True, raising=False)
    monkeypatch.setattr(
        "tkwry.webview.NativeWebView", lambda *args, **kwargs: _Native()
    )
    monkeypatch.setattr(web, "_bind_native_gc", lambda _n: None, raising=False)
    monkeypatch.setattr(web, "_sync_bounds", lambda: True, raising=False)
    monkeypatch.setattr(web, "_maybe_fire_ready", lambda: None, raising=False)
    monkeypatch.setattr(web, "_schedule_initial_load", lambda: None, raising=False)
    monkeypatch.setattr(web, "_needs_event_poll", lambda: False, raising=False)
    web._try_create()
    assert session.app_root == app.resolve()

    web.destroy()
    frame.destroy()


def test_session_close_destroys_registered_webviews(tk_root) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    web_a = WebView(frame_a, html="<p>a</p>", session=session)
    web_b = WebView(frame_b, html="<p>b</p>", session=session)

    session.close()

    assert session.closed is True
    assert web_a.destroyed is True
    assert web_b.destroyed is True
    with pytest.raises(ValueError, match="closed"):
        session.emit_all("ping")
    with pytest.raises(ValueError, match="closed"):
        _ = session.native
    session.close()

    frame_a.destroy()
    frame_b.destroy()


def test_session_close_rejects_new_webview(tk_root) -> None:
    import tkinter as tk

    session = WebSession(ephemeral=True)
    session.close()
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="closed"):
        WebView(frame, html="<p>x</p>", session=session)
    frame.destroy()
