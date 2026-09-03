# wry embedding and API map

How tkwry uses [wry](https://github.com/tauri-apps/wry) for native WebView
embedding, and which concerns stay in the Tk bridge.

Pinned crate: **wry 0.56.1** (`Cargo.toml`). User-facing OS notes stay short in
[platforms.md](platforms.md); this page is the ownership / wrap map for
contributors.

## Layering

```text
Tk Frame  →  Python (webview / _parent / _linux / _win32 / _macos)
          →  PyO3 `_core` (src/lib.rs)
          →  wry (build_as_child + set_bounds / set_visible / focus / …)
          →  OS engine (WebView2 / WKWebView / WebKitGTK)
```

**Rule of thumb:** OS child-window / subview attach, native bounds, visibility,
and first-responder style focus belong to **wry**. tkwry owns **Tk layout
events**, **parent-handle resolution**, **coordinate spaces**, and **host
coexistence** (GTK pump, HWND z-order, macOS keyboard routing).

**Anti-pattern:** reimplementing `setFrame` / `SetParent` /
`XCreateSimpleWindow` / `WebKitWebView` packing in tkwry because “we know the
OS.” Prefer wrapping wry; document gaps here and in [platforms.md](platforms.md).

## OS child embed (what wry owns)

| OS | wry internal | Parent placement | tkwry parent handle |
|----|--------------|------------------|---------------------|
| **macOS** | `WKWebView` as `NSView` subview | Added under the given `NSView` | Toplevel content `NSView` via `TkMacOSXGetRootControl` (`tkwry/_parent.py`) — child `Frame`s usually share one view; each WebView is wrapped in a clip container (`NSView` with `masksToBounds`) |
| **Windows** | WebView2 container `HWND` | Win32 child window (`SetParent`) | `Frame.winfo_id()` → HWND |
| **Linux (X11)** | X11 child window + GTK window + `WebKitWebView` | X11 child of the Tk window | `winfo_id()` → X11 window ID |
| **Linux (`gtk::Fixed`)** | wry `build_gtk` path | GTK container put | **Unused** — tkwry always uses `build_as_child` |

Create path: resolve handle → `raw_window_handle::WindowHandle` →
`WebViewBuilder::…build_as_child(&handle)` in `src/lib.rs`.

## Concern map

| Concern | tkwry | wry |
|---------|-------|-----|
| **Child embedding** | Pass parent handle | Create / attach native child |
| **Position / size** | Compute `(x, y, w, h)` from Tk (`_parent.py`, `_sync_bounds`) | `with_bounds` / `set_bounds` / `bounds` |
| **Coordinate space** | Win → Physical pixels (`make_rect`); macOS/Linux → Logical; macOS often `root_relative` on toplevel | Applies `Rect` to the native view |
| **Resize** | `<Configure>` / pack / grid / place → `_sync_bounds` | No Tk auto-resize in child mode; caller must `set_bounds` |
| **Visibility** | `<Map>` / `<Unmap>` / Notebook → `_frame_should_show` | `with_visible` / `set_visible` |
| **Focus** | Public `focus()` / `focus_parent()`; macOS NSEvent + Tcl key guard | `focus` / `focus_parent` / `with_focused` |
| **Z-order (Win)** | `raise_frame_webview` (`_win32.py`) after layout | No Tk stacking API |
| **Event loop (Linux)** | `GtkPump` + `pump_events` | Requires GTK init; does not drive Tk |

### Intentional tkwry layers (do not “dedup into wry”)

| Layer | Where | Why |
|-------|-------|-----|
| Tk `<Configure>` / `<Map>` / `<Unmap>` | `tkwry/webview.py` | wry child mode does not follow Tk geometry; unmapped Notebook tabs need `set_visible(False)` |
| Parent handle + coords | `tkwry/_parent.py`, `make_rect` | Tk Aqua / DPI / toplevel NSView quirks |
| GtkPump | `tkwry/_linux.py` | Coexist with Tk `mainloop` |
| Win HWND z-order | `tkwry/_win32.py` | Sibling Frame stacking |
| macOS keyboard routing | `src/macos/focus.rs`, `tkwry/_macos.py` | wry focus alone does not stop Tk dual-delivery |
| Wakeup write-end flags | `src/wakeup.rs` via `configure_wakeup_write_fd` | Single owner for `O_NONBLOCK` / `PIPE_NOWAIT`; Python only opens / registers / drains / closes |
| GTK init | `src/gtk_init.rs` | One idempotent path for create / session / pump |

## wry API map (major surface)

Status meanings: **Wrapped** = public or stable internal path; **Partial** =
platform-limited or deferred create-time focus; **Bridge** = tkwry policy on
top of a thin wry call; **Not exposed** = wry has it, tkwry does not ship it
yet; **Upstream gap** = wry lacks it (see [platforms.md](platforms.md)).

### Embed / window

| wry | tkwry | Notes |
|-----|-------|-------|
| `WebViewBuilder::build_as_child` | Always | Only create path |
| `build` / `build_gtk` / `gtk::Fixed` | Not used | Wayland / GTK-container embed is future Linux work |
| `with_bounds` / `set_bounds` / `bounds` | `WebView` sync + `bounds` property | Driven by Tk layout |
| `with_visible` / `set_visible` | Map/Unmap policy | Notebook tabs |
| `with_focused` / `focus` / `focus_parent` | Wrapped | macOS/Windows defer create-time `focused=True` until ready |
| `reparent` | Not exposed | Poor fit for Tk Toplevel recycle |

### Navigation / content

| wry | tkwry | Notes |
|-----|-------|-------|
| `with_url` / `with_html` / `load_url` / `load_html` | `url=` / `html=` / `load_*` | `html=` wins over `app=` for content; see trust docs |
| `with_initialization_script` | `add_init_script` / create scripts | Post-create inject is best-effort re-run on page Started |
| `evaluate_script` | `eval_js` / `execute_script` / `inject_script` | Coalescing rules documented in README |
| Navigation / page-load / title / new-window handlers | Matching `on_*` / events | Sync hooks may block WebKit briefly by design |
| `go_back` / `go_forward` | Wrapped | |
| Custom protocol | `app=` → `tkwry://` | Product path; not generic multi-scheme |

### Browser chrome / settings

| wry | tkwry | Notes |
|-----|-------|-------|
| `with_devtools` + open/close/is_open | Wrapped | Win: open works; close / is_open limited |
| `with_clipboard` | Create opt | |
| `with_javascript_disabled` | `javascript_enabled=False` | |
| `with_autoplay` | `autoplay=` | |
| `with_hotkeys_zoom` | `hotkeys_zoom=` | Win engine zoom keys |
| `with_back_forward_navigation_gestures` | `back_forward_gestures=` | |
| `with_default_context_menus` (Win) | `default_context_menus=` | Custom Tk menus need this off |
| `with_https_scheme` (Win) | `https_scheme=` | `app=` secure context |
| `with_incognito` / context | `ephemeral=` / `incognito=` / `WebSession` / `profile=` | |
| `with_proxy_config` | `proxy=` | No credentials in logs |
| `zoom` | `set_zoom` / `reset_zoom` | |
| `Theme` / `set_theme` | Not exposed yet | Host + page recipe planned for Beta |
| Transparent background | Not exposed yet | Win host overlay limits |

### Downloads / print / cookies / IPC

| wry | tkwry | Notes |
|-----|-------|-------|
| Download started / completed | `on_download*` / `Download` / events | Start-deny only; **no** mid-flight abort or progress % |
| `print` / macOS `print_with_options` | Wrapped | No PDF / no result callback |
| Cookies / clear browsing data | Wrapped | Never log values |
| Permission handler | Wrapped | |
| IPC handler | `expose` / RPC / `emit` | Trust / origins are tkwry |
| Find in page | Upstream gap | No shim via `window.find` |
| Screenshot / `print_to_pdf` | Upstream gap | Wrap when wry ships |

## Resize / visibility flow

```text
Tk <Configure> / <Map> / <Unmap>
        │
        ▼
  _sync_bounds / _frame_should_show
        │
        ├─ hide → wry set_visible(False)
        └─ show → set_bounds(x,y,w,h) → set_visible(True)
                     │
                     └─ Windows: optional HWND z-order sync
```

`ready` follows **layout size**, not map. A laid-out but unmapped Notebook tab
can stay `ready` while the native view is hidden — prefer work after the tab
is selected ([platforms.md — macOS embedding](platforms.md#macos-embedding)).

## On every wry bump

1. Re-read child-mode docs: auto-resize? z-order? focus helpers?
2. Update the tables above and [platforms.md](platforms.md) for new/removed
   engine gaps (PDF, find, screenshot, download progress).
3. Only shrink tkwry bridge code when the new wry API truly replaces a Tk
   concern — do **not** drop `<Configure>` sync on rumor.
4. Run platform CI scripts when touching embed / pump / focus / destroy.

## Related

- [Platform notes](platforms.md) — install, DPI, DevTools, print, downloads
- [Usage](usage.md) — layout / resize, cleanup
- [Trust boundaries](trust.md) — `app=` / bridge origins
- Source: `tkwry/webview.py`, `tkwry/_parent.py`, `src/lib.rs`, `src/gtk_init.rs`,
  `src/wakeup.rs`, `src/macos/focus.rs`
