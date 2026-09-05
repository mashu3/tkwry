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
- **Local apps** — `app=` serves HTML/CSS/JS without a localhost HTTP server (`tkwry://` on macOS/Linux; Windows defaults to `https://tkwry.localhost`)
- **IPC / RPC / emit** — JS↔Python events, request/response, and streams without freezing the UI
- **Trust boundaries** — IPC/RPC default to the initial origin; `untrusted=True` for arbitrary sites
- **Layout-aware** — tracks `pack` / `grid` / `place`, tabs, and `PanedWindow`

---

## 💡 Why child-window embedding?

Tkinter apps already have a window and a layout. The web belongs **inside** a `Frame` — same `mainloop`, same tabs and panes — not in a separate top-level webview that floats beside your UI. tkwry wraps wry's `build_as_child` against the native surface Tk gives your widgets.

---

## 🌐 Platform notes

Pre-built **abi3** wheels: **Windows** and **macOS**. **Linux** is source-only (**best-effort** by design).

| OS | Arch | Parent handle | Engine |
|----|------|---------------|--------|
| **Windows** | x86_64, arm64 | `Frame.winfo_id()` → HWND | WebView2 |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK |

DPI, WebView2, macOS embedding / IME / import order, and Linux eval caveats:
[Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md).

---

## 🔧 Requirements

- Python 3.10+
- Tkinter (included with most Python builds)
- **Building from source** (git clone, `pip install git+…`, or Linux) — [Rust](https://rustup.rs) toolchain (stable); `pip` uses **maturin** as the build backend
- **Windows (x86_64, arm64)** — [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (no fallback engine; see [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#windows))
- **macOS** — 11 (Big Sur)+, arm64 or x86_64; system WKWebView
- **Linux** — WebKitGTK 4.1 + GTK 3; X11 or XWayland (`$DISPLAY`); source build only (see [Installation](#-installation) and [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#linux))

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

Install system dependencies, then build from source (support posture: [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#linux)):

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
web.when_failed(lambda exc: print("native create failed:", exc))

root.mainloop()
```

The constructor **does not raise** if native create fails. Handle
`when_failed` / `<<WebViewCreateFailed>>`. Minimal app, `app=`, hidden hosts,
User-Agent, downloads, cleanup, and the API table:
[Usage](https://github.com/mashu3/tkwry/blob/main/docs/usage.md)
([Minimal app](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#minimal-app)).
IPC / RPC / stream: [docs/rpc.md](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md).
Trust (`untrusted`, `bridge_origins`): [docs/trust.md](https://github.com/mashu3/tkwry/blob/main/docs/trust.md).

---

## 🧩 Features

**Embedding & layout**

- Native child of your Tk surface (`build_as_child`) — not a floating overlay
- Bounds / visibility follow `<Configure>`, `<Map>`, `<Unmap>` (Notebook tabs hide unmapped views)
- Works with `pack` / `grid` / `place`, `Notebook`, and `PanedWindow`; window chrome is the host Toplevel ([Layout / resize](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#layout--resize))

**Local apps & bridge**

- `app=` serves assets without a localhost HTTP server (`tkwry://` on macOS/Linux; Windows defaults to `https://tkwry.localhost`)
- IPC / RPC / emit between JS and Python ([docs/rpc.md](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md))
- Origin-scoped bridge by default; `untrusted=` for arbitrary sites ([docs/trust.md](https://github.com/mashu3/tkwry/blob/main/docs/trust.md))

**Browser-ish APIs**

- `WebSession` / profiles, cookies ([Usage — Shared session](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#shared-session-websession)); navigation hooks, downloads, print, DevTools ([platforms](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md))
- Native file drag & drop into the WebView area (notify-only)

**Host integration**

- Typed create / eval / navigation / download failure signals on the Tk thread
- Lifecycle callbacks deferred onto Tk (avoids native-thread deadlocks)
- `tkwry.testing` wait helpers for integration tests

Plotly / Folium / Markdown demos live under [Examples](#-examples). Prefer
[`tkwry_browser.py`](#start-here-mini-browser) as the full-layout sample
([docs/examples-browser.md](https://github.com/mashu3/tkwry/blob/main/docs/examples-browser.md)).

---

## 📁 Examples

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### Start here: mini-browser

The easiest way to see tkwry in a full layout: toolbar + side pane + content
tabs, all as child WebViews.

| macOS · dark | Windows · light |
|:---:|:---:|
| <img src="docs/images/browser-macos-dark.png" alt="tkwry browser on macOS (dark)" width="480" /> | <img src="docs/images/browser-windows-light.png" alt="tkwry browser on Windows (light)" width="480" /> |

| Script | Description |
|--------|-------------|
| [`examples/tkwry_browser.py`](https://github.com/mashu3/tkwry/blob/main/examples/tkwry_browser.py) | Flagship mini-browser (single file): `app=` toolbar / side / Settings, separate content `WebSession`, New Tab start page, profiles, shortcuts |

```bash
python examples/tkwry_browser.py
python examples/tkwry_browser.py --private
```

Architecture, trust split, and what to copy: [docs/examples-browser.md](https://github.com/mashu3/tkwry/blob/main/docs/examples-browser.md).

### Focused demos

| Script | Description |
|--------|-------------|
| [`examples/ipc_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/ipc_demo.py) | IPC events, RPC (`call` / kwargs / worker), stream (`ticks` + cancel), and `emit` |
| [`examples/multi_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/multi_demo.py) | Multiple WebViews, tabs, panes; `emit_all` flash |
| [`examples/plotly_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/plotly_demo.py) | Plotly charts — CDN or local `app=` (`pip install plotly`) |
| [`examples/folium_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/folium_demo.py) | Folium maps (`pip install folium`; tiles need the network) |
| [`examples/markdown_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/markdown_demo.py) | Monaco markdown editor + live preview (CDN) |
| [`examples/dnd_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/dnd_demo.py) | Native file drag & drop into WebView |

```bash
python examples/ipc_demo.py
python examples/multi_demo.py
python examples/plotly_demo.py
python examples/folium_demo.py
python examples/markdown_demo.py
python examples/dnd_demo.py
```

### Related: Jupyter-style widgets ([tkipw](https://github.com/mashu3/tkipw))

Built on tkwry. Use when you want the usual **ipywidgets / anywidget** stack in Tk (not plain HTML + JS):

| Script | Description |
|--------|-------------|
| [`plotly_demo.py`](https://github.com/mashu3/tkipw/blob/main/examples/plotly_demo.py) | Plotly `FigureWidget` |
| [`ipyleaflet_demo.py`](https://github.com/mashu3/tkipw/blob/main/examples/ipyleaflet_demo.py) | Live ipyleaflet map |

See the [tkipw examples](https://github.com/mashu3/tkipw/tree/main/examples) for more.

---

## ⚠️ Known limitations

Short checklist — **details live in [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md)** (especially [macOS embedding](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#macos-embedding)).

**Platforms**

- **Windows** — [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) required; missing → create-failed signals ([install notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#windows))
- **Linux** — no PyPI wheel (by design); best-effort source install; prefer sequential `eval_js_with_callback` across multiple views ([Linux](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#linux))

**Engine gaps / partial wraps** (no invented shims)

- **Print** — system dialog (`print()`; macOS also `print_with_options` for margins); no PDF / no result ([Print](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#print))
- **Downloads** — start-deny only; no mid-flight abort, pause/resume, or progress % ([Downloads](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#downloads))
- **Screenshot / find in page** — not exposed as tkwry APIs (Windows may still show engine Ctrl+F chrome) ([Screenshot](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#screenshot), [Find](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#find-in-page))

**macOS / Windows quirks**

- **macOS** — import `tkwry` before AppKit; IME not Safari-parity; inline `url()` may be `None`; DevTools needs `devtools=True` then `open_devtools()` (private APIs — avoid Mac App Store) ([macOS embedding](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#macos-embedding), [DevTools](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#devtools))
- **Windows DevTools** — `open_devtools()` works; `close_devtools` is a no-op; `is_devtools_open` always `False` ([DevTools](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#devtools))

**Trust & session**

- Shared non-ephemeral `WebSession` + `app=` must use the same root; do not share a persistent profile with untrusted sites
- External content / IPC defaults and `untrusted=` — [Trust boundaries](https://github.com/mashu3/tkwry/blob/main/docs/trust.md)

**Lifecycle & IPC**

- Sync `on_navigation` / `on_new_window` / `on_download` / create-time `permission_handler` may block the engine until they return (wait capped ~60s); do not create a WebView from `on_new_window` ([lifecycle callbacks](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#navigation--lifecycle-callbacks))
- RPC cancel / `destroy()` are cooperative only; async queues cap at 2048 each; IPC/RPC messages at 10 MiB ([RPC limits](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md#limits), [timeout/cancel](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md#timeout-and-cancel))
- Eval / navigation timeouts surface typed events/errors on the Tk thread (not raised on the WebKit thread)
- Native drag & drop is **WebView area only** and **notify-only** (cannot deny from Python; use [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) for arbitrary Tk widgets)

See [CHANGELOG.md](https://github.com/mashu3/tkwry/blob/main/CHANGELOG.md) for release history.

---

## 📤 Packaging (best-effort)

Freeze a tkwry app to a Windows ``.exe`` or macOS ``.app`` with PyInstaller or
Nuitka. **Not CI-verified in 0.1.x** — verify on your target OS. Full notes:
[docs/packaging.md](https://github.com/mashu3/tkwry/blob/main/docs/packaging.md).

```bash
pip install pyinstaller tkwry   # or: pip install nuitka tkwry
```

**PyInstaller — Windows ``.exe``**

```bat
pyinstaller --noconsole --onefile --collect-submodules tkwry --name MyApp main.py
```

**PyInstaller — macOS ``.app``**

```bash
pyinstaller --windowed --onedir --collect-submodules tkwry --name MyApp main.py
```

**Nuitka — Windows one-file ``.exe``**

```bat
python -m nuitka --standalone --onefile --windows-console-mode=disable --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --output-filename=MyApp.exe main.py
```

**Nuitka — macOS ``.app``** (Homebrew: include ``--static-libpython=no``)

```bash
python -m nuitka --standalone --macos-create-app-bundle --static-libpython=no --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --macos-app-name=MyApp main.py
```

Always collect / include the ``tkwry`` package (native ``_core``). Windows
users still need [WebView2](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).
Samples for ``examples/tkwry_browser.py``:
[docs/examples-browser.md — Packaging](https://github.com/mashu3/tkwry/blob/main/docs/examples-browser.md#packaging-best-effort).
``app=`` data dirs:
[docs/packaging.md](https://github.com/mashu3/tkwry/blob/main/docs/packaging.md).

---

## 🗂 Documentation

| Topic | Doc |
|-------|-----|
| Usage (minimal app, `app=`, hidden hosts, UA, API) | [docs/usage.md](https://github.com/mashu3/tkwry/blob/main/docs/usage.md) |
| Mini-browser example (flagship layout / sessions / trust) | [docs/examples-browser.md](https://github.com/mashu3/tkwry/blob/main/docs/examples-browser.md) |
| Trust boundaries (`untrusted`, `bridge_origins`, recipes) | [docs/trust.md](https://github.com/mashu3/tkwry/blob/main/docs/trust.md) |
| IPC / RPC / emit (`expose`, `call` / `stream`, cancel, limits) | [docs/rpc.md](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md) |
| Platform notes (Windows / macOS / Linux, print, window chrome) | [docs/platforms.md](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md) |
| wry embedding / API ownership map | [docs/wry-embedding.md](https://github.com/mashu3/tkwry/blob/main/docs/wry-embedding.md) |
| Packaging (PyInstaller / Nuitka → ``.exe`` / ``.app``) | [docs/packaging.md](https://github.com/mashu3/tkwry/blob/main/docs/packaging.md) |

---

## 📝 License

This project is licensed under the **MIT License**. See [LICENSE](https://github.com/mashu3/tkwry/blob/main/LICENSE).

This project links against [wry](https://github.com/tauri-apps/wry), which is dual-licensed (Apache-2.0 **or** MIT). tkwry uses wry under MIT; see [NOTICE](https://github.com/mashu3/tkwry/blob/main/NOTICE) for attribution.

---

## 👨‍💻 Author

[mashu3](https://github.com/mashu3)

[![Contributors](https://contrib.rocks/image?repo=mashu3/tkwry)](https://github.com/mashu3/tkwry/graphs/contributors)
