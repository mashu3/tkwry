# Usage

How-to for `WebView` after the [README landing](../README.md#-usage).
Contracts live elsewhere: [Trust boundaries](trust.md),
[IPC / RPC / emit](rpc.md), [Platform notes](platforms.md),
[Packaging notes](packaging.md).

| Start here | |
|------------|--|
| [Minimal app](#minimal-app) | First runnable window (`app=` or URL) |
| [Local HTTP / ASGI](#local-http--asgi-loopback) | Loopback Flask/FastAPI — not in-process WSGI |
| [Hidden hosts](#hidden-hosts) | Notebook / `pack_forget` vs `lift` overlap |
| [User-Agent](#user-agent) | App identity — not a Chrome spoof |
| [Observability](#observability) | ``WebViewPhase`` + ``take_queue_drop_stats()`` |
| [Cleanup](#cleanup) | `destroy` / Frame / `WebSession.close` order |
| [API stability](#api-stability) | Public vs Provisional (Alpha) |
| [API summary](#api-summary) | Public surface table |

The constructor **does not raise** if the native view cannot be created
(WebView2 missing, retries exhausted, …). Handle
`<<WebViewCreateFailed>>` / `when_failed` / `on_creation_failed=`, or check
`creation_failed` before treating the widget as live. Gated APIs still raise
`WebViewCreationError`. Windows Runtime install:
[Platform notes — WebView2](platforms.md#webview2-runtime-probe-and-install).

## Minimal app

A complete local UI in one file (no localhost HTTP server). Put HTML under
`web/` and point `app=` at that directory:

```text
myapp/
├── main.py
└── web/
    └── index.html
```

```html
<!-- web/index.html -->
<!DOCTYPE html>
<html>
  <body>
    <h1 id="t">Hello from tkwry</h1>
    <button id="ping">Ping Python</button>
    <script>
      document.getElementById("ping").onclick = async () => {
        const n = await window.tkwry.invoke("ping", {});
        document.getElementById("t").textContent = "pong " + n;
      };
    </script>
  </body>
</html>
```

```python
# main.py
import tkinter as tk
from pathlib import Path

from tkwry import WebView, configure_window

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

root = tk.Tk()
configure_window(root, title="My app", geometry="800x500", minsize=(400, 300))

frame = tk.Frame(root)
frame.pack(fill="both", expand=True)

web = WebView(
    frame,
    app=WEB,
    user_agent="MyApp/0.1",  # app identity — see User-Agent below
)
web.when_failed(lambda exc: print("create failed:", exc))


@web.rpc("ping")
def ping() -> int:
    return 1


root.mainloop()
```

Next steps:

- Shared cookies / tabs: [Shared session](#shared-session-websession) and
  [`examples/browser_demo.py`](../examples/browser_demo.py)
- JS↔Python streams / cancel: [IPC / RPC / emit](rpc.md) and
  [`examples/ipc_demo.py`](../examples/ipc_demo.py)
- Trust for arbitrary URLs: [Trust boundaries](trust.md)

Real-device smoke stays in the examples (`browser_demo` print/download/destroy;
`ipc_demo` stream+cancel). Automated Notebook hide/show:
`tests/integration/test_notebook.py`.

## Local app assets (`app=` / `tkwry://`)

Serve a directory of HTML/CSS/JS through a custom protocol — no localhost HTTP
server. Relative links resolve offline (React/Vue/Svelte/Monaco bundles, etc.).

```text
web/
├── index.html
├── style.css
└── assets/
    └── main.js
```

```python
web = WebView(frame, app="./web")          # loads tkwry://localhost/index.html
# or: WebView(frame, app="./web/index.html")
# SPA client routes: spa_fallback=True
# Dev: app_dev=True (Cache-Control: no-store) + web.watch_app() for reload
# watch_app() polls web suffixes (skips node_modules/.git/.vendor; max 2000 files)
```

**SPA fallback** (`spa_fallback=True`): missing **extension-less** paths
(and `.html` / `.htm`) fall back to `index.html`. A missing static
asset such as `/app.js` / `/style.css` / `/video.mp4` stays **404** —
it is never replaced with `index.html`. If the request has an `Accept`
header that does not include `text/html` or `*/*` (for example
`application/json`), fallback is skipped.

**Cache:** `app_dev=True` sends `Cache-Control: no-store`. Production
(default) still emits `ETag`; conditional `If-None-Match` returns 304.
`HEAD` and single `Range: bytes=` requests are supported (audio/video).

Path confinement, CSP / COOP / CORP, and `tkwry://` Origin checks:
[Trust boundaries — tkwry serving](trust.md#tkwry-serving).
Monaco / CDN scripts may still load from the network inside that HTML when
you choose not to vendor them yet (set `csp=` accordingly, or use `html=`).
The Plotly demo toggles **CDN** vs **Local** (`app=`); Local caches
`plotly.js` under `examples/.vendor/`.

On **Windows**, `app=` is served as `https://tkwry.localhost` by default
(secure context). `https_scheme=False` uses wry's `http://tkwry.localhost`
instead. macOS / Linux stay `tkwry://`. See
[Platform notes — HTTPS scheme](platforms.md#https-scheme-windows-app).

See [`examples/plotly_demo.py`](../examples/plotly_demo.py).
Need a real HTTP/ASGI stack instead of static files?
[Local HTTP / ASGI](#local-http--asgi-loopback).

## Local HTTP / ASGI (loopback)

Prefer [`app=`](#local-app-assets-app--tkwry) for static HTML/CSS/JS.
Use a **loopback HTTP** (or ASGI) server when you already have Flask /
FastAPI / Django, or need SSR / WebSocket / a real HTTP stack.

tkwry does **not** embed WSGI/ASGI (no portless in-process Flask —
that stays out of scope). The framework is **your** dependency
(`pip install fastapi uvicorn`, …), not tkwry's.

Bind **`127.0.0.1` only** (not `0.0.0.0`). Point the WebView at that
origin. `bridge_origins` defaults to the `url=` origin, so RPC works
without `"*"`. Treat loopback like any other http origin:
[Trust boundaries](trust.md#localhost--asgi).

```python
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

httpd = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
web = WebView(frame, url=f"http://127.0.0.1:{port}/")


def on_quit():
    httpd.shutdown()
    web.destroy()
    root.destroy()
```

FastAPI/Uvicorn is the same shape: serve on `127.0.0.1`, start the
server off the Tk thread, `url=http://127.0.0.1:<port>/`. Stop the
server on quit ([Cleanup](#cleanup)).

## Shared session (`WebSession`)

Share cookies / cache / `localStorage` across WebViews via wry's
`WebContext`:

```python
from tkwry import Cookie, WebSession, WebView

session = WebSession(data_directory="~/.myapp/webview")
left = WebView(frame_a, html=HTML, session=session)
right = WebView(frame_b, html=HTML, session=session)
session.emit_all("theme", {"mode": "dark"})  # → both views (if emit-eligible)

# Cookie CRUD is on WebView (wry names; Tk thread; ready). Never log values:
for c in left.cookies_for_url("https://example.com/"):
    print(c.name, c.domain)  # not c.value
left.set_cookie(
    Cookie("sid", "…", domain="example.com", path="/", secure=True, http_only=True)
)
left.delete_cookie("sid", "https://example.com/")
# or: left.delete_cookie(Cookie("sid", "", domain="example.com", path="/"))
left.clear_all_browsing_data()  # this WebView's store

# App shutdown: tear down every live view on the profile, then release it.
session.close()  # idempotent; run on the Tk main thread
```

Convenience: `WebView(..., data_directory=...)` or `ephemeral=True`
creates an owned session. **`user_data_dir=`** is an alias for
``data_directory=`` (Electron-style naming). **`profile="name"`** opens a
named persistent profile under ``~/.tkwry/profiles/<name>`` (override the
root with :func:`~tkwry.set_profiles_base` or ``TKWRY_PROFILES_DIR``); every
WebView with the same name shares one :class:`~tkwry.WebSession` in-process.
Call :func:`~tkwry.close_profile` to tear down a named profile at quit.

```python
from tkwry import WebView, close_profile, set_profiles_base

set_profiles_base("./browser_data")  # optional; before first profile=
a = WebView(frame_a, url="https://example.com", profile="account_a")
b = WebView(frame_b, url="https://other.example", profile="account_b")
# ...
close_profile("account_a")  # optional; destroys views on that profile
```

**Incognito / private browsing** is that same
flag: `WebSession(ephemeral=True)` maps to wry `with_incognito`.
`incognito=True` is a constructor alias (`WebView` and `WebSession`);
read the result with `session.ephemeral`. Not a second profile mode, and
not a per-view override on a persistent session. Cookie sharing across
views in an ephemeral session is best-effort by platform. Call :meth:`WebSession.close` when the profile
is no longer needed (or destroy every WebView first). Keep the session open
while any WebView uses it (especially with `app=` on macOS). Isolation rules (same `app=`
root, do not share a persistent profile with untrusted sites):
[Trust boundaries — Session isolation](trust.md#session-isolation).
See also [`examples/browser_demo.py`](../examples/browser_demo.py).

## Load HTML / evaluate JavaScript

```python
web.load_html("<h1>Hello</h1>")
web.eval_js("document.title = 'Hi'")  # fire-and-forget (Tk idle, no return value)
web.eval_js("bad()", on_error=lambda exc: print("eval failed:", exc))
web.eval_js_with_callback("document.title", print)  # async; callback on Tk main thread
web.load_url("https://example.com")
web.load_url(
    "https://api.example.com/me",
    headers={"Authorization": "Bearer …"},  # this request only; never logged
)
web.reload()
web.print()  # system print dialog (no PDF, no success/fail result)
# macOS only — margins; still no result. Win/Linux → OSError:
# web.print_with_options(top=36, left=36)
web.set_zoom(1.25)  # page zoom (1.0 = 100%); reset_zoom() → 1.0
print(web.url)
web.focus()
```

DevTools need `devtools=True` at construction, then `open_devtools()`
(calling `open_devtools()` alone is a no-op on macOS if the flag was false).

```python
web = WebView(frame, html="<h1>Hello</h1>", devtools=True)
web.open_devtools()

# Web Clipboard API (Monaco / editors): opt-in on Windows / Linux.
# macOS WebView clipboard is always available — see platforms.md.
web = WebView(frame, html="<textarea></textarea>", clipboard=True)

# Break-glass: disable page JS (wry with_javascript_disabled). Default True.
# untrusted=True does not flip this.
web = WebView(frame, url="https://example.com", javascript_enabled=False)

# Media without a user gesture (wry with_autoplay). Default True.
web = WebView(frame, html="<video autoplay src='clip.mp4'></video>", autoplay=True)

# Windows: Ctrl+/- / Ctrl+wheel / pinch page zoom (wry with_hotkeys_zoom).
# Default False. Not set_zoom. macOS/Linux ignore the engine flag.
web = WebView(frame, html="<p>zoom</p>", hotkeys_zoom=True)

# Trackpad / swipe history (wry with_back_forward_navigation_gestures).
# Default False. Win / macOS / Linux. Not go_back / go_forward.
web = WebView(frame, html="<p>swipe</p>", back_forward_gestures=True)

# Windows: hide WebView2 page context menu (Inspect / Back / …).
# Default True. untrusted=True does not flip this. macOS/Linux ignore.
web = WebView(frame, html="<p>kiosk</p>", default_context_menus=False)

# Windows app=: https://tkwry.localhost (secure context). Default True.
# False → http://tkwry.localhost (mixed content; not SW / crypto.subtle).
web = WebView(frame, app="./web", https_scheme=True)

# HTTP CONNECT or SOCKSv5 (wry with_proxy_config). Create-only.
# Exactly one key. No credentials (rejected, never logged). macOS 14+.
web = WebView(frame, html="<p>via proxy</p>", proxy={"http": "127.0.0.1:8080"})
```

Rapid `load_url` / `load_html` calls are **coalesced (last-wins)** —
`load(A); load(B); load(C)` loads `C` only.

`eval_js` does not return a result (not synchronous). Use
`eval_js_with_callback` when you need the JavaScript return value as a
`str`. Failures and the 30s callback timeout call `on_error=` (if set),
generate `<<WebViewEvalFailed>>`, and set `last_eval_error`
(`WebViewTimeoutError` on timeout). Without `on_error` the error is also
printed to stderr.

Print honesty: [Platform notes — Print](platforms.md#print).
Page zoom: [Platform notes — Zoom](platforms.md#zoom-page-not-window).
Swipe history: [Platform notes — Back / forward gestures](platforms.md#back--forward-gestures-swipe).
Context menus: [Platform notes — Default context menus](platforms.md#default-context-menus).
Windows `app=` origin: [Platform notes — HTTPS scheme](platforms.md#https-scheme-windows-app).
Proxy: [Platform notes — Proxy](platforms.md#proxy-http-connect--socksv5).
DevTools OS caveats: [Platform notes](platforms.md).

## Layout / resize

Bounds sync runs automatically on `<Configure>`, `<Map>`, and `<Unmap>`.
Call `sync_bounds()` manually after custom layout changes so the WebView
reflows (e.g. centered images):

```python
web.sync_bounds()
```

**Size contract:** once the host is laid out, the mapped `Frame.winfo_width()`
/ `winfo_height()` are the sole source of truth for native bounds.
Constructor `width`/`height` and explicit `place(..., width=, height=)` are
only used **before** Tk reports a real size (`winfo_* <= 1`). Prefer passing
`width`/`height` to `place()` so the host gets a definite allocation
(especially on Linux / Xvfb).

**Window chrome is the host Toplevel**, not the WebView:
[Platform notes — Window chrome](platforms.md#window-chrome-tk-not-the-webview).
Use `configure_window(root, title=..., geometry=..., minsize=..., …)` for
the common chrome kwargs; the WebView only follows its Frame (`sync_bounds`).
ttk / dark host chrome / custom titlebar → **tkface** (sibling; import OK),
not a second chrome kit inside tkwry.

## Hidden hosts

Native visibility follows **map state**, not Tk stacking order.

| Approach | What happens | Use when |
|----------|--------------|----------|
| **Unmap** (`Notebook` inactive tab, `pack_forget`, `grid_remove`) | Host `<Unmap>` → `set_visible(False)`; `phase` may be `WebViewPhase.HIDDEN` | Switching which view is on screen |
| **Constructor `width` / `height`** | Eager warmup — native can exist while still hidden / 1×1 | Create before first show without waiting for layout |
| **`lift` / `tkraise` on still-mapped Frames** | Both natives stay shown and **overlap** (Win HWND z-order synced; macOS one `NSView`) | Never as a “hide the other tab” trick |

There is **no** `WebViewStack` / lazy-create helper in 0.1.x. Prefer unmap.

```python
import tkinter as tk
from tkinter import ttk

from tkwry import WebView, WebViewPhase

root = tk.Tk()
nb = ttk.Notebook(root)
nb.pack(fill="both", expand=True)

page_a = tk.Frame(nb)
page_b = tk.Frame(nb)
nb.add(page_a, text="A")
nb.add(page_b, text="B")

# Inactive Notebook pages are unmapped → native hides automatically.
web_a = WebView(page_a, html="<p>A</p>", width=400, height=300)
web_b = WebView(page_b, html="<p>B</p>", width=400, height=300)

# ready is layout-based (may stay True while HIDDEN):
# web_a.phase is WebViewPhase.HIDDEN  # when tab B is selected

root.mainloop()
```

Coverage: [`tests/integration/test_notebook.py`](../tests/integration/test_notebook.py).
Multi-pane (still mapped): [`examples/multi_demo.py`](../examples/multi_demo.py).

## User-Agent

Create-only (`user_agent=` or `set_user_agent` before the native view
exists). After create it raises. Use it to **name your app**, not to
pretend to be Chrome:

```python
web = WebView(frame, url="https://example.com", user_agent="MyApp/1.2")
```

The engine may **prefix or suffix** the default WebView UA — live tests
check that your string appears **in** `navigator.userAgent`, not that it
replaces the whole value. Client Hints and other `navigator.*` fields
still describe the real engine. There are no UA presets.
`load_url(..., headers=)` applies to **that request only** — it does not
rewrite `navigator.userAgent`.

When a third-party site **degrades** in the WebView (YouTube comments
missing, “unsupported browser”, Google login stuck), do not treat that
as a missing spoof API. Check the URL first: `/embed/` and
`youtube-nocookie.com` never show comments. `untrusted=True` uses an
ephemeral profile, so there is no lasting Google session. If you need
the full site (comments, login, payments), send that URL to the system
browser:

```python
from tkwry import open_in_browser

web = WebView(
    frame,
    app="./web",
    navigation_allow=[],  # keep YouTube out of the WebView
    open_external=True,   # off-list http(s) → default browser
)
# or from a button / custom hook:
open_in_browser("https://www.youtube.com/watch?v=…")
```

A desktop-looking `user_agent=` at create is an app-level experiment
only. WebView2 / WKWebView / WebKitGTK still fingerprint as themselves;
tkwry will not ship a per-site compat layer.

## Navigation / lifecycle callbacks

```python
from tkwry import (
    NewWindowResponse,
    PageLoadEvent,
    PermissionKind,
    PermissionResponse,
    unique_download_path,
)

web = WebView(
    frame,
    url="https://example.com",
    on_page_load=lambda evt, url: print(evt, url),
    on_title_changed=lambda title: root.title(title),
    on_navigation=lambda event: event.url.startswith("https://"),
    permission_handler=lambda kind: (
        PermissionResponse.Allow
        if kind in (PermissionKind.Camera, PermissionKind.Microphone)
        else PermissionResponse.Default
    ),
    on_new_window=lambda url: NewWindowResponse.Deny,
)

# Local app: stay on tkwry:// (+ extra origins); off-list http(s) → system browser.
# Never create a WebView from on_new_window (WKWebView deadlocks).
web = WebView(
    frame,
    app="./web",
    navigation_allow=["https://docs.example.com"],
    open_external=True,
)
web.go_back()
web.go_forward()

# Navigation policy: allow only matching URLs (no custom on_navigation needed).
web.set_navigation_policy(
    lambda event: event.url.startswith("https://example.com")
)

# Downloads: untrusted=True denies unless on_download / download_allow permits.
# on_download accepts Download (one arg) or legacy (url, suggested_dest).
# Return download.save("./downloads"), an absolute path, True, or False.
# Same-name files: unique_download_path(dest) — tkwry does not overwrite.
# Cancel = start-deny only. No mid-flight abort / progress % — Platform notes.
web = WebView(
    frame,
    url="https://example.com",
    untrusted=True,
    download_allow=["https://cdn.example.com"],
    on_download=lambda download: download.save("./downloads"),
    on_download_started=lambda item: print("started", item.url),
    on_download_complete=lambda url, dest, ok: print("done", ok, dest),
    on_download_failed=lambda url, dest: print("failed", url, dest),
)
# also: last_download, last_started_download, in_flight_downloads,
# <<WebViewDownloadStarted>> / Complete / Failed
```

`on_page_load` fires `PageLoadEvent.Started` and `PageLoadEvent.Finished`
**for every navigation** while a handler is registered (native listening
follows the handler). Events are **not** replayed for navigations that
happened before `set_on_page_load` / constructor `on_page_load`.

**Callback threads:** lifecycle / IPC / page-load / title / DnD handlers run
on the **Tk main thread**. RPC handlers default to the same thread; use
`@web.expose(thread=True)` for background work. `on_navigation`,
`on_new_window`, and create-time `permission_handler` are also invoked on Tk,
but WebKit **blocks** until they return a value — keep them fast (heavy work
→ return deny/default and defer with `root.after`). Do **not** create another
WebView from `on_new_window` (even deferred): WKWebView deadlocks. Prefer
`open_external=True` or `open_in_browser(url)`; intercept links in JS for
in-app tabs (see [`examples/browser_demo.py`](../examples/browser_demo.py)).
Timed-out sync hooks are canceled after about **60s** total wait. Navigation /
new-window timeouts still return the default (deny) and signal
`WebViewNavigationError` via `<<WebViewNavigationFailed>>` /
`last_navigation_error` — they are not raised on the WebKit thread.
`permission_handler` timeouts / bad returns → `PermissionResponse.Deny`.
Omit `permission_handler` for the engine default; 0.1.5 does **not** change
`untrusted=True` to default-Deny for permissions. Coverage varies by engine
(see wry / platform notes).

Async queues (IPC, RPC, page-load, title, drag-drop, eval) cap at **2048**
pending items each; further events are compacted or dropped. Worker→Tk
RPC **stream** chunks and download-complete events also cap at 2048.
Each IPC/RPC **message** also caps at **10 MiB**. RPC is a separate queue
from IPC. Prefer `take_queue_drop_stats()` → `QueueDropCounts` (named
fields including `download_complete` and `rpc_stream`). The legacy
`take_queue_drop_counts()` 6-tuple
`(ipc, page_load, title, drag_drop, eval, rpc)` remains for 0.1.x.

Callback exceptions are printed to stderr and do not stop event delivery.
Optional provisional ``on_callback_error=(exc, kind) -> None`` (or
:meth:`WebView.set_on_callback_error`) routes those failures to app code;
``kind`` names the hook (e.g. ``"on_page_load"``, ``"ipc_handler"``). Not
in ``tkwry.__all__`` — may change in 0.2.x.

Trust / download policy: [Trust boundaries](trust.md).

## Drag & drop (native OS path)

File drops from Finder / Explorer are handled by the OS WebView. Your
handler runs on the **Tk main thread** (tkwry queues events from WebKit
automatically). The handler is **notify-only** (`-> None`); drops are always
accepted and cannot be denied from Python.

```python
from tkwry import DragDropEvent

def on_drop(event, paths, position):
    if event == DragDropEvent.Drop:
        print("files:", paths)

web = WebView(frame, html="...", drag_drop_handler=on_drop)
```

See [`examples/dnd_demo.py`](../examples/dnd_demo.py).

## Cleanup

Tear down on the **Tk main thread**. Host `<Destroy>` already calls
`web.destroy()`, so destroying the Frame or Toplevel is enough — explicit
`destroy()` is for releasing the native view **while keeping** the Frame.

| Order | Call | Why |
|-------|------|-----|
| 1 | `web.destroy()` (optional if the Frame is going away) | Unbinds host events, cancels in-flight RPC (~2s join), disposes native WebView, drops wakeup-pipe registration. Idempotent. |
| 2 | Destroy the host `Frame` if you no longer need it | `<Destroy>` → `web.destroy()` if you skipped step 1. |
| 3 | `session.close()` for a **shared** `WebSession` | Destroys any remaining views on the profile, then drops the native context. Idempotent. Run **after** views you still needed, or instead of per-view destroy at quit. |
| 4 | `root.destroy()` / Toplevel last | Do not kill Tcl while native teardown is still in flight. |

```python
def on_quit():
    session.close()  # destroys remaining WebViews, then the profile
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_quit)
```

Do **not**:

- Reuse a `WebView` after `destroy()` or after `creation_failed` — construct a new one.
- Call `destroy` / `session.close` from a worker thread.
- `session.close()` while another live view still needs that profile.
- Close the Toplevel first and expect a later `web.destroy()` to be reliable.

Owned sessions (`data_directory=` / `ephemeral=` with no `session=`) are
not auto-`close()`d on `web.destroy()`; drop your last reference or call
`close()` if you kept the session object.

Further commands raise `WebViewDestroyedError` (`destroy()` is
idempotent; snapshot properties and `take_queue_drop_stats()` stay
readable). In-flight RPC / streams are cancelled cooperatively.

See [`examples/browser_demo.py`](../examples/browser_demo.py) quit path.
Regression: existing destroy / session tests (`tests/unit/test_destroy_api.py`,
`tests/unit/test_session.py`).

## Observability

Use **`phase`** / :class:`~tkwry.WebViewPhase` for a cheap lifecycle snapshot,
**`bounds`** for the native view geometry last applied by the engine (same
space as ``set_bounds`` / ``sync_bounds``), and **`take_queue_drop_stats()`**
to detect handler backlogs. All are safe to read from the Tk main thread; stats
remain readable after :meth:`~tkwry.WebView.destroy`.

### Lifecycle phase

``WebView.phase`` is derived — it does not drive transitions. Typical flow:

| Phase | Meaning | Action |
|-------|---------|--------|
| `PRE_CREATE` | Host frame exists; native not yet live | Wait or handle create failure |
| `NATIVE` | Native view exists; layout may still be 1×1 | Optional ``sync_bounds()`` |
| `READY` | Sized and eligible for gated APIs | ``eval_js`` / ``expose`` / ``emit`` |
| `HIDDEN` | Host unmapped (e.g. inactive Notebook tab) | ``ready`` may stay true; prefer re-select tab before eval |
| `CREATE_FAILED` | Native create abandoned | ``creation_error`` / ``<<WebViewCreateFailed>>`` |
| `TEARING_DOWN` / `DESTROYED` | ``destroy()`` in progress or done | Do not call gated APIs |

```python
from tkwry import WebView, WebViewPhase

def on_ready(_event=None):
    if web.phase is not WebViewPhase.READY:
        return
    web.eval_js("console.log('live')")

web.bind("<<WebViewReady>>", on_ready)
```

Hidden-host rules: [Hidden hosts](#hidden-hosts).

### Queue drop stats

Internal async queues cap at **2048** pending items. Overflow events are
**dropped and counted** — delivery is best-effort under backlog.

```python
from tkwry import QueueDropCounts

stats: QueueDropCounts = web.take_queue_drop_stats()
if any(
    (
        stats.ipc,
        stats.page_load,
        stats.title,
        stats.drag_drop,
        stats.eval,
        stats.rpc,
        stats.download_complete,
        stats.rpc_stream,
    )
):
    print("tkwry queue drops:", stats)
```

Call periodically from a Tk timer or after heavy bursts (IPC storms, stream
chunks, download-complete without handler). Each call **resets** counters
(both ``take_queue_drop_stats`` and the legacy six-field
``take_queue_drop_counts`` share the first six buckets).

**Interpretation:**

- **`eval` / `rpc` spikes** — Python handlers or ``eval_js_with_callback`` too
  slow; shorten work or move to ``@expose(thread=True)``.
- **`download_complete`** — complete events arrived faster than Tk drained them
  (rare unless the main loop is blocked).
- **`rpc_stream`** — generator ``@expose`` yields faster than JS consumes;
  cancel on the JS side or throttle yields.

Sync-hook timeouts (navigation / new window / permission) surface via
``last_navigation_error`` / ``<<WebViewNavigationFailed>>`` — not queue drops.

Provisional callback exceptions: ``on_callback_error`` (see
[API stability](#api-stability)).

## API stability

**Alpha (0.1.x):** behavior may change; not recommended for production.

| Class | Rule |
|-------|------|
| **Public** | Listed in ``tkwry.__all__`` and the [API summary](#api-summary) below |
| **Provisional** | Documented but **not** in ``__all__`` — may change in 0.2.x |
| **Internal** | Underscore modules / methods — unsupported |

**Provisional today:**

| Symbol | Notes |
|--------|-------|
| ``on_callback_error`` / ``set_on_callback_error`` | Route callback exceptions to app code (`exc`, `kind`); default remains stderr |

Constructor vs setter **dual paths** (e.g. ``on_navigation=`` vs
``set_on_navigation``) are intended to be equivalent before native create and
after; prefer one style per app. Beta will publish a full classification and
Stability policy (0.2.0).

## API summary

| Category | Members |
|----------|---------|
| Content | `load_url` (`headers=` this request only, http(s)), `load_html`, `reload`, `go_back` / `go_forward` / `can_go_back` / `can_go_forward`, `print`, `print_with_options` (macOS margins), `url` |
| Cookies / browsing data | `cookies`, `cookies_for_url`, `set_cookie`, `delete_cookie` (`Cookie` or `name` + page `url`), `clear_all_browsing_data`, `Cookie` |
| JavaScript | `eval_js` (`on_error`), `eval_js_with_callback`, `last_eval_error`, `<<WebViewEvalFailed>>` |
| IPC / RPC / emit | `set_ipc_handler`, `expose` / `rpc` / `unexpose` (`allow_any_origin=`), `emit`, `WebSession.emit_all`, `watch_app`, `set_bridge_origins`, `set_bridge_allow` (JS: `window.tkwry.call` / `invoke` / `stream` / `cancel`) |
| Callbacks | `set_on_navigation`, `set_navigation_policy`, `set_on_page_load`, `set_on_title_changed`, `set_on_new_window`, `set_drag_drop_handler`, `set_on_download`, `set_on_download_started`, `set_on_download_complete`, `set_on_download_failed`; create-only `permission_handler=` |
| Appearance | `set_background_color`, `set_zoom` / `reset_zoom`, `focus`, `focus_parent`, `open_devtools`, `close_devtools`, `is_devtools_open` |
| Create-only | `set_user_agent`, `set_initialization_script` (raise after native create); `devtools=`, `clipboard=`, `javascript_enabled=`, `autoplay=`, `hotkeys_zoom=`, `back_forward_gestures=`, `default_context_menus=`, `https_scheme=`, `proxy=`, `permission_handler=` |
| Layout | `pack`, `grid`, `place`, `sync_bounds`, `bounds` (native geometry in ``set_bounds`` space) |
| Lifecycle | `ready`, `phase` / `WebViewPhase`, `when_ready`, `when_failed`, `wait_until_ready`, `bind` (`<<WebViewReady>>` / `<<WebViewCreateFailed>>` / `<<WebViewEvalFailed>>` / `<<WebViewNavigationFailed>>` / `<<WebViewDownloadStarted>>` / `<<WebViewDownloadComplete>>` / `<<WebViewDownloadFailed>>`), `destroy`, `destroyed`, `native`, `creation_failed`, `creation_error`, `last_eval_error`, `last_navigation_error`, `last_download`, `last_started_download`, `in_flight_downloads`, `profile`, `untrusted`, `clipboard`, `javascript_enabled`, `autoplay`, `hotkeys_zoom`, `back_forward_gestures`, `default_context_menus`, `https_scheme`, `proxy`, `navigation_allow`, `open_external`, `download_allow`, `csp` / `coop` / `corp`, `bridge_origins`, `bridge_allow` |
| Diagnostics | `take_queue_drop_stats` / `QueueDropCounts`, `take_queue_drop_counts` |

Constructor options: `width` / `height`, `url`, `html`, `app`, `spa_fallback`,
`app_dev`, `csp` / `coop` / `corp`, `session` / `profile` / `user_data_dir` /
`data_directory` / `ephemeral` / `incognito` (alias of `ephemeral`),
`untrusted`, `bridge_origins`, `bridge_allow`, `navigation_allow`,
`open_external`, `download_allow`, `ipc_handler`, `rpc_traceback`, `devtools`,
`clipboard`, `javascript_enabled`, `autoplay`, `hotkeys_zoom`,
`back_forward_gestures`, `default_context_menus`, `https_scheme`, `proxy`,
`background_color`, `user_agent`,
`initialization_script`, `focused`,
`permission_handler`, `on_navigation`, `navigation_policy`, `on_download`,
`on_download_started`, `on_download_complete`, `on_download_failed`,
`on_creation_failed`,
plus the callback hooks above.

Enums: `PageLoadEvent`, `NavigationType`, `NewWindowResponse`, `PermissionKind`,
`PermissionResponse`, `DragDropEvent`, `WebViewPhase`.
Types: `WebView`, `WebSession`, `Cookie` (``repr`` omits ``value`` — never log secrets),
`Download`, `InFlightDownload`, `NavigationEvent`, `QueueDropCounts`.
Exceptions: `WebViewNotReadyError`, `WebViewCreationError`, `WebViewDestroyedError`,
`WebViewTimeoutError`, `WebViewNavigationError`,
`RpcTimeoutError`, `RpcCancelledError`, `RpcSerializationError`.
Warning: `TkwrySecurityWarning`. Helpers: `configure_window`, `close_profile`,
`set_profiles_base`, `rpc_cancelled`,
`rpc_cancel_event`, `open_in_browser`, `unique_download_path`, `DEFAULT_CSP`.

Type aliases: `IpcHandler`, `BridgeOrigins`, `BridgeAllow`, `NavigationHandler`,
`PageLoadHandler`, `TitleChangedHandler`, `NewWindowHandler`, `DragDropHandler`,
`EvalCallback`, `EvalErrorHandler`, `CreationFailedHandler`, `DownloadHandler`,
`DownloadCompleteHandler`, `DownloadFailedHandler`, `DownloadHandler`,
`DownloadStartedHandler`, `NavigationHandler`, `NavigationPolicyHandler`,
`PermissionHandler`.

## Related

- [Trust boundaries](trust.md)
- [IPC / RPC / emit](rpc.md)
- [Platform notes](platforms.md)
- [Packaging notes](packaging.md)
- [README — Known limitations](../README.md#-known-limitations)
