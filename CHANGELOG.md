# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

[0.0.3]: https://github.com/mashu3/tkwry/releases/tag/v0.0.3
[0.0.2]: https://github.com/mashu3/tkwry/releases/tag/v0.0.2
[0.0.1]: https://github.com/mashu3/tkwry/releases/tag/v0.0.1
