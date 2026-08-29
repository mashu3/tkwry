# Usage

How-to for `WebView` after the [README landing](../README.md#-usage).
Contracts live elsewhere: [Trust boundaries](trust.md),
[IPC / RPC / emit](rpc.md), [Platform notes](platforms.md).

The constructor **does not raise** if the native view cannot be created
(WebView2 missing, retries exhausted, …). Handle
`<<WebViewCreateFailed>>` / `when_failed` / `on_creation_failed=`, or check
`creation_failed` before treating the widget as live. Gated APIs still raise
`WebViewCreationError`.

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

See [`examples/plotly_demo.py`](../examples/plotly_demo.py).

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
left.delete_cookie(Cookie("sid", "", domain="example.com", path="/"))
left.clear_all_browsing_data()  # this WebView's store
```

Convenience: `WebView(..., data_directory=...)` or `ephemeral=True`
creates an owned session. Keep the `WebSession` alive while any WebView
uses it (especially with `app=` on macOS). Isolation rules (same `app=`
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
web.set_zoom(1.25)  # page zoom (1.0 = 100%); reset_zoom() → 1.0
print(web.url)
web.focus()
```

DevTools need `devtools=True` at construction, then `open_devtools()`
(calling `open_devtools()` alone is a no-op on macOS if the flag was false).

```python
web = WebView(frame, html="<h1>Hello</h1>", devtools=True)
web.open_devtools()
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
The WebView only follows its Frame (`sync_bounds`).

Unmapped hosts (inactive `Notebook` tabs) call `set_visible(False)`.
`ready` stays layout-based (`True` while hidden); use
`phase is WebViewPhase.HIDDEN` when you need visibility.

Switching which WebView is on screen: **unmap** the host (`Notebook`,
`pack_forget`, `grid_remove`). Constructor `width`/`height` is eager
warmup (native exists while hidden). `lift` / `tkraise` of still-mapped
Frames does **not** hide the other native view — they overlap (Windows
HWND z-order is synced; macOS shares one `NSView`). There is no
`WebViewStack` / lazy-create helper yet.

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
still describe the real engine. There are no UA presets, and
`load_url(..., headers=)` (when added) would apply to **that request
only** — it does not rewrite `navigator.userAgent`.

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
from tkwry import NewWindowResponse, PageLoadEvent, unique_download_path

web = WebView(
    frame,
    url="https://example.com",
    on_page_load=lambda evt, url: print(evt, url),
    on_title_changed=lambda title: root.title(title),
    on_navigation=lambda url: url.startswith("https://"),
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

# Downloads: untrusted=True denies unless on_download / download_allow permits.
# on_download must return an absolute path (or True / False). Same-name files:
# unique_download_path(dest) — tkwry does not overwrite or prompt.
web = WebView(
    frame,
    url="https://example.com",
    untrusted=True,
    download_allow=["https://cdn.example.com"],
    on_download=lambda url, dest: unique_download_path(dest),
    on_download_complete=lambda url, dest, ok: print(ok, dest),
)
# also: last_download, <<WebViewDownloadComplete>> / <<WebViewDownloadFailed>>
```

`on_page_load` fires `PageLoadEvent.Started` and `PageLoadEvent.Finished`
**for every navigation** while a handler is registered (native listening
follows the handler). Events are **not** replayed for navigations that
happened before `set_on_page_load` / constructor `on_page_load`.

**Callback threads:** lifecycle / IPC / page-load / title / DnD handlers run
on the **Tk main thread**. RPC handlers default to the same thread; use
`@web.expose(thread=True)` for background work. `on_navigation` and
`on_new_window` are also invoked on Tk, but WebKit **blocks** until they
return a value — keep them fast (heavy work → return deny/default and defer
with `root.after`). Do **not** create another WebView from `on_new_window`
(even deferred): WKWebView deadlocks. Prefer `open_external=True` or
`open_in_browser(url)`; intercept links in JS for in-app tabs (see
[`examples/browser_demo.py`](../examples/browser_demo.py)). Timed-out sync
hooks are canceled after about **60s** total wait. Navigation / new-window
timeouts still return the default (deny) and signal `WebViewNavigationError`
via `<<WebViewNavigationFailed>>` / `last_navigation_error` — they are not
raised on the WebKit thread.

Async queues (IPC, RPC, page-load, title, drag-drop, eval) cap at **2048**
pending items each; further events are compacted or dropped. Each IPC/RPC
**message** also caps at **10 MiB**. RPC is a separate queue from IPC. Use
`take_queue_drop_counts()` to observe overflows — it returns
`(ipc, page_load, title, drag_drop, eval, rpc)`.

Callback exceptions are printed to stderr and do not stop event delivery.

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

```python
web.destroy()   # release native webview; host Frame is kept
# further commands raise WebViewDestroyedError (`destroy()` is idempotent;
# snapshot properties and take_queue_drop_counts() stay readable)
# or destroy the host Frame — both tear down the webview
# in-flight RPC / streams are cancelled cooperatively (pool join ~2s)
```

## API summary

| Category | Members |
|----------|---------|
| Content | `load_url` (`headers=` this request only, http(s)), `load_html`, `reload`, `go_back` / `go_forward` / `can_go_back` / `can_go_forward`, `print`, `url` |
| Cookies / browsing data | `cookies`, `cookies_for_url`, `set_cookie`, `delete_cookie`, `clear_all_browsing_data`, `Cookie` |
| JavaScript | `eval_js` (`on_error`), `eval_js_with_callback`, `last_eval_error`, `<<WebViewEvalFailed>>` |
| IPC / RPC / emit | `set_ipc_handler`, `expose` / `unexpose` (`allow_any_origin=`), `emit`, `WebSession.emit_all`, `watch_app`, `set_bridge_origins`, `set_bridge_allow` (JS: `window.tkwry.call` / `stream` / `cancel`) |
| Callbacks | `set_on_navigation`, `set_on_page_load`, `set_on_title_changed`, `set_on_new_window`, `set_drag_drop_handler`, `set_on_download`, `set_on_download_complete` |
| Appearance | `set_background_color`, `set_zoom` / `reset_zoom`, `focus`, `focus_parent`, `open_devtools`, `close_devtools`, `is_devtools_open` |
| Create-only | `set_user_agent`, `set_initialization_script` (raise after native create) |
| Layout | `pack`, `grid`, `place`, `sync_bounds` (delegate to host `Frame` except `sync_bounds`) |
| Lifecycle | `ready`, `phase` / `WebViewPhase`, `when_ready`, `when_failed`, `wait_until_ready`, `bind` (`<<WebViewReady>>` / `<<WebViewCreateFailed>>` / `<<WebViewEvalFailed>>` / `<<WebViewNavigationFailed>>` / `<<WebViewDownloadComplete>>` / `<<WebViewDownloadFailed>>`), `destroy`, `destroyed`, `native`, `creation_failed`, `creation_error`, `last_eval_error`, `last_navigation_error`, `last_download`, `untrusted`, `navigation_allow`, `open_external`, `download_allow`, `csp` / `coop` / `corp`, `bridge_origins`, `bridge_allow` |
| Diagnostics | `take_queue_drop_counts` |

Constructor options: `width` / `height`, `url`, `html`, `app`, `spa_fallback`,
`app_dev`, `csp` / `coop` / `corp`, `session` / `data_directory` / `ephemeral`,
`untrusted`, `bridge_origins`, `bridge_allow`, `navigation_allow`,
`open_external`, `download_allow`, `ipc_handler`, `rpc_traceback`, `devtools`,
`background_color`, `user_agent`, `initialization_script`, `focused`,
`on_download`, `on_download_complete`, `on_creation_failed`, plus the
callback hooks above.

Enums: `PageLoadEvent`, `NewWindowResponse`, `DragDropEvent`, `WebViewPhase`.
Types: `Cookie` (``repr`` omits ``value`` — never log secrets).
Exceptions: `WebViewNotReadyError`, `WebViewCreationError`, `WebViewDestroyedError`,
`WebViewTimeoutError`, `WebViewNavigationError`,
`RpcTimeoutError`, `RpcCancelledError`, `RpcSerializationError`.
Warning: `TkwrySecurityWarning`. Helpers: `rpc_cancelled`, `rpc_cancel_event`,
`open_in_browser`, `unique_download_path`, `DEFAULT_CSP`.

Type aliases: `IpcHandler`, `BridgeOrigins`, `BridgeAllow`, `NavigationHandler`,
`PageLoadHandler`, `TitleChangedHandler`, `NewWindowHandler`, `DragDropHandler`,
`EvalCallback`, `EvalErrorHandler`, `CreationFailedHandler`, `DownloadHandler`,
`DownloadCompleteHandler`.

## Related

- [Trust boundaries](trust.md)
- [IPC / RPC / emit](rpc.md)
- [Platform notes](platforms.md)
- [README — Known limitations](../README.md#-known-limitations)
