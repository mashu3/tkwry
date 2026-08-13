"""Content loading, callbacks, and drag-and-drop queueing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.tk import host_frame, pump, wait_until

from tkwry import DragDropEvent, PageLoadEvent, WebView, rpc_cancelled
from tkwry.exceptions import WebViewDestroyedError


def test_create_with_html_and_destroy(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p id='t'>ok</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    assert web.native is not None

    web.destroy()
    with pytest.raises(WebViewDestroyedError):
        _ = web.native

    frame.destroy()


def test_url_none_for_inline_html_then_concrete_after_load_url(
    tk_root, tmp_path: Path
) -> None:
    """Inline HTML has no document URL; ``load_url`` must expose a concrete URI.

    On macOS this is the non-panicking ``url()`` path (WKWebView.URL may be nil).
    """
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>inline</p>")

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    assert web.native is not None
    assert web.native.url() is None
    assert web.url is None

    page = tmp_path / "concrete.html"
    page.write_text("<p>file</p>", encoding="utf-8")
    expected = page.absolute().as_uri()
    web.load_url(str(page))

    def url_ready() -> bool:
        try:
            return web.url == expected
        except Exception:
            return False

    assert wait_until(tk_root, url_ready, steps=300), (
        f"expected document URL {expected!r}, got {web.url!r}"
    )

    web.destroy()
    frame.destroy()


def test_load_url_before_create_normalizes_pending(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("example.com")

    assert web.url == "https://example.com"
    web.destroy()
    frame.destroy()


def test_load_html_supersedes_pending_url_before_create(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("example.com")
    web.load_html("<p>html wins</p>")

    assert web.url == "<html>"
    assert web._pending_html == "<p>html wins</p>"
    assert web._pending_url is None

    web.destroy()
    frame.destroy()


def test_initial_load_runs_after_bounds_sync(tk_root) -> None:
    """Deferred initial content load completes after bounds sync (no network)."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>deferred</title><p>sync</p>")

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    pump(tk_root, steps=80)
    assert web._initial_load is None, (
        f"initial_load still pending: {web._initial_load!r}"
    )

    web.destroy()
    frame.destroy()


def test_load_url_coalesces_before_create(tk_root) -> None:
    """Rapid load_url calls before native create keep only the last URL."""
    frame = host_frame(tk_root)
    web = WebView(frame)
    web.load_url("https://example.com/a")
    web.load_url("https://example.com/b")
    web.load_url("https://example.com/c")

    assert web._pending_url == "https://example.com/c"
    assert web._pending_load is None

    web.destroy()
    frame.destroy()


def test_load_coalesces_to_last_pending(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>init</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_url("https://example.com/a")
    web.load_url("https://example.com/b")
    web.load_url("https://example.com/c")

    assert web._initial_load is None
    # Linux may flush pending loads synchronously; Win/macOS keep the coalesced entry.
    assert web._pending_load in (None, ("url", "https://example.com/c"))
    if web._pending_load is not None:
        assert web._pending_load == ("url", "https://example.com/c")

    web.destroy()
    frame.destroy()


def test_load_after_create_cancels_deferred_initial_load(tk_root) -> None:
    """Post-create load_* must win over the delayed constructor reload."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>A</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web._initial_load = ("html", "<p>A</p>")  # re-arm as if delay not yet fired
    web.load_url("https://example.com/B")

    assert web._initial_load is None
    # Linux may flush pending loads synchronously; do not rely on native url().
    assert web._pending_load in (None, ("url", "https://example.com/B"))
    if web._pending_load is not None:
        assert web._pending_load == ("url", "https://example.com/B")
    web._run_initial_load()  # delayed callback must be a no-op
    assert web._initial_load is None
    assert web._pending_load != ("html", "<p>A</p>")

    web.destroy()
    frame.destroy()


def test_page_load_callback_receives_finished(tk_root) -> None:
    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_html("<title>smoke</title><p>load</p>")

    def finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    assert wait_until(tk_root, finished, steps=400), (
        f"expected PageLoadEvent.Finished, got {events!r}"
    )

    web.destroy()
    frame.destroy()


def test_reload_after_ready_fires_page_load(tk_root, tmp_path: Path) -> None:
    page = tmp_path / "reload.html"
    page.write_text(
        "<title>reload-test</title><p id='t'>v1</p>",
        encoding="utf-8",
    )

    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )
    web.load_url(str(page))

    assert wait_until(tk_root, lambda: web.native is not None)

    def initial_finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    assert wait_until(tk_root, initial_finished, steps=400), (
        f"expected initial PageLoadEvent.Finished, got {events!r}"
    )
    finished_before = sum(1 for evt, _ in events if evt == PageLoadEvent.Finished)
    started_before = sum(1 for evt, _ in events if evt == PageLoadEvent.Started)

    web.reload()
    pump(tk_root, steps=50)

    def reload_finished() -> bool:
        finished = sum(1 for evt, _ in events if evt == PageLoadEvent.Finished)
        started = sum(1 for evt, _ in events if evt == PageLoadEvent.Started)
        return finished >= finished_before + 1 and started >= started_before + 1

    assert wait_until(tk_root, reload_finished, steps=400), (
        f"expected Started+Finished after reload(), got {events!r}"
    )

    text: list[str] = []
    web.eval_js_with_callback("document.getElementById('t').textContent", text.append)
    assert wait_until(tk_root, lambda: text, steps=200), (
        f"expected reloaded document content, got {text!r}"
    )
    assert json.loads(text[0]) == "v1"

    web.destroy()
    frame.destroy()


def test_page_load_discards_backlog_before_handler_attach(tk_root) -> None:
    events: list[tuple[PageLoadEvent, str]] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>first</p>")

    assert wait_until(tk_root, lambda: web.native is not None)
    web.load_html("<p>before handler</p>")
    pump(tk_root, steps=80)

    web.set_on_page_load(lambda evt, url: events.append((evt, url)))
    events.clear()
    web.load_html("<p>after handler</p>")

    def finished() -> bool:
        return any(evt == PageLoadEvent.Finished for evt, _ in events)

    assert wait_until(tk_root, finished, steps=400), (
        f"expected Finished after handler attach, got {events!r}"
    )
    assert not any("before handler" in url for _, url in events)

    web.destroy()
    frame.destroy()


def test_ipc_handler_exception_does_not_stop_poll(tk_root) -> None:
    received: list[str] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>ipc</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(msg: str) -> None:
        if msg == "bad":
            raise ValueError("boom")
        received.append(msg)

    web.set_ipc_handler(handler)
    web._enqueue_ipc("bad")
    web._enqueue_ipc("ok")
    pump(tk_root, steps=50)

    assert wait_until(tk_root, lambda: received == ["ok"], steps=100)

    web.destroy()
    frame.destroy()


def test_ipc_post_message_reaches_handler(tk_root) -> None:
    """End-to-end: JS window.ipc.postMessage -> Tk-thread handler."""
    received: list[str] = []
    loaded: list[PageLoadEvent] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="<p>ipc-e2e</p>",
        ipc_handler=lambda msg: received.append(msg),
        on_page_load=lambda evt, _url: loaded.append(evt),
    )
    assert web.wait_until_ready(timeout=10.0)
    assert wait_until(
        tk_root,
        lambda: PageLoadEvent.Finished in loaded,
        steps=400,
    ), f"expected page load Finished before IPC, got {loaded!r}"

    # WebView2 may need a few retries before window.ipc is callable.
    for _ in range(10):
        web.eval_js("window.ipc && window.ipc.postMessage('hello-from-js')")
        if wait_until(tk_root, lambda: "hello-from-js" in received, steps=40):
            break
    assert "hello-from-js" in received, f"expected JS IPC message, got {received!r}"

    web.destroy()
    frame.destroy()


def test_rpc_expose_call_roundtrip(tk_root) -> None:
    """``@web.expose`` + ``window.tkwry.call`` settles a Promise with the result."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")

    @web.expose
    def add(a: int, b: int) -> int:
        return int(a) + int(b)

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)

    web.eval_js(
        """
        (function () {
          if (!window.tkwry || !window.tkwry.call) {
            document.title = "no-tkwry";
            return;
          }
          window.tkwry.call("add", 2, 3).then(function (n) {
            document.title = "sum=" + n;
          }).catch(function (e) {
            document.title = "err=" + e;
          });
        })();
        """
    )

    titles: list[str] = []

    def read_title() -> None:
        web.eval_js_with_callback("document.title", titles.append)

    def title_ready() -> bool:
        read_title()
        return any("sum=5" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), (
        f"expected document.title sum=5, got {titles!r}"
    )

    web.destroy()
    frame.destroy()


def test_rpc_kwargs_call_roundtrip(tk_root) -> None:
    """``call(..., { kwargs: { ... } })`` is passed as Python keyword args."""
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")

    @web.expose
    def greet(message: str, times: int = 1) -> str:
        return str(message) * int(times)

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)

    web.eval_js(
        """
        (function () {
          if (!window.tkwry || !window.tkwry.call) {
            document.title = "no-tkwry";
            return;
          }
          window.tkwry.call("greet", "hi", { kwargs: { times: 3 } }).then(
            function (text) {
              document.title = "out=" + text;
            }
          ).catch(function (e) {
            document.title = "err=" + e;
          });
        })();
        """
    )

    titles: list[str] = []

    def read_title() -> None:
        web.eval_js_with_callback("document.title", titles.append)

    def title_ready() -> bool:
        read_title()
        return any("out=hihihi" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), (
        f"expected document.title out=hihihi, got {titles!r}"
    )

    web.destroy()
    frame.destroy()


def test_rpc_unknown_method_rejects(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")

    @web.expose
    def ping() -> str:
        return "pong"

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)

    web.eval_js(
        """
        (function () {
          window.tkwry.call("missing").then(function () {
            document.title = "unexpected-ok";
          }).catch(function (e) {
            document.title = "err=" + e;
          });
        })();
        """
    )

    titles: list[str] = []

    def read_title() -> None:
        web.eval_js_with_callback("document.title", titles.append)

    def title_ready() -> bool:
        read_title()
        return any("unknown method" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), (
        f"expected reject title, got {titles!r}"
    )

    web.destroy()
    frame.destroy()


def test_rpc_worker_thread_does_not_block_handler_thread_flag(tk_root) -> None:
    """``thread=True`` runs the handler off the Tk thread."""
    import threading

    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")
    caller_ids: list[int] = []

    @web.expose(thread=True)
    def whoami() -> int:
        caller_ids.append(threading.get_ident())
        return threading.get_ident()

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    tk_ident = threading.get_ident()

    web.eval_js(
        """
        (function () {
          window.tkwry.call("whoami").then(function (id) {
            document.title = "id=" + id;
          }).catch(function (e) {
            document.title = "err=" + e;
          });
        })();
        """
    )

    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("id=" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"
    assert caller_ids
    assert caller_ids[0] != tk_ident

    web.destroy()
    frame.destroy()


def test_emit_delivers_to_js_listener(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="""
        <title>emit</title>
        <script>
          window.__emit_ready = false;
          function boot() {
            if (!window.tkwry || !window.tkwry.on) return;
            window.tkwry.on("ping", function (payload) {
              document.title = "ping=" + (payload && payload.n);
            });
            window.__emit_ready = true;
          }
          boot();
          setInterval(boot, 50);
        </script>
        """,
    )

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)

    # Ensure bridge exists even if constructor had no expose yet.
    web.emit("warmup", None)
    pump(tk_root, steps=20)

    web.eval_js(
        """
        if (window.tkwry && window.tkwry.on) {
          window.tkwry.on("ping", function (payload) {
            document.title = "ping=" + (payload && payload.n);
          });
          window.__emit_ready = true;
        }
        """
    )
    pump(tk_root, steps=20)
    web.emit("ping", {"n": 7})

    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("ping=7" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"

    web.destroy()
    frame.destroy()


def test_title_changed_delivers_on_document_title_set(tk_root) -> None:
    titles: list[str] = []

    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="<title>initial</title><p>title</p>",
        on_title_changed=lambda title: titles.append(title),
    )

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    pump(tk_root, steps=50)
    titles.clear()

    web.eval_js("document.title = 'tkwry-title-test'")
    assert wait_until(
        tk_root,
        lambda: "tkwry-title-test" in titles,
        steps=200,
    ), f"expected title callback, got {titles!r}"

    titles.clear()
    web.set_on_title_changed(lambda title: titles.append(title))
    web.eval_js("document.title = 'tkwry-title-after-set'")
    assert wait_until(
        tk_root,
        lambda: "tkwry-title-after-set" in titles,
        steps=200,
    ), f"expected title after set_on_title_changed, got {titles!r}"

    web.destroy()
    frame.destroy()


def test_drag_drop_native_queues_without_blocking(tk_root) -> None:
    """Queue Enter/Drop on the Tk thread (same queue OS drops use).

    Full Finder/Explorer drops cannot be synthesized reliably in CI; this
    covers enqueue -> poll -> Python handler on that path.
    """
    received: list[tuple] = []

    frame = host_frame(tk_root)
    web = WebView(frame, html="<p>dnd</p>")

    assert wait_until(tk_root, lambda: web.native is not None)

    def handler(evt, paths, pos) -> None:
        received.append((evt, paths, pos))

    web.set_drag_drop_handler(handler)

    web._native_drag_drop(DragDropEvent.Enter, ["/tmp/a.txt"], (1, 2))
    web._native_drag_drop(DragDropEvent.Over, [], (3, 4))
    web._native_drag_drop(DragDropEvent.Drop, ["/tmp/a.txt"], (5, 6))

    pump(tk_root, steps=30)
    assert wait_until(tk_root, lambda: len(received) >= 2, steps=100), (
        f"expected queued drag events, got {received!r}"
    )

    web.destroy()
    frame.destroy()


def test_load_local_html_resolves_relative_resources(tk_root, tmp_path: Path) -> None:
    (tmp_path / "style.css").write_text(
        "p { color: rgb(255, 0, 0); }", encoding="utf-8"
    )
    (tmp_path / "index.html").write_text(
        (
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="style.css">'
            "</head><body><p id='t'>local</p></body></html>"
        ),
        encoding="utf-8",
    )

    events: list[tuple[PageLoadEvent, str]] = []
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )
    web.load_url(str(tmp_path / "index.html"))

    assert wait_until(tk_root, lambda: web.ready, steps=200)
    assert wait_until(
        tk_root,
        lambda: any(evt == PageLoadEvent.Finished for evt, _ in events),
        steps=400,
    ), f"expected page load, got {events!r}"
    pump(tk_root, steps=50)

    colors: list[str] = []

    def on_color(value: str) -> None:
        colors.append(value)

    script = "getComputedStyle(document.getElementById('t')).color"
    web.eval_js_with_callback(script, on_color)
    assert wait_until(tk_root, lambda: colors, steps=100), "expected computed color"
    assert "255" in colors[0] or "rgb(255" in colors[0].replace(" ", "")

    web.destroy()
    frame.destroy()


def test_app_custom_protocol_resolves_relative_resources(
    tk_root, tmp_path: Path
) -> None:
    """``app=`` serves local files via ``tkwry://`` (relative CSS works)."""
    (tmp_path / "style.css").write_text(
        "p { color: rgb(0, 128, 0); }", encoding="utf-8"
    )
    (tmp_path / "index.html").write_text(
        (
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="style.css">'
            "</head><body><p id='t'>app</p></body></html>"
        ),
        encoding="utf-8",
    )

    events: list[tuple[PageLoadEvent, str]] = []
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        app=tmp_path,
        on_page_load=lambda evt, url: events.append((evt, url)),
    )

    assert wait_until(tk_root, lambda: web.ready, steps=200)

    def app_finished() -> bool:
        return any(
            evt == PageLoadEvent.Finished
            and ("tkwry" in url or url.startswith("https://tkwry"))
            for evt, url in events
        )

    assert wait_until(tk_root, app_finished, steps=400), (
        f"expected tkwry:// (or https://tkwry.localhost) Finished, got {events!r}"
    )
    pump(tk_root, steps=50)

    colors: list[str] = []

    def on_color(value: str) -> None:
        colors.append(value)

    script = "getComputedStyle(document.getElementById('t')).color"
    web.eval_js_with_callback(script, on_color)
    assert wait_until(tk_root, lambda: colors, steps=100), "expected computed color"
    assert "128" in colors[0] or "rgb(0" in colors[0].replace(" ", "")

    web.destroy()
    frame.destroy()


def test_app_spa_fallback_serves_index_for_client_routes(
    tk_root, tmp_path: Path
) -> None:
    """``spa_fallback=True`` maps extension-less paths to ``index.html``.

    Missing ``/missing.js`` and non-HTML ``Accept`` requests stay 404.
    """
    (tmp_path / "index.html").write_text(
        (
            "<!doctype html><html><head><title>spa</title></head>"
            "<body><p id='t'>spa-ok</p>"
            "<script>document.title='spa-ok';</script>"
            "</body></html>"
        ),
        encoding="utf-8",
    )

    frame = host_frame(tk_root)
    web = WebView(frame, app=tmp_path, spa_fallback=True, app_dev=True)
    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=40)

    web.load_url("tkwry://localhost/app/settings")
    pump(tk_root, steps=60)

    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("spa-ok" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"

    # Missing static assets must stay 404 (never SPA-replaced with index.html).
    statuses: list[str] = []
    web.eval_js(
        """
        Promise.all([
          fetch('/missing.js').then(function (r) { return r.status; }),
          fetch('/app/settings', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.status; })
        ]).then(function (values) {
          document.title = 'st=' + values.join(',');
        }).catch(function (e) {
          document.title = 'fetch-err=' + e;
        });
        """
    )

    def statuses_ready() -> bool:
        web.eval_js_with_callback("document.title", statuses.append)
        return any("st=404,404" in str(t) for t in statuses)

    assert wait_until(tk_root, statuses_ready, steps=400), f"got {statuses!r}"

    web.destroy()
    frame.destroy()


def test_shared_session_local_storage_roundtrip(tk_root, tmp_path: Path) -> None:
    """Two WebViews with the same WebSession share localStorage."""
    from tkwry import WebSession

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text(
        "<!doctype html><html><body><p id='t'>s</p></body></html>",
        encoding="utf-8",
    )
    session = WebSession(data_directory=tmp_path / "profile")
    frame_a = host_frame(tk_root)
    frame_b = host_frame(tk_root)
    web_a = WebView(frame_a, app=app_dir, session=session, width=320, height=240)
    web_b = WebView(frame_b, app=app_dir, session=session, width=320, height=240)

    assert wait_until(tk_root, lambda: web_a.ready and web_b.ready, steps=300)
    pump(tk_root, steps=60)

    web_a.eval_js("localStorage.setItem('tkwry_session_key', 'shared-ok');")
    pump(tk_root, steps=40)

    values: list[str] = []

    def on_value(value: str) -> None:
        values.append(value)

    web_b.eval_js_with_callback("localStorage.getItem('tkwry_session_key')", on_value)
    assert wait_until(tk_root, lambda: values, steps=200), (
        f"expected localStorage value, got {values!r}"
    )
    assert "shared-ok" in values[0]

    web_a.destroy()
    web_b.destroy()
    frame_a.destroy()
    frame_b.destroy()


def test_rpc_concurrent_calls_and_ipc_mix(tk_root) -> None:
    received: list[str] = []
    frame = host_frame(tk_root)
    web = WebView(
        frame,
        html="<title>rpc</title><p>rpc</p>",
        ipc_handler=received.append,
    )

    @web.expose
    def add(a: int, b: int) -> int:
        return int(a) + int(b)

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    web.eval_js(
        """
        (function () {
          if (!window.tkwry || !window.tkwry.call || !window.ipc) {
            document.title = "no-bridge";
            return;
          }
          window.ipc.postMessage("flood-1");
          window.ipc.postMessage("flood-2");
          Promise.all([
            window.tkwry.call("add", 1, 2),
            window.tkwry.call("add", 10, 20),
            window.tkwry.call("add", 3, 4)
          ]).then(function (values) {
            document.title = "sums=" + values.join(",");
          }).catch(function (e) {
            document.title = "err=" + e;
          });
        })();
        """
    )
    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("sums=3,30,7" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"
    assert wait_until(
        tk_root,
        lambda: "flood-1" in received and "flood-2" in received,
        steps=100,
    ), f"expected IPC floods, got {received!r}"
    web.destroy()
    frame.destroy()


def test_rpc_worker_timeout_rejects(tk_root) -> None:
    import time

    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")

    @web.expose(thread=True, timeout=0.2)
    def slow() -> str:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if rpc_cancelled():
                return "cancelled"
            time.sleep(0.02)
        return "done"

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    web.eval_js(
        """
        window.tkwry.call("slow").then(function () {
          document.title = "unexpected-ok";
        }).catch(function (e) {
          document.title = "err=" + (e && e.name ? e.name : e);
        });
        """
    )
    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("RpcTimeoutError" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"
    web.destroy()
    frame.destroy()


def test_rpc_destroy_during_worker_call(tk_root) -> None:
    import threading
    import time

    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")
    started = threading.Event()

    @web.expose(thread=True)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if rpc_cancelled():
                return "cancelled"
            time.sleep(0.02)
        return "done"

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    web.eval_js("window.tkwry.call('slow');")
    assert wait_until(tk_root, started.is_set, steps=200)
    web.destroy()
    frame.destroy()


def test_emit_listener_off_stops_delivery(tk_root) -> None:
    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>emit</title>")
    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    # Bootstrap is injected on first emit; register after that, like
    # test_emit_delivers_to_js_listener.
    web.emit("warmup", None)
    pump(tk_root, steps=20)
    web.eval_js(
        """
        window.__n = 0;
        window.__handler = function () {
          window.__n += 1;
          document.title = "n=" + window.__n;
        };
        window.tkwry.on("tick", window.__handler);
        """
    )
    pump(tk_root, steps=20)
    web.emit("tick", None)
    titles: list[str] = []

    def saw_one() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("n=1" in str(t) for t in titles)

    assert wait_until(tk_root, saw_one, steps=300), f"got {titles!r}"
    web.eval_js("window.tkwry.off('tick', window.__handler); document.title = 'off';")
    pump(tk_root, steps=20)
    titles.clear()
    web.emit("tick", None)
    pump(tk_root, steps=40)
    web.eval_js_with_callback("document.title", titles.append)
    assert wait_until(
        tk_root,
        lambda: any("off" in str(t) for t in titles),
        steps=80,
    )
    assert not any("n=2" in str(t) for t in titles)
    web.destroy()
    frame.destroy()


def test_watch_app_reloads_when_file_changes(tk_root, tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(
        "<!doctype html><html><head><title>v1</title></head>"
        "<body><script>document.title='v1';</script></body></html>",
        encoding="utf-8",
    )
    frame = host_frame(tk_root)
    web = WebView(frame, app=tmp_path, app_dev=True)
    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=40)
    web.watch_app(interval_ms=120, suffixes=[".html"])
    titles: list[str] = []

    def saw_v1() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("v1" in str(t) for t in titles)

    assert wait_until(tk_root, saw_v1, steps=300), f"got {titles!r}"
    index.write_text(
        "<!doctype html><html><head><title>v2</title></head>"
        "<body><script>document.title='v2';</script></body></html>",
        encoding="utf-8",
    )
    import os
    import time

    now = time.time() + 2.0
    os.utime(index, (now, now))
    titles.clear()

    def saw_v2() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("v2" in str(t) for t in titles)

    assert wait_until(tk_root, saw_v2, steps=500), (
        f"expected reload to v2, got {titles!r}"
    )
    web.destroy()
    frame.destroy()


def test_rpc_js_cancel_rejects_worker(tk_root) -> None:
    import threading
    import time

    frame = host_frame(tk_root)
    web = WebView(frame, html="<title>rpc</title><p>rpc</p>")
    started = threading.Event()
    saw_cancel = threading.Event()

    @web.expose(thread=True)
    def slow() -> str:
        started.set()
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            from tkwry import rpc_cancelled

            if rpc_cancelled():
                saw_cancel.set()
                return "cancelled"
            time.sleep(0.03)
        return "done"

    assert web.wait_until_ready(timeout=10.0)
    pump(tk_root, steps=30)
    web.eval_js(
        """
        (function () {
          var p = window.tkwry.call("slow");
          window.__rpc_id = p.id;
          p.then(function () { document.title = "unexpected-ok"; })
           .catch(function (e) {
             document.title = "err=" + (e && e.name ? e.name : e);
           });
          setTimeout(function () { window.tkwry.cancel(window.__rpc_id); }, 80);
        })();
        """
    )
    titles: list[str] = []

    def title_ready() -> bool:
        web.eval_js_with_callback("document.title", titles.append)
        return any("RpcCancelledError" in str(t) for t in titles)

    assert wait_until(tk_root, title_ready, steps=400), f"got {titles!r}"
    assert wait_until(tk_root, saw_cancel.is_set, steps=200)
    web.destroy()
    frame.destroy()
