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

Pre-built **abi3** wheels ship for **Windows (x86_64)** and **macOS (Apple Silicon + Intel)** — these are the primary release targets.
**Linux** is **best-effort**: build from source (sdist / git); timing and headless behavior are not guaranteed in v0.0.x.

---

## 🔧 Requirements

- Python 3.10+
- Tkinter (included with most Python builds)
- **Windows (x86_64 only)**
  - [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) must be installed (pre-installed on many Windows 10/11 systems).
  - **Without WebView2, tkwry is not supported on Windows** — there is no fallback engine.
- **macOS** — 11 (Big Sur) or later; Apple Silicon (**arm64**) or Intel (**x86_64**); system WKWebView (no extra runtime)
- **Linux** — WebKitGTK 4.1 + GTK 3 dev packages; X11 or XWayland (`$DISPLAY`); build the extension from source (see below)

---

## 📦 Installation

Install from PyPI (Windows / macOS wheels):

```bash
pip install tkwry
```

> **Developing or running examples from a git clone** — use an editable install so
> Python picks up this repo (not an older copy in site-packages):
>
> ```bash
> pip install -e .
> ```

Or install from GitHub for the latest changes:

```bash
pip install git+https://github.com/mashu3/tkwry.git
```

### Linux (source install, best-effort)

Linux builds from source and runs for many apps, but **v0.0.x does not treat Linux stability as a release requirement** — focus is on Windows and macOS wheels. Install system dependencies, then:

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
web.eval_js_with_callback("document.title", print)  # async; callback on Tk main thread
web.load_url("https://example.com")
web.reload()
print(web.url)
web.focus()
web.open_devtools()
```

Rapid `load_url` / `load_html` calls are **coalesced (last-wins)** — `load(A); load(B); load(C)` loads `C` only.

`eval_js` does not return a result (not synchronous). Use `eval_js_with_callback` when you need the JavaScript return value as a `str`.

### Layout / resize

Bounds sync runs automatically on `<Configure>`, `<Map>`, and `<Unmap>`. Call `sync_bounds()` manually after custom layout changes so the WebView reflows (e.g. centered images):

```python
web.sync_bounds()
```

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

`on_page_load` fires `PageLoadEvent.Started` and `PageLoadEvent.Finished` **for every navigation**. Events that occurred before a handler was registered are **discarded** when you call `set_on_page_load` (or pass `on_page_load` in the constructor from the start).

Callback exceptions are printed to stderr and do not stop event delivery.

### Drag & drop (native OS path)

File drops from Finder / Explorer are handled by the OS WebView. Your handler runs on the **Tk main thread** (tkwry queues events from WebKit automatically).

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
| JavaScript | `eval_js`, `eval_js_with_callback` |
| IPC | `set_ipc_handler` |
| Callbacks | `set_on_navigation`, `set_on_page_load`, `set_on_title_changed`, `set_on_new_window`, `set_drag_drop_handler` |
| Appearance | `set_background_color`, `focus`, `focus_parent`, `open_devtools`, `close_devtools`, `is_devtools_open` |
| Layout | `pack`, `grid`, `place`, `sync_bounds` (delegate to host `Frame` except `sync_bounds`) |
| Lifecycle | `destroy`, `destroyed`, `native` |

Constructor options: `url`, `html`, `ipc_handler`, `devtools`, `background_color`,
`user_agent`, `initialization_script`, `focused`, plus the callback hooks above.

Enums: `PageLoadEvent`, `NewWindowResponse`, `DragDropEvent`.

---

## ⚠️ Known limitations

- **Alpha** — APIs may change; not recommended for production yet
- **Windows** — WebView2 Runtime required; systems without it are unsupported
- **Linux** — source install only (no PyPI wheel); **best-effort** in v0.0.x — headless CI and event timing are not release blockers
- **DevTools** — macOS uses private APIs; avoid in Mac App Store release builds
- **macOS input** — Tk text widgets and the WebView share one window; tkwry routes focus automatically (see [macOS embedding](#macos-embedding)). IME and other advanced input may still differ from a standalone browser
- **Drag & drop** — drop target is the WebView area only (not arbitrary Tk widgets; use [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) for those)

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## 🌐 Platform notes

| OS | Arch | Parent handle | Engine | Notes |
|----|------|---------------|--------|-------|
| **Windows** | x86_64 | `Frame.winfo_id()` → HWND | WebView2 | WebView2 Runtime required |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView | See [macOS embedding](#macos-embedding) below |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK | Source install; **best-effort** (not a wheel release target) |

### macOS embedding

On macOS, Tk child `Frame`s usually **do not** get their own `NSView` — that is a property of the Tk Aqua backend, not something tkwry can turn into per-frame native views without upstream Tk changes.

tkwry works around this by:

1. Attaching the WebView to the **toplevel content view**
2. Positioning it with `set_bounds` to match your `Frame` (`<Configure>`)
3. Hiding it with `set_visible(False)` when the frame is unmapped (`<Unmap>`) — e.g. another `ttk.Notebook` tab is selected

**Keyboard focus (macOS):** tkwry routes input between Tk widgets (`Entry`, `Text`, …) and the WebView automatically. Rust hit-tests clicks at the `NSEvent` layer and switches first responder; Python drains focus signals on the Tk main thread so keystrokes reach the correct target. Use `web.focus()` and `web.focus_parent()` when you need explicit control — see [`examples/url_demo.py`](examples/url_demo.py). IME and other advanced input may still behave differently than in a standalone browser.

**You do not need extra code for tabs or panes** — see [`examples/multi_demo.py`](examples/multi_demo.py). IPC, page-load, title, eval, and drag-and-drop handlers are dispatched on the **Tk main thread** via an internal queue (avoids WebKit deadlocks).

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
- **Navigation hooks** — `on_navigation`, `on_page_load`, `on_title_changed`, `on_new_window`
- **Multiple layouts** — works with `pack`, `grid`, `place`, `Notebook`, and `PanedWindow` (see examples)
- **Plotly-ready** — load HTML + `eval_js` for interactive charts
- **Folium-ready** — embed Leaflet maps from Folium HTML (right-click to pin)
- **Alpha, but tested** — CI on Windows and macOS; Linux CI is smoke/build only (best-effort)

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
| [`examples/dnd_demo.py`](examples/dnd_demo.py) | Native file drag & drop into WebView |

```bash
python examples/url_demo.py
python examples/ipc_demo.py
python examples/multi_demo.py
python examples/plotly_demo.py
python examples/folium_demo.py
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
