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
- **IPC / RPC / emit** — JS↔Python events, request/response, and streams without freezing the UI
- **Trust boundaries** — IPC/RPC default to the initial origin; `untrusted=True` for arbitrary sites
- **Layout-aware** — tracks `pack` / `grid` / `place`, tabs, and `PanedWindow`

Pre-built **abi3** wheels ship for **Windows** and **macOS**. **Linux** is source-only (**best-effort** by design) — see [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md).

---

## 🗂 Documentation

| Topic | Doc |
|-------|-----|
| Usage (minimal app, `app=`, hidden hosts, UA, API) | [docs/usage.md](https://github.com/mashu3/tkwry/blob/main/docs/usage.md) |
| Trust boundaries (`untrusted`, `bridge_origins`, recipes) | [docs/trust.md](https://github.com/mashu3/tkwry/blob/main/docs/trust.md) |
| IPC / RPC / emit (`expose`, `call` / `stream`, cancel, limits) | [docs/rpc.md](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md) |
| Platform notes (Windows / macOS / Linux, print, window chrome) | [docs/platforms.md](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md) |
| Packaging (PyInstaller / Nuitka notes — not CI-verified in 0.1.x) | [docs/packaging.md](https://github.com/mashu3/tkwry/blob/main/docs/packaging.md) |

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

## ⚠️ Known limitations

Short checklist — **details live in [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md)** (especially [macOS embedding](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#macos-embedding)).

- **Alpha** — APIs may change; not for production yet (see banner above)
- **Windows** — WebView2 Runtime required; missing runtime → `creation_failed` / `<<WebViewCreateFailed>>` (gated APIs raise `WebViewCreationError` with [install text](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#windows))
- **Print** — `web.print()` opens the system dialog; no PDF, no return value, no success/fail/cancel (wry has none). See [Platform notes — Print](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#print)
- **Window chrome** — title / icon / geometry / fullscreen / min/max / `-topmost` are the host **Toplevel** (`configure_window`); WebView size follows the Frame (`sync_bounds`). See [Usage — Layout / resize](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#layout--resize)
- **Windows DevTools** — wry/WebView2 reports `is_devtools_open()` as `False` and `close_devtools()` is a no-op; `open_devtools()` still opens the inspector
- **Linux** — no PyPI wheel (by design); best-effort source install
- **Linux concurrent `eval_js_with_callback`** — evaluating on multiple WebViews at once can stall WebKitGTK; prefer sequential evals (see [Linux](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#linux))
- **Shared `WebSession` + `app=`** — WebViews that share a non-ephemeral session must use the same `app=` root (`ValueError` otherwise; Linux can register `tkwry://` only once per context); do not share a persistent profile with untrusted sites
- **Trust / external content** — RPC/IPC default to the initial origin (optional path prefix / `bridge_allow`); `bridge_origins="*"` warns and needs `expose(..., allow_any_origin=True)`; `app=` locks navigation to `tkwry://` (`navigation_allow` / `open_external=True` for extra origins + system browser); `untrusted=True` also **denies downloads** unless `download_allow` / `on_download` permits (see [Trust boundaries](https://github.com/mashu3/tkwry/blob/main/docs/trust.md))
- **macOS DevTools** — create with `devtools=True`, then `open_devtools()` (flag alone does not open; `open_devtools()` without the flag is a no-op on macOS); uses private APIs — avoid in Mac App Store builds
- **macOS IME / focus** — not Safari-parity; mid-composition focus flips can mis-route input
- **macOS import order** — import `tkwry` before AppKit/`NSApplication`, or you may see a double titlebar
- **`url()` on macOS** — may be `None` for inline HTML until a concrete `load_url` (WKWebView has no document `NSURL`)
- **Sync hooks / queues** — `on_navigation` / `on_new_window` / create-time `permission_handler` may block WebKit up to ~60s; do not create a WebView from `on_new_window` (use `open_external=True` / `open_in_browser`); async event queues cap at 2048; IPC/RPC messages cap at 10 MiB (see [Usage — Navigation / lifecycle callbacks](https://github.com/mashu3/tkwry/blob/main/docs/usage.md#navigation--lifecycle-callbacks))
- **RPC cancel / destroy** — timeout, JS `cancel`, and `destroy()` are **cooperative only** (`rpc_cancelled()`), including open streams; Python cannot preempt a running worker. `destroy()` joins the pool for ~2 seconds; leftover threads are logged to stderr (see [IPC / RPC / emit](https://github.com/mashu3/tkwry/blob/main/docs/rpc.md#timeout-and-cancel))
- **Eval / navigation timeout** — `eval_js_with_callback` timeout (30s) is `WebViewTimeoutError` (`on_error`, `<<WebViewEvalFailed>>`, `last_eval_error`); `on_navigation` / `on_new_window` timeout still returns the default deny and signals `WebViewNavigationError` (`<<WebViewNavigationFailed>>`, `last_navigation_error`) — not raised on the WebKit thread
- **Drag & drop** — WebView area only (use [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) for arbitrary Tk widgets)
- **Screenshot** — no `WebView` capture API; wry 0.56.1 does not expose one yet ([wry#1674](https://github.com/tauri-apps/wry/pull/1674)). tkwry will wrap it when upstream ships; no JS fallback (see [Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md#screenshot))

See [CHANGELOG.md](https://github.com/mashu3/tkwry/blob/main/CHANGELOG.md) for release history.

---

## 🌐 Platform notes

Pre-built wheels: **Windows** and **macOS**. **Linux** is source-only (best-effort).

| OS | Arch | Parent handle | Engine |
|----|------|---------------|--------|
| **Windows** | x86_64, arm64 | `Frame.winfo_id()` → HWND | WebView2 |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK |

DPI, WebView2, macOS embedding / IME / import order, and Linux eval caveats:
[Platform notes](https://github.com/mashu3/tkwry/blob/main/docs/platforms.md).

---

## 💡 Why child-window embedding?

Tkinter apps already have a window and a layout. The web belongs **inside** a `Frame` — same `mainloop`, same tabs and panes — not in a separate top-level webview that floats beside your UI. tkwry wraps wry's `build_as_child` against the native surface Tk gives your widgets.

---

## 🧩 Features

- **Local app assets** — `app=` + `tkwry://` (SPA fallback, `app_dev` no-store, ETag/HEAD/Range, default CSP, optional COOP/CORP, bounded `watch_app()`; open-then-verify symlink/junction confinement)
- **IPC / RPC / emit** — events vs request/response; sync-generator `stream`; worker RPC; typed TypeError; protocol `version`; JS `cancel`; Python→JS `emit`; origin/path allowlist (`bridge_origins`) + `bridge_allow` + `untrusted=` viewer mode
- **WebSession** — shared wry `WebContext`; Cookie CRUD on `WebView` (`cookies` / `set_cookie` / …); shared `app=` roots must match; `emit_all` broadcast
- **Testing helpers** — `tkwry.testing.wait_until` / `wait_ready` / `wait_eval` / `wait_title`
- **Child-window embedding** — WebView is a native child of your Tk window surface, not a floating overlay
- **Bounds & visibility sync** — follows `<Configure>`, `<Map>`, and `<Unmap>` (tabs / `Notebook` hide unmapped views)
- **Typed failure signals** — create: `<<WebViewCreateFailed>>` / `when_failed`; eval: `<<WebViewEvalFailed>>` / `WebViewTimeoutError`; nav hook timeout: `<<WebViewNavigationFailed>>` / `WebViewNavigationError` (native still returns the default deny); downloads: `<<WebViewDownloadComplete>>` / `<<WebViewDownloadFailed>>` / `last_download`
- **Deferred callbacks** — IPC, RPC, page load, title, eval results, and DnD queue to Tk (avoids macOS deadlocks)
- **URL safety** — Python `load_url` normalizes/validates schemes; in-page nav denies `javascript:`/`blob:`/… (`data:` under `app=`); `app=` stays on `tkwry://`; IPC/RPC origin/path allowlist + `bridge_allow`
- **DevTools** — `devtools=True` at create, then `open_devtools()` / `close_devtools()` / `is_devtools_open()` (macOS: private APIs)
- **Print** — `web.print()` opens the system print dialog (no PDF / no result)
- **Downloads** — `on_download` / `on_download_complete` + `download_allow`; `untrusted=True` denies unless permitted; `last_download` + `<<WebViewDownloadComplete>>` / `<<WebViewDownloadFailed>>`; `unique_download_path` for same-name files (absolute dest only; no overwrite policy)
- **Native drag & drop** — OS-level file drops into the WebView (no tkinterdnd2)
- **Navigation hooks** — all handlers on the Tk thread; `on_navigation` / `on_new_window` block WebKit until they return
- **Multiple layouts** — works with `pack`, `grid`, `place`, `Notebook`, and `PanedWindow` (see examples)
- **Plotly-ready** — load HTML + `eval_js`; demo toggles CDN vs local `app=`
- **Folium-ready** — embed Leaflet maps from Folium HTML (right-click to pin)
- **Markdown-ready** — Monaco editor + live preview in a `PanedWindow` (see [`examples/markdown_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/markdown_demo.py); CDN required — or vendor under `app=`)
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
| [`examples/browser_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/browser_demo.py) | URL bar, tabs, shared `WebSession`, print / downloads / `emit_all` (`bridge_origins="*"`; no `expose`) |
| [`examples/ipc_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/ipc_demo.py) | IPC events, RPC (`call` / kwargs / worker), stream (`ticks` + cancel), and `emit` |
| [`examples/multi_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/multi_demo.py) | Multiple WebViews, tabs, panes; `emit_all` flash |
| [`examples/plotly_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/plotly_demo.py) | Plotly charts — CDN or local `app=` (`pip install plotly`) |
| [`examples/folium_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/folium_demo.py) | Folium maps (`pip install folium`; tiles need the network) |
| [`examples/markdown_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/markdown_demo.py) | Monaco markdown editor + live preview (CDN) |
| [`examples/dnd_demo.py`](https://github.com/mashu3/tkwry/blob/main/examples/dnd_demo.py) | Native file drag & drop into WebView |

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

This project is licensed under the **MIT License**. See [LICENSE](https://github.com/mashu3/tkwry/blob/main/LICENSE).

This project links against [wry](https://github.com/tauri-apps/wry), which is dual-licensed (Apache-2.0 **or** MIT). tkwry uses wry under MIT; see [NOTICE](https://github.com/mashu3/tkwry/blob/main/NOTICE) for attribution.

---

## 👨‍💻 Author

[mashu3](https://github.com/mashu3)

[![Contributors](https://contrib.rocks/image?repo=mashu3/tkwry)](https://github.com/mashu3/tkwry/graphs/contributors)
