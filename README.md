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
- **IPC bridge** — JavaScript → Python callbacks without freezing the UI
- **Layout-aware** — tracks `pack` / `grid` / `place`, tabs, and `PanedWindow`

Pre-built **abi3** wheels ship for **Windows** and **macOS**. **Linux** is source-only (**best-effort** by design) — see [Platform notes](#-platform-notes).

---

## 🔧 Requirements

- Python 3.10+
- Tkinter (included with most Python builds)
- **Building from source** (git clone, `pip install git+…`, or Linux) — [Rust](https://rustup.rs) toolchain (stable); `pip` uses **maturin** as the build backend
- **Windows (x86_64, arm64)** — [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (no fallback engine; see [Platform notes](#windows))
- **macOS** — 11 (Big Sur)+, arm64 or x86_64; system WKWebView
- **Linux** — WebKitGTK 4.1 + GTK 3; X11 or XWayland (`$DISPLAY`); source build only (see [Installation](#-installation) and [Platform notes](#linux))

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

Install system dependencies, then build from source (support posture: [Platform notes](#linux)):

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

### IPC (JavaScript → Python)

```python
def on_message(msg: str) -> None:
    print("from JS:", msg)

web = WebView(
    frame,
    html='<button onclick="window.ipc.postMessage(\'hi\')">send</button>',
    ipc_handler=on_message,
)
```

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
```

`on_page_load` fires `PageLoadEvent.Started` and `PageLoadEvent.Finished` **for every navigation** while a handler is registered (native listening follows the handler). Events are **not** replayed for navigations that happened before `set_on_page_load` / constructor `on_page_load`.

**Callback threads:** all user handlers run on the **Tk main thread**. `on_page_load`, `on_title_changed`, IPC, and drag-and-drop are queued asynchronously. `on_navigation` and `on_new_window` are also invoked on Tk, but WebKit **blocks** until they return a value — keep them fast (heavy work → return deny/default and defer with `root.after`).

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
```

---

## 📚 API summary

| Category | Members |
|----------|---------|
| Content | `load_url`, `load_html`, `reload`, `url` |
| JavaScript | `eval_js` (`on_error`), `eval_js_with_callback` |
| IPC | `set_ipc_handler` |
| Callbacks | `set_on_navigation`, `set_on_page_load`, `set_on_title_changed`, `set_on_new_window`, `set_drag_drop_handler` |
| Appearance | `set_background_color`, `focus`, `focus_parent`, `open_devtools`, `close_devtools`, `is_devtools_open` |
| Create-only | `set_user_agent`, `set_initialization_script` (raise after native create) |
| Layout | `pack`, `grid`, `place`, `sync_bounds` (delegate to host `Frame` except `sync_bounds`) |
| Lifecycle | `ready`, `phase` / `WebViewPhase`, `when_ready`, `wait_until_ready`, `bind`, `destroy`, `destroyed`, `native`, `creation_failed`, `creation_error` |
| Diagnostics | `take_queue_drop_counts` |

Constructor options: `url`, `html`, `ipc_handler`, `devtools`, `background_color`,
`user_agent`, `initialization_script`, `focused`, plus the callback hooks above.

Enums: `PageLoadEvent`, `NewWindowResponse`, `DragDropEvent`, `WebViewPhase`.

Type aliases: `IpcHandler`, `NavigationHandler`, `PageLoadHandler`, `TitleChangedHandler`, `NewWindowHandler`, `DragDropHandler`, `EvalCallback`, `EvalErrorHandler`.

---

## ⚠️ Known limitations

Short checklist — **details live in [Platform notes](#-platform-notes)** (especially [macOS embedding](#macos-embedding)).

- **Alpha** — APIs may change; not for production yet (see banner above)
- **Windows** — WebView2 Runtime required; missing runtime → `WebViewCreationError`
- **Linux** — no PyPI wheel (by design); best-effort source install
- **macOS DevTools** — optional (`devtools=True` / `open_devtools()`); uses private APIs — avoid in Mac App Store builds
- **macOS IME / focus** — not Safari-parity; mid-composition focus flips can mis-route input
- **macOS import order** — import `tkwry` before AppKit/`NSApplication`, or you may see a double titlebar
- **`url()` on macOS** — may be `None` for inline HTML until a concrete `load_url`
- **Hidden Notebook tabs** — native view is hidden; `ready` can stay `True` while unmapped
- **Drag & drop** — WebView area only (use [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) for arbitrary Tk widgets)

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## 🌐 Platform notes

| OS | Arch | Parent handle | Engine |
|----|------|---------------|--------|
| **Windows** | x86_64, arm64 | `Frame.winfo_id()` → HWND | WebView2 |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK |

### Windows

[WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) must be installed (common on Windows 10/11). Without it, creation fails with `WebViewCreationError` (install link in the message). There is **no** fallback engine.

### Linux

**By design in v0.0.x:** no PyPI wheel; install from source (sdist / git). Support is **best-effort** — not a release blocker for Windows/macOS wheels. CI runs the integration suite under **Xvfb**; real-desktop / Wayland timing may still differ. GTK is pumped on a Tk timer automatically after install.

For `place` layouts, pass explicit `width`/`height` so host `winfo_*` settles; native size follows those `winfo_*` values (see [Layout / resize](#layout--resize)).

### macOS embedding

Tk child `Frame`s usually **do not** get their own `NSView` (Tk Aqua). tkwry attaches to the **toplevel content view**, positions with `set_bounds` on `<Configure>`, and hides with `set_visible(False)` on `<Unmap>` (e.g. another Notebook tab). Per-frame native views would need upstream Tk changes.

**Keyboard focus:** clicks are hit-tested at the `NSEvent` layer; Python drains focus signals on the Tk main thread. Use `web.focus()` / `web.focus_parent()` for explicit control ([`examples/url_demo.py`](examples/url_demo.py)). `focused=True` waits for `<<WebViewReady>>`, then calls `focus()`; call `focus()` yourself after later layout changes.

**IME:** composition stays with the current first responder. Switching Tk ↔ WebView mid-composition (or fighting the system candidate window) can cancel or mis-deliver input vs Safari. **Not** a v0.1 goal — finish composition before changing focus, or keep IME editing in one surface.

**Import order / double titlebar:** import `tkwry` **before** anything that starts `AppKit` / `NSApplication`. On import, tkwry disables process-level automatic window tabbing on the main thread. If AppKit starts first, macOS may show a **double titlebar** strip — see [`examples/macos_double_titlebar_repro.py`](examples/macos_double_titlebar_repro.py). If per-window tabbing disable during create fails, tkwry logs and retries asynchronously (non-fatal).

**`url()`:** may be `None` for inline HTML (`html=` / `load_html`) or when WKWebView has no document `NSURL`. After `load_url`, it becomes the concrete URI.

**Notebook / tabs:** unmapped tabs hide the native view (`set_visible(False)`) and show again on `<Map>` — required because frames share the toplevel `NSView`. `ready` is layout-based (can stay `True` while hidden); prefer visible-tab work after the tab is selected. No extra app code for tabs/panes — [`examples/multi_demo.py`](examples/multi_demo.py).

All user handlers run on the **Tk main thread**. `on_navigation` / `on_new_window` still make WebKit wait for a return value — see [Navigation / lifecycle callbacks](#navigation--lifecycle-callbacks).

---

## 💡 Why child-window embedding?

Tkinter apps already have a window and a layout. The web belongs **inside** a `Frame` — same `mainloop`, same tabs and panes — not in a separate top-level webview that floats beside your UI. tkwry wraps wry's `build_as_child` against the native surface Tk gives your widgets.

---

## 🧩 Features

- **Child-window embedding** — WebView is a native child of your Tk window surface, not a floating overlay
- **Bounds & visibility sync** — follows `<Configure>`, `<Map>`, and `<Unmap>` (tabs / `Notebook` work out of the box on macOS)
- **Deferred callbacks** — IPC, page load, title, eval results, and DnD queue to Tk (avoids macOS deadlocks)
- **URL safety** — normalizes and validates URLs before navigation
- **DevTools** — `open_devtools()` / `devtools=True` for debugging
- **Native drag & drop** — OS-level file drops into the WebView (no tkinterdnd2)
- **Navigation hooks** — all handlers on the Tk thread; `on_navigation` / `on_new_window` block WebKit until they return
- **Multiple layouts** — works with `pack`, `grid`, `place`, `Notebook`, and `PanedWindow` (see examples)
- **Plotly-ready** — load HTML + `eval_js` for interactive charts
- **Folium-ready** — embed Leaflet maps from Folium HTML (right-click to pin)
- **Markdown-ready** — Monaco editor + live preview in a `PanedWindow` (see [`examples/markdown_demo.py`](examples/markdown_demo.py); CDN required)
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
| [`examples/url_demo.py`](examples/url_demo.py) | URL bar + embedded page |
| [`examples/ipc_demo.py`](examples/ipc_demo.py) | JavaScript ↔ Tkinter IPC |
| [`examples/multi_demo.py`](examples/multi_demo.py) | Multiple WebViews, tabs, panes |
| [`examples/plotly_demo.py`](examples/plotly_demo.py) | Plotly charts (`pip install plotly`) |
| [`examples/folium_demo.py`](examples/folium_demo.py) | Folium maps (`pip install folium`) |
| [`examples/markdown_demo.py`](examples/markdown_demo.py) | Monaco markdown editor + live preview (CDN) |
| [`examples/dnd_demo.py`](examples/dnd_demo.py) | Native file drag & drop into WebView |
| [`examples/macos_double_titlebar_repro.py`](examples/macos_double_titlebar_repro.py) | macOS import-order / double titlebar comparison |

```bash
python examples/url_demo.py
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
