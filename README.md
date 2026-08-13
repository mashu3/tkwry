# tkwry

[![License: MIT](https://img.shields.io/pypi/l/tkwry)](https://opensource.org/licenses/MIT)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/tkwry)](https://pypi.org/project/tkwry)
[![GitHub Release](https://img.shields.io/github/v/release/mashu3/tkwry?color=orange)](https://github.com/mashu3/tkwry/releases)
[![PyPI Version](https://img.shields.io/pypi/v/tkwry?color=yellow)](https://pypi.org/project/tkwry/)
[![Downloads](https://static.pepy.tech/badge/tkwry)](https://pepy.tech/project/tkwry)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-red)](https://github.com/mashu3/tkwry)
[![CI](https://github.com/mashu3/tkwry/actions/workflows/ci.yml/badge.svg)](https://github.com/mashu3/tkwry/actions/workflows/ci.yml)

**Keep Tkinter — give it the WebView it never had.**

Embed a real system WebView ([wry](https://github.com/tauri-apps/wry)) inside your `Frame`: modern HTML, JS, and IPC in the same layout as your buttons and tabs — one `mainloop`, no floating overlay.

> **Alpha** — Early preview (see PyPI badge for the current version). APIs and behavior may change without notice. Not recommended for production use yet.

---

## 📖 Overview

Tkinter is still a solid GUI shell — it just had no first-class way to host modern web content **inside** a widget. Overlay-style WebViews drift out of sync when you move, resize, or switch tabs.

**tkwry** fills that missing piece:

- **True child embedding** — `build_as_child` via HWND, NSView, or X11 window ID
- **One event loop** — Tk `mainloop` only; no separate app runtime
- **Local apps** — `app=` serves HTML/CSS/JS via `tkwry://` (no localhost HTTP server)
- **IPC / RPC / emit** — JS↔Python events and request/response without freezing the UI
- **Trust boundaries** — IPC/RPC default to the initial origin; `untrusted=True` for arbitrary sites
- **Layout-aware** — tracks `pack` / `grid` / `place`, tabs, and `PanedWindow`

Pre-built **abi3** wheels ship for **Windows** and **macOS**. **Linux** is source-only (**best-effort** by design) — see [Platform notes](docs/platforms.md).

---

## 🗂 Documentation

| Topic | Doc |
|-------|-----|
| Trust boundaries (`untrusted`, `bridge_origins`, `app=` nav) | [docs/trust.md](docs/trust.md) |
| IPC / RPC / emit (`expose`, cancel, limits) | [docs/rpc.md](docs/rpc.md) |
| Platform notes (Windows / macOS / Linux) | [docs/platforms.md](docs/platforms.md) |

---

## 🔧 Requirements

- Python 3.10+
- Tkinter (included with most Python builds)
- **Building from source** (git clone, `pip install git+…`, or Linux) — [Rust](https://rustup.rs) toolchain (stable); `pip` uses **maturin** as the build backend
- **Windows (x86_64, arm64)** — [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (no fallback engine; see [Platform notes](docs/platforms.md#windows))
- **macOS** — 11 (Big Sur)+, arm64 or x86_64; system WKWebView
- **Linux** — WebKitGTK 4.1 + GTK 3; X11 or XWayland (`$DISPLAY`); source build only (see [Installation](#-installation) and [Platform notes](docs/platforms.md#linux))

---

## 📦 Installation

### PyPI (recommended — Windows / macOS wheels)

```bash
pip install tkwry
```

### From a git clone (source build)

Cloning the repo and installing locally compiles the Rust extension on your machine. You need a **Rust toolchain** ([rustup](https://rustup.rs)) and platform runtimes from [Requirements](#-requirements) above (WebView2 on Windows, etc.). `pip` pulls in **maturin** automatically as the build backend.

```bash
git clone https://github.com/mashu3/tkwry.git
cd tkwry
pip install -e .
```

Use this for development and for running the [examples](#-examples) from the tree.

### Install a git revision with pip (source build)

```bash
pip install git+https://github.com/mashu3/tkwry.git
```

This builds from source (sdist via git), **not** a pre-built wheel — needs **Rust**, same as `pip install .`. Prefer the PyPI wheel on Windows and macOS unless you need unreleased commits.

### Linux (source install)

Install system dependencies, then build from source (support posture: [Platform notes](docs/platforms.md#linux)):

```bash
# Debian / Ubuntu
sudo apt install \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libglib2.0-dev

# Runtime (for end users of your app)
# sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0

pip install maturin
git clone https://github.com/mashu3/tkwry.git
cd tkwry
pip install .
```

GTK events are pumped automatically on a Tk timer while your app runs.

---

## 🚀 Usage

### Basic WebView

```python
import tkinter as tk
from tkwry import WebView

root = tk.Tk()
root.geometry("900x600")

frame = tk.Frame(root, bg="#222")
frame.pack(fill="both", expand=True, padx=8, pady=8)

web = WebView(frame, url="https://github.com")

root.mainloop()
```

### IPC and RPC (JavaScript ↔ Python)

Use **IPC** for fire-and-forget events and **RPC** for request/response:

| Direction | Role | Python | JavaScript |
|-----------|------|--------|------------|
| JS → Python | IPC (event) | `set_ipc_handler` / `ipc_handler=` | `window.ipc.postMessage(str)` |
| JS → Python | RPC (call) | `@web.expose` | `await window.tkwry.call(name, ...)` |
| Python → JS | Emit (event) | `web.emit(event, data)` | `window.tkwry.on(event, handler)` |

These APIs run with **desktop-app privileges**. By default only the initial
page origin may use them — see [Trust boundaries](docs/trust.md).

```python
def on_message(msg: str) -> None:
    print("from JS:", msg)

web = WebView(
    frame,
    html='<button onclick="window.ipc.postMessage(\'hi\')">send</button>',
    ipc_handler=on_message,
)
```

```python
@web.expose
def greet(name: str) -> str:
    return f"hello {name}"
```

```js
const text = await window.tkwry.call("greet", "Ada");
```

Worker RPC, timeouts, JS `cancel`, argument limits, and `emit` are in
[IPC / RPC / emit](docs/rpc.md). See also [`examples/ipc_demo.py`](examples/ipc_demo.py).

### Local app assets (``app=`` / ``tkwry://``)

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

**SPA fallback** (``spa_fallback=True``): missing **extension-less** paths
(and ``.html`` / ``.htm``) fall back to ``index.html``. A missing static
asset such as ``/app.js`` / ``/style.css`` / ``/video.mp4`` stays **404** —
it is never replaced with ``index.html``. If the request has an ``Accept``
header that does not include ``text/html`` or ``*/*`` (for example
``application/json``), fallback is skipped.

**Cache:** ``app_dev=True`` sends ``Cache-Control: no-store``. Production
(default) still emits ``ETag``; conditional ``If-None-Match`` returns 304.
``HEAD`` and single ``Range: bytes=`` requests are supported (audio/video).

Constructor ``app=`` fixes the filesystem root at create time. Later
``load_url("tkwry://localhost/other.html")`` can navigate within that root
(Windows WebView2 rewrites this to ``https://tkwry.localhost/...`` internally).
Path confinement (percent-decode, symlink/junction checks) is in
[Trust boundaries](docs/trust.md#tkwry-serving).
Monaco / CDN scripts may still be loaded from the network inside that HTML when
you choose not to vendor them yet. The Plotly demo toggles **CDN** vs **Local**
(``app=``); Local caches ``plotly.js`` under ``examples/.vendor/``.

See [`examples/plotly_demo.py`](examples/plotly_demo.py).

### Trust boundaries (external pages)

``window.ipc`` / ``window.tkwry.call`` run with **desktop-app privileges**.
Pick a constructor that matches the page:

```python
# Local UI with RPC — bridge defaults to tkwry://
web = WebView(frame, app="./web")

# Arbitrary websites — no IPC/RPC, ephemeral storage
web = WebView(frame, url="https://example.com", untrusted=True)

# One trusted origin, or a path prefix (not /application)
web = WebView(
    frame,
    url="https://trusted.example/app",
    bridge_origins=["https://trusted.example/app"],
)
```

Defaults, ``bridge_origins="*"``, ``app=`` navigation, dangerous schemes, and
``tkwry://`` Origin checks: [Trust boundaries](docs/trust.md).

### Python → JS events (``emit``)

```python
web.emit("data_updated", {"n": 1})
```

```js
window.tkwry.on("data_updated", (payload) => { ... });
```

Listener errors log via ``console.error`` (``window.tkwry.debug = false`` silences).
Details: [IPC / RPC / emit](docs/rpc.md#python-to-js-events-emit).

### Shared session (``WebSession``)

Share cookies / cache / ``localStorage`` across WebViews via wry's
``WebContext``:

```python
from tkwry import WebSession, WebView

session = WebSession(data_directory="~/.myapp/webview")
left = WebView(frame_a, html=HTML, session=session)
right = WebView(frame_b, html=HTML, session=session)
```

Convenience: ``WebView(..., data_directory=...)`` or ``ephemeral=True``
creates an owned session. Keep the ``WebSession`` alive while any WebView
uses it (especially with ``app=`` on macOS).

**Shared ``app=``:** WebViews that share a **non-ephemeral** ``WebSession``
must use the **same** ``app=`` root. Linux can register ``tkwry://`` only once
per WebContext; tkwry raises ``ValueError`` if a second root is used (all
platforms). Use a separate session for unrelated local apps. Do not share a
persistent profile with untrusted sites — see [Trust boundaries](docs/trust.md)
and [`examples/browser_demo.py`](examples/browser_demo.py).

### Load HTML / evaluate JavaScript

```python
web.load_html("<h1>Hello</h1>")
web.eval_js("document.title = 'Hi'")  # fire-and-forget (Tk idle, no return value)
web.eval_js("bad()", on_error=lambda exc: print("eval failed:", exc))
web.eval_js_with_callback("document.title", print)  # async; callback on Tk main thread
web.load_url("https://example.com")
web.reload()
print(web.url)
web.focus()
```

DevTools need `devtools=True` at construction, then `open_devtools()` (calling `open_devtools()` alone is a no-op on macOS if the flag was false).

```python
web = WebView(frame, html="<h1>Hello</h1>", devtools=True)
web.open_devtools()
```

Rapid `load_url` / `load_html` calls are **coalesced (last-wins)** — `load(A); load(B); load(C)` loads `C` only.

`eval_js` does not return a result (not synchronous). Use `eval_js_with_callback` when you need the JavaScript return value as a `str`. Pass `on_error=` to handle evaluation failures on the Tk main thread; otherwise the traceback is printed to stderr (`EvalErrorHandler`).

### Layout / resize

Bounds sync runs automatically on `<Configure>`, `<Map>`, and `<Unmap>`. Call `sync_bounds()` manually after custom layout changes so the WebView reflows (e.g. centered images):

```python
web.sync_bounds()
```

**Size contract:** once the host is laid out, the mapped `Frame.winfo_width()` / `winfo_height()` are the sole source of truth for native bounds. Constructor `width`/`height` and explicit `place(..., width=, height=)` are only used **before** Tk reports a real size (`winfo_* <= 1`). Prefer passing `width`/`height` to `place()` so the host gets a definite allocation (especially on Linux / Xvfb).

Unmapped hosts (inactive `Notebook` tabs) call `set_visible(False)`. `ready` stays layout-based (`True` while hidden); use `phase is WebViewPhase.HIDDEN` when you need visibility.

### Navigation / lifecycle callbacks

```python
from tkwry import NewWindowResponse, PageLoadEvent

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
```

`on_page_load` fires `PageLoadEvent.Started` and `PageLoadEvent.Finished` **for every navigation** while a handler is registered (native listening follows the handler). Events are **not** replayed for navigations that happened before `set_on_page_load` / constructor `on_page_load`.

**Callback threads:** lifecycle / IPC / page-load / title / DnD handlers run on
the **Tk main thread**. RPC handlers default to the same thread; use
``@web.expose(thread=True)`` for background work. `on_navigation` and
`on_new_window` are also invoked on Tk, but WebKit **blocks** until they return
a value — keep them fast (heavy work → return deny/default and defer with
`root.after`). Do **not** create another WebView from `on_new_window` (even
deferred): WKWebView deadlocks. Prefer ``open_external=True`` or
``open_in_browser(url)``; intercept links in JS for in-app tabs (see
[`examples/browser_demo.py`](examples/browser_demo.py)). Timed-out sync hooks
are canceled after about **60s** total wait.

Async queues (IPC, RPC, page-load, title, drag-drop, eval) cap at **2048** pending items each; further events are compacted or dropped. Each IPC/RPC **message** also caps at **10 MiB**. RPC is a separate queue from IPC. Use `take_queue_drop_counts()` to observe overflows — it returns `(ipc, page_load, title, drag_drop, eval, rpc)`.

Callback exceptions are printed to stderr and do not stop event delivery.

### Drag & drop (native OS path)

File drops from Finder / Explorer are handled by the OS WebView. Your handler runs on the **Tk main thread** (tkwry queues events from WebKit automatically). The handler is **notify-only** (`-> None`); drops are always accepted and cannot be denied from Python.

```python
from tkwry import DragDropEvent

def on_drop(event, paths, position):
    if event == DragDropEvent.Drop:
        print("files:", paths)

web = WebView(frame, html="...", drag_drop_handler=on_drop)
```

See [`examples/dnd_demo.py`](examples/dnd_demo.py).

### Cleanup

```python
web.destroy()   # release native webview; host Frame is kept
# or destroy the host Frame — both tear down the webview
# in-flight RPC is cancelled cooperatively (pool join ~2s)
```

---

## 📚 API summary

| Category | Members |
|----------|---------|
| Content | `load_url`, `load_html`, `reload`, `go_back` / `go_forward` / `can_go_back` / `can_go_forward`, `url` |
| JavaScript | `eval_js` (`on_error`), `eval_js_with_callback` |
| IPC / RPC / emit | `set_ipc_handler`, `expose` / `unexpose` (`allow_any_origin=`), `emit`, `watch_app`, `set_bridge_origins`, `set_bridge_allow` |
| Callbacks | `set_on_navigation`, `set_on_page_load`, `set_on_title_changed`, `set_on_new_window`, `set_drag_drop_handler` |
| Appearance | `set_background_color`, `focus`, `focus_parent`, `open_devtools`, `close_devtools`, `is_devtools_open` |
| Create-only | `set_user_agent`, `set_initialization_script` (raise after native create) |
| Layout | `pack`, `grid`, `place`, `sync_bounds` (delegate to host `Frame` except `sync_bounds`) |
| Lifecycle | `ready`, `phase` / `WebViewPhase`, `when_ready`, `wait_until_ready`, `bind`, `destroy`, `destroyed`, `native`, `creation_failed`, `creation_error`, `untrusted`, `navigation_allow`, `open_external`, `bridge_origins`, `bridge_allow` |
| Diagnostics | `take_queue_drop_counts` |

Constructor options: `width` / `height`, `url`, `html`, `app`, `spa_fallback`,
`app_dev`, `session` / `data_directory` / `ephemeral`, `untrusted`,
`bridge_origins`, `bridge_allow`, `navigation_allow`, `open_external`,
`ipc_handler`, `rpc_traceback`, `devtools`, `background_color`, `user_agent`,
`initialization_script`, `focused`, plus the callback hooks above.

Enums: `PageLoadEvent`, `NewWindowResponse`, `DragDropEvent`, `WebViewPhase`.
Exceptions: `WebViewNotReadyError`, `WebViewCreationError`, `WebViewDestroyedError`,
`RpcTimeoutError`, `RpcCancelledError`, `RpcSerializationError`.
Warning: `TkwrySecurityWarning`. Helpers: `rpc_cancelled`, `rpc_cancel_event`,
`open_in_browser`.

Type aliases: `IpcHandler`, `BridgeOrigins`, `BridgeAllow`, `NavigationHandler`, `PageLoadHandler`, `TitleChangedHandler`, `NewWindowHandler`, `DragDropHandler`, `EvalCallback`, `EvalErrorHandler`.

---

## ⚠️ Known limitations

Short checklist — **details live in [Platform notes](docs/platforms.md)** (especially [macOS embedding](docs/platforms.md#macos-embedding)).

- **Alpha** — APIs may change; not for production yet (see banner above)
- **Windows** — WebView2 Runtime required; missing runtime → `WebViewCreationError`
- **Windows DevTools** — wry/WebView2 reports `is_devtools_open()` as `False` and `close_devtools()` is a no-op; `open_devtools()` still opens the inspector
- **Linux** — no PyPI wheel (by design); best-effort source install
- **Linux concurrent `eval_js_with_callback`** — evaluating on multiple WebViews at once can stall WebKitGTK; prefer sequential evals (see [Linux](docs/platforms.md#linux))
- **Shared `WebSession` + `app=`** — WebViews that share a non-ephemeral session must use the same `app=` root (`ValueError` otherwise; Linux can register `tkwry://` only once per context); do not share a persistent profile with untrusted sites
- **Trust / external content** — RPC/IPC default to the initial origin (optional path prefix / `bridge_allow`); `bridge_origins="*"` warns and needs `expose(..., allow_any_origin=True)`; `app=` locks navigation to `tkwry://` (`navigation_allow` / `open_external=True` for extra origins + system browser); use `untrusted=True` for arbitrary websites (see [Trust boundaries](docs/trust.md))
- **macOS DevTools** — create with `devtools=True`, then `open_devtools()` (flag alone does not open; `open_devtools()` without the flag is a no-op on macOS); uses private APIs — avoid in Mac App Store builds
- **macOS IME / focus** — not Safari-parity; mid-composition focus flips can mis-route input
- **macOS import order** — import `tkwry` before AppKit/`NSApplication`, or you may see a double titlebar
- **`url()` on macOS** — may be `None` for inline HTML until a concrete `load_url` (WKWebView has no document `NSURL`)
- **Sync hooks / queues** — `on_navigation` / `on_new_window` may block WebKit up to ~60s; do not create a WebView from `on_new_window` (use `open_external=True` / `open_in_browser`); async event queues cap at 2048; IPC/RPC messages cap at 10 MiB (see [Navigation / lifecycle callbacks](#navigation--lifecycle-callbacks))
- **RPC cancel / destroy** — timeout, JS `cancel`, and `destroy()` are **cooperative only** (`rpc_cancelled()`); Python cannot preempt a running worker. `destroy()` joins the pool for ~2 seconds; leftover threads are logged to stderr (see [IPC / RPC / emit](docs/rpc.md#timeout-and-cancel))
- **Drag & drop** — WebView area only (use [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) for arbitrary Tk widgets)

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## 🌐 Platform notes

Pre-built wheels: **Windows** and **macOS**. **Linux** is source-only (best-effort).

| OS | Arch | Parent handle | Engine |
|----|------|---------------|--------|
| **Windows** | x86_64, arm64 | `Frame.winfo_id()` → HWND | WebView2 |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK |

DPI, WebView2, macOS embedding / IME / import order, and Linux eval caveats:
[Platform notes](docs/platforms.md).

---

## 💡 Why child-window embedding?

Tkinter apps already have a window and a layout. The web belongs **inside** a `Frame` — same `mainloop`, same tabs and panes — not in a separate top-level webview that floats beside your UI. tkwry wraps wry's `build_as_child` against the native surface Tk gives your widgets.

---

## 🧩 Features

- **Local app assets** — `app=` + `tkwry://` (SPA fallback, `app_dev` no-store, ETag/HEAD/Range, bounded `watch_app()`; open-then-verify symlink/junction confinement)
- **IPC / RPC / emit** — events vs request/response; worker RPC; typed TypeError; protocol `version`; JS `cancel`; Python→JS `emit`; origin/path allowlist (`bridge_origins`) + `bridge_allow` + `untrusted=` viewer mode
- **WebSession** — shared wry `WebContext`; shared `app=` roots must match
- **Testing helpers** — `tkwry.testing.wait_until` / `wait_ready` / `wait_eval` / `wait_title`
- **Child-window embedding** — WebView is a native child of your Tk window surface, not a floating overlay
- **Bounds & visibility sync** — follows `<Configure>`, `<Map>`, and `<Unmap>` (tabs / `Notebook` hide unmapped views)
- **Deferred callbacks** — IPC, RPC, page load, title, eval results, and DnD queue to Tk (avoids macOS deadlocks)
- **URL safety** — Python `load_url` normalizes/validates schemes; in-page nav denies `javascript:`/`blob:`/… (`data:` under `app=`); `app=` stays on `tkwry://`; IPC/RPC origin/path allowlist + `bridge_allow`
- **DevTools** — `devtools=True` at create, then `open_devtools()` / `close_devtools()` / `is_devtools_open()` (macOS: private APIs)
- **Native drag & drop** — OS-level file drops into the WebView (no tkinterdnd2)
- **Navigation hooks** — all handlers on the Tk thread; `on_navigation` / `on_new_window` block WebKit until they return
- **Multiple layouts** — works with `pack`, `grid`, `place`, `Notebook`, and `PanedWindow` (see examples)
- **Plotly-ready** — load HTML + `eval_js`; demo toggles CDN vs local `app=`
- **Folium-ready** — embed Leaflet maps from Folium HTML (right-click to pin)
- **Markdown-ready** — Monaco editor + live preview in a `PanedWindow` (see [`examples/markdown_demo.py`](examples/markdown_demo.py); CDN required — or vendor under `app=`)
- **CI-tested** — `pytest` on Windows (x86_64 + arm64), macOS, and Linux (Xvfb + WebKitGTK)

---

## 📁 Examples

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

| Script | Description |
|--------|-------------|
| [`examples/browser_demo.py`](examples/browser_demo.py) | URL bar, tabs, shared `WebSession`, open-in-new-tab (`bridge_origins="*"`; no `expose`) |
| [`examples/ipc_demo.py`](examples/ipc_demo.py) | IPC events, RPC (`call` / kwargs / worker), and `emit` |
| [`examples/multi_demo.py`](examples/multi_demo.py) | Multiple WebViews, tabs, panes |
| [`examples/plotly_demo.py`](examples/plotly_demo.py) | Plotly charts — CDN or local `app=` (`pip install plotly`) |
| [`examples/folium_demo.py`](examples/folium_demo.py) | Folium maps (`pip install folium`; tiles need the network) |
| [`examples/markdown_demo.py`](examples/markdown_demo.py) | Monaco markdown editor + live preview (CDN) |
| [`examples/dnd_demo.py`](examples/dnd_demo.py) | Native file drag & drop into WebView |

```bash
python examples/browser_demo.py
python examples/ipc_demo.py
python examples/multi_demo.py
python examples/plotly_demo.py
python examples/folium_demo.py
python examples/markdown_demo.py
python examples/dnd_demo.py
```

---

## 📝 License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE).

This project links against [wry](https://github.com/tauri-apps/wry), which is dual-licensed (Apache-2.0 **or** MIT). tkwry uses wry under MIT; see [NOTICE](NOTICE) for attribution.

---

## 👨‍💻 Author

[mashu3](https://github.com/mashu3)

[![Contributors](https://contrib.rocks/image?repo=mashu3/tkwry)](https://github.com/mashu3/tkwry/graphs/contributors)
