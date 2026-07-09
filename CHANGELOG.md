# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.0.8] - 2026-07-10

### Fixed

- macOS `url()` reads the document URL from `WKWebView` directly so inline HTML / missing `NSURL` no longer panics in wry 0.55
- URL normalization extended: bracket IPv6 hosts, resolve Windows drive roots and IDN paths; reject bare paths as `https` and pathless `file://` URLs
- Constructor `url=` validated at WebView construction time
- Deferred initial load canceled on `reload()`, rescheduled when the frame is not ready, and prevented from overwriting later `load_url` / `load_html`
- Ready state reset when the host frame is unmapped; deferred ready callbacks skipped after destroy
- `<<WebViewReady>>` delivered on idle; late bind delivery routed through `_invoke_callback` with a guard when the probe event is missed
- `eval_js_with_callback` polling made race-safe across threads; stale polls expire after timeout
- Event queue rejects pushes when full; lock poison surfaced; TOCTOU closed so disabled events cannot requeue; async events delivered only from Python
- Teardown hardened: `destroy_pending` retained until native teardown completes; native reference cleared on failed `destroy()`; host-frame Tk handlers unbound; GtkPump stopped when the last Linux WebView is destroyed; macOS `bind_all` / `bind_class` hooks torn down with the last WebView; interp thread map released on Tk destroy
- GtkPump tracks attachments per widget, cancels pending ticks when pumping stops, and avoids clearing refcount
- macOS Tk dylib handles cached per Tcl library path; key guard reliably unbound; drawable offsets probed from natives
- `DragDropHandler` is notify-only
- Navigation handler type errors no longer print a spurious traceback
- Multi-WebView eval wait hardened against empty interim JS results
- WebView create, load lifecycle, setters, and dimension validation hardened against teardown races

### Changed

- `wait_until_ready()` requires a finite timeout; reentrancy documented
- Regression tests for `<<WebViewReady>>` delivery ordering and JS IPC end-to-end

## [0.0.7] - 2026-07-08

### Added

- `examples/markdown_demo.py` — Monaco markdown editor with live preview, tabs, save, split themes, and native dark chrome
- `wait_until_ready()` — pump the Tk loop until the host frame is laid out and the WebView is ready
- `eval_js` / `eval_js_with_callback` — optional `on_error` handler for evaluation failures on the Tk main thread
- URL normalization for `host:port`, `host/path` inputs misread by `urlparse`, and Windows `file://C:/...` → `file:///C:/...`

### Fixed

- Tk thread ownership enforced on native WebView API calls
- Async callbacks queued on the Tk thread; synchronous handler errors reported instead of swallowed
- Reentrant deadlocks prevented in native WebView callbacks
- Avoid Tk `after()` from the WebKit thread when delivering eval results
- `eval_js_with_callback` polling kept alive so late callbacks are not dropped
- `about:blank` treated as no document URL for `load_html`
- WebView creation size resolved per axis instead of applying 800×600 defaults
- `<<WebViewReady>>` deferred until an explicit-size host frame is laid out
- Skip 1×1 bounds sync until host geometry is meaningful
- `wait_until_ready()` returns `True` only when layout is ready
- `NativeWebView.url()` propagates errors; typed as `str | None`
- `set_on_navigation(None)` calls `clear_on_navigation` directly
- `background_color` rejects bool values at the Python boundary
- GtkPump strong refs avoided in Gtk `after` callbacks
- Removed unused `_on_page_load` from the native WebView stub and Rust API
- Linux CI: eval-poll unit tests isolated from GTK pump and Tk timer leaks

### Changed

- README: git installs require a Rust source build; documents `markdown_demo` and `eval_js` `on_error`
- Integration tests use `wait_until_ready()` instead of ad-hoc polling

## [0.0.6] - 2026-07-05

### Added

- `examples/folium_demo.py` — Folium maps via `load_html`, city hall markers, right-click pins
- Pre-built **abi3** wheels for **Windows arm64** (alongside x86_64)
- `clear_on_new_window()` — `set_on_new_window(None)` clears the Rust callback (matches other handlers)
- CI: stubtest for typed `_core` API; `windows-11-arm` in test and release matrices
- Integration tests: `reload()` after ready; macOS focus, title-changed, and multi-WebView coverage

### Fixed

- macOS key guard for `ttk.Combobox` and dynamically added text widgets (`<Map>`); removed per-pump full-tree rescans
- Log when `page_load_pending` queue overflows (was silent discard)
- `set_on_title_changed(None)` and `set_drag_drop_handler(None)` clear Rust callbacks
- `_sync_bounds` / `_schedule_bounds_sync` guard against `TclError` on destroyed frames
- `page_load_pending` capped to prevent unbounded growth
- macOS focus helpers moved to `_macos.py`; wakeup pipe fd teardown order

### Changed

- README: current macOS focus routing, WebKit-thread vs Tk-thread callbacks, `EvalErrorHandler`, Linux CI scope, Windows arm64
- `folium_demo` uses `when_ready` for initial map load; documents CDN/network requirement

## [0.0.5] - 2026-07-04

### Fixed

- Initial load no longer silently lost on macOS (`after_idle` scheduling removed)
- Native webview always created as visible to prevent script execution stalls
- `_schedule_bounds_sync` restores `update_idletasks` so the frame is mapped before webview creation
- Initial load not abandoned when the frame is not yet viewable
- Mutex-poisoned errors propagated from callback setter methods instead of silently ignored
- `background_color` components validated at the Python boundary with clear error messages
- `_looks_like_file_path` avoids filesystem I/O — uses string heuristics only
- 10 MiB size limit on IPC messages to prevent DoS from malicious pages
- `when_ready` callbacks routed through `_invoke_callback` for consistent error handling
- Dead `set_on_page_load` stub removed from the Rust native layer
- `_widget_threads` entries cleaned up on widget GC to prevent memory leak
- `load_html` errors propagated instead of silently ignored
- macOS system `Tk.framework` supported; Tk 8.5 pointer truncation fixed
- `_version.py` hardened — catches only `PackageNotFoundError` and guards `Cargo.toml` fallback

### Changed

- `_sync_bounds` debounced to reduce CPU load during rapid resizes
- `Optional[X]` unified to `X | None` across `webview.py`
- `conftest.py` uses pytest `pythonpath` setting instead of `sys.path` hack
- CI: pip/cargo caches, ruff format check, version tag guard, and fast CI profile

## [0.0.4] - 2026-07-04

### Added

- `file://` URLs and local filesystem paths in `load_url` (relative assets resolve correctly)
- Public callback type aliases (`IpcHandler`, `NavigationHandler`, `PageLoadHandler`, and others)
- `WebView.__repr__` for easier debugging
- Off-thread `WebView` API calls raise `RuntimeError` instead of failing unpredictably

### Fixed

- `<<WebViewReady>>` handlers bound after ready now receive a Tk event argument
- `set_on_new_window(None)` and `set_on_navigation(None)` clear the active handler
- Explicit `width=800, height=600` is treated as an intentional size (no magic-number default check)
- Windows drive paths (`C:\...`) normalize to `file://` URIs
- macOS no longer creates a temporary `Tk()` to resolve the libtk path

## [0.0.3] - 2026-07-02

### Added

- `sync_bounds()` — manually push host frame geometry to the native WebView
- Documented navigation (**last-wins**), page-load, and `eval_js` / `eval_js_with_callback` semantics
- Callback exceptions are printed to stderr; the Tk event poll keeps running

### Fixed

- Initial URL/HTML load deferred until after bounds sync (fixes blank startup on macOS)
- `eval_js_with_callback` pairs each result with its callback (no FIFO mismatch)
- Linux Xvfb: do not rely on `winfo_viewable()` for bounds and initial load

### Changed

- **Linux stability is best-effort for v0.0.x** — release quality targets **Windows** and **macOS** wheels; Linux remains source-installable but timing, headless CI, and edge cases are not release blockers

## [0.0.2] - 2026-07-01

### Fixed

- WebView bounds sync after `pack`/`grid`/`place` and on initial embed (Windows WebView2 layout glitches)
- Page-load event drain only when an `on_page_load` handler is set

### Added

- Integration tests for layout bounds sync and JS viewport size on Windows

## [0.0.1] - 2026-06-23

### Added

- `WebView` widget — embed wry as a true child of a Tkinter `Frame` (HWND / NSView / X11)
- Layout sync for `pack`, `grid`, `place`, tabs, and `PanedWindow`
- IPC bridge (`window.ipc.postMessage`) with Tk-thread queueing
- Navigation hooks: `on_navigation`, `on_page_load`, `on_title_changed`, `on_new_window`
- Native OS drag-and-drop into the WebView (`drag_drop_handler`, `DragDropEvent`)
- `load_url`, `load_html`, `reload`, `eval_js`, `eval_js_with_callback`
- DevTools, `focus`, `background_color`, `user_agent`, `initialization_script`
- URL normalization and `http`/`https` validation
- Pre-built **abi3** wheels for Windows (x86_64) and macOS (arm64 + Intel)
- Examples: `url_demo`, `ipc_demo`, `multi_demo`, `plotly_demo`, `dnd_demo`
- CI on Linux (Xvfb + WebKitGTK), Windows, and macOS

### Known limitations

- **Alpha** — APIs may change without notice
- **macOS** — child `Frame`s share the toplevel content view; tkwry syncs bounds and visibility automatically (including `ttk.Notebook` tabs)
- **Linux** — no PyPI wheels; build from source with WebKitGTK 4.1
- **DevTools** — uses private APIs on macOS; avoid in App Store release builds
- Drag-and-drop targets the WebView region only (not arbitrary Tk widgets)

[0.0.8]: https://github.com/mashu3/tkwry/releases/tag/v0.0.8
[0.0.7]: https://github.com/mashu3/tkwry/releases/tag/v0.0.7
[0.0.6]: https://github.com/mashu3/tkwry/releases/tag/v0.0.6
[0.0.5]: https://github.com/mashu3/tkwry/releases/tag/v0.0.5
[0.0.4]: https://github.com/mashu3/tkwry/releases/tag/v0.0.4
[0.0.3]: https://github.com/mashu3/tkwry/releases/tag/v0.0.3
[0.0.2]: https://github.com/mashu3/tkwry/releases/tag/v0.0.2
[0.0.1]: https://github.com/mashu3/tkwry/releases/tag/v0.0.1
