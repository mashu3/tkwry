# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

[0.0.5]: https://github.com/mashu3/tkwry/releases/tag/v0.0.5
[0.0.4]: https://github.com/mashu3/tkwry/releases/tag/v0.0.4
[0.0.3]: https://github.com/mashu3/tkwry/releases/tag/v0.0.3
[0.0.2]: https://github.com/mashu3/tkwry/releases/tag/v0.0.2
[0.0.1]: https://github.com/mashu3/tkwry/releases/tag/v0.0.1
