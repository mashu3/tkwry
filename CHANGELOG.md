# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Release provenance: ``SHA256SUMS`` on GitHub Releases, artifact build
  attestations in ``release.yml``, and [docs/provenance.md](docs/provenance.md)
  (checksum verify, ``gh attestation verify``, optional ``cargo audit`` note)

## [0.1.5] - 2026-08-30

Browser essentials (cookies, headers, zoom, permission, clipboard), 0.1.4
download-complete / RPC fixes, docs CI, and tag gates.

### Added

- Cookie / browsing-data wrap: ``Cookie``, ``cookies`` / ``cookies_for_url``,
  ``set_cookie`` / ``delete_cookie``, ``clear_all_browsing_data`` (wry 0.56
  names on ``WebView``; ``repr(Cookie)`` omits ``value``)
- ``load_url(..., headers={...})`` — extra headers on that navigation only
  (http(s); values never logged; not a ``navigator.userAgent`` spoof)
- ``set_zoom(scale)`` / ``reset_zoom()`` — page zoom (wry ``WebView::zoom``;
  ``1.0`` = 100%; no tkwry clamp; not Tk window zoom)
- Create-time ``permission_handler=`` → wry ``with_permission_handler``;
  ``PermissionKind`` / ``PermissionResponse`` (Allow / Deny / Default). Sync
  hook on the Tk thread (same family as ``on_navigation``). Omit handler for
  engine default; does **not** change ``untrusted=True`` defaults
- Create-time ``clipboard=True`` (default ``False``) → wry ``with_clipboard``;
  Win/Linux Web Clipboard API opt-in; macOS WebView side always-on (document)
- ``configure_window(...)`` — host Toplevel chrome helper (title / geometry /
  min·max / fullscreen / ``-topmost`` / icon); not a WebView bounds API
- ``QueueDropCounts`` + ``take_queue_drop_stats()`` — named overflow snapshot
  including ``download_complete`` and ``rpc_stream``; legacy 6-tuple
  ``take_queue_drop_counts()`` unchanged

### Fixed

- Cap worker→Tk RPC stream chunk queue at 2048 and count overflows
  (``rpc_stream``); ``WebSession.emit_all`` continues after a sibling
  ``emit`` failure (counts only successful sends)
- Linux: initialize GTK before constructing ``WebSession`` / wry ``WebContext``
  (``ephemeral=True`` / ``data_directory=`` in ``WebView.__init__`` no longer
  panics with ``GTK has not been initialized`` in a fresh process)
- Deliver download-complete ``last_download`` / ``<<WebViewDownloadComplete>>`` /
  ``<<WebViewDownloadFailed>>`` without ``on_download_complete`` (wakeup path;
  no idle ``_webview is not None`` poll latch)
- Windows (and Tk without ``createfilehandler``): after-poll the shared wakeup
  pipe so handler-less download-complete still drains (D21 gap on Required)
- Provisional ``on_callback_error=(exc, kind)`` / ``set_on_callback_error``
  for lifecycle / IPC / page-load / title / DnD / download-complete /
  ``when_ready`` / ``when_failed`` callback exceptions (default remains stderr)
- Windows CI: run long-lived ``thread=True`` RPC stress tests
  (timeout / destroy-during-worker / JS cancel) in a separate pytest process
  so arm64 does not abort with ``0x80000003`` after a long ``test_content``
  create/destroy streak
- Linux / Windows / macOS CI: run ``tests/unit/test_sync_hooks.py`` in a
  separate pytest process (worker + Tk pump under GC aborted the full suite
  after the create/destroy streak grew)
- macOS CI: stabilize sync-hook pre-start timeout unit test (GHA Tk drain
  timing)

### Tests

- Dual ctor/setter equivalence for lifecycle handler APIs
  (``test_dual_api_equivalence.py`` — Beta B3 partial)
- Browser-essentials integration (local HTTP): ``load_url`` custom headers,
  ``Set-Cookie`` / ``set_cookie`` / ``delete_cookie`` /
  ``clear_all_browsing_data``, zoom + permission + clipboard smoke, and
  post-``destroy`` → ``WebViewDestroyedError``; own pytest process on
  Linux / Windows CI
- Docs CI (``scripts/check_docs.py``): ``__all__`` ↔ ``docs/usage.md`` API
  summary, ``ast.parse`` on README / ``docs/*.md`` Python fences, relative
  + GitHub-blob link / anchor / example-path checks

### Changed

- Release workflow publishes to PyPI with ``pypa/gh-action-pypi-publish``
  (Trusted Publishing unchanged). ``maturin upload`` is deprecated
  ([maturin#2334](https://github.com/PyO3/maturin/issues/2334))
- Release tags reuse CI (lint / stubtest / platform tests) before wheels;
  wheel + sdist import smoke, ``twine check``, then PyPI, then GitHub Release
- Move README how-to (``app=``, eval, layout, navigation, downloads, API
  table) to ``docs/usage.md``; README Usage is the Basic example plus links
- Document hidden-host switching: unmap (Notebook / ``pack_forget``) hides
  native; ``lift`` of mapped Frames overlaps; constructor size is eager
  warmup, not lazy create
- Document create-only ``user_agent=`` as app identity (engine may
  prefix/suffix; not a Chrome-spoof / fingerprint kit). Third-party
  sites that degrade in-WebView (e.g. YouTube comments) → system browser
- Minimal-app tutorial + hidden-host recipe table in ``docs/usage.md``
  (Notebook / ``pack_forget`` vs constructor warmup vs ``lift`` overlap;
  links to ``test_notebook`` / examples)
- README repo links are absolute GitHub URLs so PyPI long-description
  keeps working after the Usage split; ``project.urls.Documentation``
  points at ``docs/usage.md``

## [0.1.4] - 2026-08-14

Wrap wry 0.56 (print, downloads, back/forward, streaming RPC) and close
the 0.1.4 contract.

### Added

- Navigation helpers: ``navigation_allow`` (extra in-webview origins / path
  prefixes), ``open_external=True`` (off-list http(s) → system browser;
  never creates a WebView from ``on_new_window``), and ``open_in_browser()``
- ``go_back`` / ``go_forward`` / ``can_go_back`` / ``can_go_forward`` (wry
  0.56); ``examples/browser_demo.py`` Back / Forward buttons
- ``app=`` / ``tkwry://`` default Content-Security-Policy (``csp=False`` or a
  custom string to override); optional ``coop=True`` / ``corp=True``
- Create-failed observability: ``<<WebViewCreateFailed>>``, ``when_failed``,
  and constructor ``on_creation_failed=`` (constructor still does not raise)
- Downloads: ``on_download`` / ``on_download_complete`` wrap wry start/complete;
  ``download_allow`` origin/path allowlist; ``untrusted=True`` denies downloads
  unless a handler or allowlist permits. Completions set ``last_download`` and
  generate ``<<WebViewDownloadComplete>>`` / ``<<WebViewDownloadFailed>>``.
  ``unique_download_path(dest)`` returns a free absolute path for same-name
  files (``on_download`` dests must be absolute; tkwry does not overwrite)
- ``WebView.print()`` → wry system print dialog
- ``WebSession.emit_all(event, data)`` broadcasts ``emit`` to siblings sharing
  the session (skips untrusted / not-ready / disallowed ``bridge_origins``)
- Streaming RPC: ``window.tkwry.stream`` consumes sync generator
  ``@web.expose`` handlers as JSON chunks (protocol ``version: 1`` +
  ``stream: true``); ``call`` on a generator rejects with ``TypeError``.
  Each chunk is capped at 10 MiB (``RpcMessageTooLarge``). JS ``cancel``
  and ``destroy()`` cancel open streams cooperatively. Handler errors
  reject the iterator — no second error channel
- ``WebViewTimeoutError`` / ``WebViewNavigationError``: eval timeout and
  dropped eval results generate ``<<WebViewEvalFailed>>`` (``last_eval_error``);
  ``on_navigation`` / ``on_new_window`` hook timeouts still return the default
  deny and generate ``<<WebViewNavigationFailed>>`` (``last_navigation_error``)

### Changed

- Split README into a landing page plus ``docs/trust.md``, ``docs/rpc.md``,
  and ``docs/platforms.md``. Trust recipes (local / untrusted / mixed
  sessions), Origin / ``download_allow`` error table, ``print()`` honesty
  (system dialog, no PDF / no result), and window chrome = host Toplevel
  (WebView follows the Frame)
- Bump wry ``0.55.1`` → ``0.56.1`` (IPC no longer panics on invalid document
  URIs; Windows minimized-focus / teardown crash fixes). macOS ``url()`` still
  uses the WKWebView workaround — wry's wrapper can still panic on inline HTML
- Examples: ``browser_demo`` print / downloads / create-failed / ``emit_all``
  toasts; ``multi_demo`` shared session + flash-all; ``ipc_demo``
  ``on_creation_failed`` and stream ticks + cancel
- Document that screenshot is unavailable until wry exposes capture
  (``WebView`` has no API in 0.56.1; [wry#1674](https://github.com/tauri-apps/wry/pull/1674))

### Fixed

- ``__version__`` reads ``Cargo.toml`` in a source checkout, so a bump is
  visible before the next ``maturin develop`` / ``pip install`` (wheels still
  use distribution metadata)
- Post-``destroy()`` WebView commands raise ``WebViewDestroyedError``
  (including ``wait_until_ready`` and ``emit``). ``destroy()`` stays
  idempotent; snapshot properties and ``take_queue_drop_counts()`` remain
  readable. A ``wait_until_ready`` that observes destroy mid-wait still
  returns ``False``
- ``download_allow=["*"]`` raises a download-specific ``ValueError``;
  ``RpcOriginError`` names the page URL and hints to extend
  ``bridge_origins``

## [0.1.3] - 2026-08-13

Trust boundaries for IPC/RPC, typed/cancellable RPC, and hardened
``tkwry://`` serving.

### Added

- Trust boundaries: ``untrusted=True`` viewer mode (no IPC/RPC, ephemeral
  session, http(s) only); ``bridge_origins`` allowlist (default infers the
  initial content origin; ``"*"`` allows every page); ``app=`` locks in-page
  navigation to ``tkwry://`` and denies new windows unless ``on_navigation`` /
  ``on_new_window`` is set
- IPC/RPC carry the page URL; foreign origins drop IPC and reject RPC with
  ``RpcOriginError``; ``tkwry://`` requests with a non-app ``Origin``/``Referer``
  return 403; native navigation denies ``javascript:`` / ``blob:`` /
  ``vbscript:`` / ``mailto:`` (not ``data:`` — WebView2 ``html=`` uses it);
  ``app=`` still rejects ``data:`` in Python policy
- RPC protocol ``version: 1`` (unknown versions → ``RpcProtocolError``);
  ``window.tkwry.cancel(id)`` / ``promise.cancel()`` → ``RpcCancelledError``
- Typed RPC bind: arity / simple annotation mismatch rejects as ``TypeError``
- ``tkwry://`` ``HEAD``, ``ETag`` / ``If-None-Match`` (304), single byte
  ``Range`` (206); SPA fallback skips non-HTML ``Accept`` and static assets
- Public signature guard (``_core.pyi`` vs runtime ``__new__`` / ``WebView.__init__``)
- ``watch_app(suffixes=..., ignore_dirs=..., max_files=...)`` — default web
  suffixes, skip ``node_modules`` / ``.git`` / ``.vendor`` / build dirs, cap
  at 2000 files (``suffixes="*"`` watches everything)
- ``window.tkwry.debug`` (default on) logs ``emit`` listener exceptions via
  ``console.error``; set ``false`` to silence
- ``tkwry://`` / ``app=`` serving canonicalizes paths and refuses symlinks,
  Windows junctions, and reparse points that escape the app root
- ``bridge_allow`` callback and origin **path prefixes** on ``bridge_origins``
  (``https://trusted.example/app`` matches ``/app`` and descendants only)
- ``TkwrySecurityWarning`` when ``bridge_origins="*"`` (and again with
  ``devtools=True``); ``expose(..., allow_any_origin=True)`` required for ``"*"``
- ``RpcSerializationError`` for non-JSON RPC results and ``emit`` payloads
  (no more ``default=str`` / NaN / Infinity)
- Structured ``RpcMessageTooLarge`` when an RPC envelope exceeds 10 MiB
  (request id is recovered when possible so the Promise can reject)
- ``RpcArgumentLimitError`` when an RPC call has more than 256 positional
  args or kwargs
- Cooperative RPC cancel: ``rpc_cancelled()`` / ``rpc_cancel_event()``;
  timeout and destroy set the flag (``Future.cancel()`` still cannot stop
  running Python). Destroy joins workers for ~2s; leftover threads log to stderr

### Changed

- README documents trust defaults (``untrusted``, origin/path allowlists,
  ``"*"`` warning) and warns against sharing a persistent ``WebSession`` with
  external sites; ``examples/browser_demo.py`` sets ``bridge_origins="*"``
  explicitly (link interception only; warning expected)
- ``untrusted=True`` cannot be combined with ``bridge_origins`` / ``bridge_allow``
- ``set_bridge_origins("*")`` is refused unless every ``expose()`` used
  ``allow_any_origin=True``
- Documented that ``expose(timeout=…)`` does not preempt worker threads and
  is ignored for synchronous ``run_in="main"`` handlers; cancel/destroy are
  specified as cooperative only (Python threads cannot be killed)
- ``watch_app()`` no longer follows directory or file symlinks when scanning
  mtimes
- Shared ``WebSession`` + ``app=`` constraint is called out on ``WebSession`` /
  ``WebView.__init__``, README, and ``examples/browser_demo.py``; Python raises
  ``ValueError`` before native create when roots differ

### Fixed

- Worker RPC completions settle on the Tk event poll instead of ``after_idle``
  from the pool thread; destroy aborts inflight RPC without ``eval_js``,
  joins pool threads, and does not reschedule poll on a dead frame
  (avoids Tcl/native abort on the next Tk update)
- ``tkwry://`` open+inode (Unix) / ``GetFileInformationByHandle`` (Windows)
  check closes the canonicalize-then-read TOCTOU window for escaped
  symlinks / reparse points (stable std; no ``windows_by_handle``)

## [0.1.2] - 2026-08-12

Local ``app=`` / ``tkwry://`` apps, JS↔Python RPC and ``emit``, shared
``WebSession``, and example cleanup (tabbed browser + Plotly CDN/local).

### Added

- ``WebView(app=...)`` — serve a local HTML/CSS/JS tree via the ``tkwry://``
  custom protocol (no localhost HTTP server). Pass a directory with
  ``index.html`` or a path to an HTML entry file.
- ``spa_fallback=True``, ``app_dev=True`` (``Cache-Control: no-store``),
  ``watch_app()`` mtime hot reload; expanded MIME types
- Plotly demo toggles CDN vs local ``app=`` (``plotly.js`` cached in
  ``examples/.vendor/``)
- Unit / integration coverage for ``tkwry://`` URL rules and relative CSS via
  ``app=``
- RPC: ``WebView.expose`` / ``@web.expose`` and ``window.tkwry.call``
  (JSON envelope over existing IPC; ``ipc_handler`` unchanged)
- Worker RPC: ``@web.expose(thread=True)`` / ``run_in="worker"``
  (ThreadPoolExecutor); optional ``timeout`` (seconds); JS
  ``call(..., { timeout: ms, kwargs: { … } })``
- Structured RPC errors (``type`` / ``message`` / optional ``traceback`` via
  ``rpc_traceback=True`` or ``TKWRY_RPC_TRACEBACK=1``); ``RpcTimeoutError``
- RPC name collision guard (``replace=True``); ``unexpose``; destroy rejects
  in-flight Promises
- Dedicated RPC queue (cap 2048) so IPC overflow cannot drop ``tkwry.call``
- ``expose`` handlers may return a ``concurrent.futures.Future``; the Promise
  settles when the Future completes (Tk-thread ``after_idle``)
- Python → JS events: ``web.emit(event, data)`` / ``window.tkwry.on`` / ``off``
- ``examples/ipc_demo.py`` (IPC events, RPC, ``emit``) and ``tkwry.ipc`` helpers
- ``tkwry.testing`` helpers (``wait_until``, ``wait_ready``, ``wait_eval``,
  ``wait_title``)
- ``WebSession`` — shared wry ``WebContext`` for cookies / cache /
  localStorage across WebViews (``session=`` / ``data_directory=`` /
  ``ephemeral=``); ``examples/browser_demo.py``

### Changed

- Folded ``examples/rpc_demo.py`` into ``examples/ipc_demo.py`` (IPC + RPC +
  ``emit`` in one two-pane demo)
- Folded ``url_demo`` / ``session_demo`` into ``examples/browser_demo.py``
  (URL bar, tabs, shared ``WebSession``, link menu → new tab)
- ``take_queue_drop_counts()`` is now
  ``(ipc, page_load, title, drag_drop, eval, rpc)``
- URL layer accepts ``tkwry://localhost/...`` (requires ``app=`` at create)
- Windows custom-protocol origins use HTTPS scheme (wry
  ``with_https_scheme``) so they align more closely with macOS/Linux
- Document IPC = events, RPC = request/response, emit = Python→JS; default
  ``@web.expose`` stays on the Tk main thread (use ``thread=True`` for heavy
  work)
- Split ``webview.py``: host/wakeup helpers → ``_host.py``, IPC/RPC/emit/
  ``watch_app`` → ``_rpc_api.WebViewRpcMixin`` (public API unchanged)

### Removed

- ``examples/macos_double_titlebar_repro.py`` — maintainer repro, not a demo;
  import-order guidance stays in README
- ``examples/local_assets_demo.py`` — ``app=`` is covered by the Plotly demo's
  Local mode
- ``examples/url_demo.py`` / ``session_demo.py`` — folded into
  ``examples/browser_demo.py`` (URL bar, tabs, shared ``WebSession``)

### Fixed

- Windows: rewrite ``tkwry://`` → ``https://tkwry.localhost/...`` on
  ``load_url`` so deferred ``app=`` navigation reaches the custom protocol
  (wry only rewrote ``with_url`` at create)
- macOS: ``open_devtools`` / ``close_devtools`` no longer hold the native
  ``inner`` lock across the nested WebKit turn (deadlock after prior WebView
  teardown in the same process; hung ``test_create_options`` suite)
- Linux: honor ``winfo_ismapped()`` for map/visibility so inactive Notebook
  tabs ``set_visible(False)`` / ``phase=HIDDEN`` (Xvfb still treats mapped but
  non-viewable hosts as showable)

## [0.1.1] - 2026-08-04

Patch release: Windows DPI/focus fixes, macOS destroy-safe event handlers, and
hidden-host initial-load warmup when constructor size is set.

### Fixed

- Windows: use physical WebView bounds when the process is DPI-aware (Tk
  `winfo_*` already matches the embed HWND; wry `Logical` double-scaled)
- Windows: defer `focused=True` until `<<WebViewReady>>` (WebView2 `MoveFocus`
  during create can return `E_INVALIDARG` on cloaked / unfocused hosts)
- macOS: resolve `event.widget` safely when it is a path `str` after destroy
  (focus / key-guard / map / destroy handlers no longer raise)
- Allow initial Navigate while the host is still hidden or 1×1 when constructor
  `width`/`height` are set (off-screen warmup); without size, viewable wait is
  unchanged (Notebook tabs)
- Stabilize create-options integration tests on Windows WebView2

### Changed

- Document that DevTools requires the constructor `devtools=True` flag; expand
  create-only option coverage
- `examples/markdown_demo.py`: preview pane toggle in the editor tab strip
- Windows CI: split unit / integration pytest processes (avoid WebView2 hangs
  after many create/destroy cycles, especially on arm64)

## [0.1.0] - 2026-07-16

First minor release after the 0.0.x series. Windows/macOS wheels remain the
supported install path; Linux stays source-only (best-effort) by design.

### Added

- `WebViewPhase` — derived lifecycle snapshot (`PRE_CREATE` … `DESTROYED`)
- Hard-fail with a clear install hint when the Windows WebView2 Runtime is missing
- Documented size contract: mapped host `winfo_*` is authoritative; constructor /
  `place(..., width=, height=)` sizes apply only before Tk reports a real size
- README contracts for sync-hook wait (~60s), async queue caps (2048), Linux
  concurrent `eval_js_with_callback` stalls, and optional macOS DevTools

### Fixed

- macOS keyboard ownership: resign WebView when Tk editable takes focus; peel
  stale Tcl Entry focus while Web owns the keyboard; separate cache query from
  Idle→Web Tcl rising-edge
- Linux GtkPump: yield under Xvfb so multi-WebView `page_load` works; nest drains
  through `pump_gtk_unless_active`; queue-only service when GtkPump owns the
  toplevel; create/destroy pumps use the same path
- Linux initial load deferred through pending/`_dispatch_pending_load` (GTK /
  `place` layouts); geometry manager required for `ready`
- Viewport/`place` sizing pinned to host `winfo_*` (place size is pre-layout
  fallback only); mypy-safe `place_info` parsing
- Teardown poll armed after `destroy`; off-thread destroy drain/atexit/schedule
  unified; `__del__` logs exceptions instead of swallowing them
- Eval lifetime table + shared terminal-state bookkeeping; poll stop unified
- Cancel deferred initial-load timer when user `load_*` supersedes constructor
  content (fixes multi-WebView `url()` races on macOS)

### Changed

- Lifecycle / load / event-poll flags routed through write helpers; map-axis
  viewable helper shared; ready funnel via `_sync_bounds_and_stacking`
- README Known limitations deduped into Platform notes; sync hooks documented as
  Tk-thread + WebKit-blocking; `WebViewNotReadyError` / `url` docs corrected
- Linux CI: shared runner with Docker, larger shm, split pytest suites, WebKit
  reaped between suites; most content/layout/viewport/lifecycle/IPC tests enabled

### Known limitations

Unchanged posture — see README: Alpha APIs, Linux no wheel, Linux concurrent
eval, macOS IME / import-order / DevTools private APIs, Notebook `ready`≠map.

## [0.0.9] - 2026-07-12

### Added

- `WebViewCreationError` — raised when native WebView creation fails after all retries
- `WebView.take_queue_drop_counts()` — returns `(ipc, page_load, title, drag_drop, eval)` overflow counts since the last call
- macOS: `disable_process_automatic_window_tabbing` runs at `tkwry` import on the main thread
- `examples/macos_double_titlebar_repro.py` — import-order / double titlebar comparison
- Lifecycle state table and initial-load rules documented in the `WebView` class docstring

### Fixed

- Native teardown poll capped when `is_alive` never clears; off-thread `__del__` / GC destroy queued via wakeup pipe with cached toplevel (no Tcl from worker threads)
- Deferred native teardown kept async on Windows and macOS; Python native ref cleared on deferred destroy; partial `__del__` guarded
- Sync-hook and event-queue drains run on the Tk thread only; atexit drain for pending destroys; sync-hook handler wait capped and timed-out hooks canceled
- `<<WebViewReady>>` double-fire prevented; ready callback ordering fixed; `focused=True` deferred until ready on macOS
- macOS focus routing consolidated in `src/macos/focus.rs` (per-window monitors, z-order hit tests, Tcl focus release)
- macOS embed probe hardened — drawable lookup failures raise `WebViewCreationError`; Tk 8.5 offsets validated via native NSView
- macOS window tabbing disabled at import and scoped to the host window; double-titlebar mitigated by avoiding early `NSApplication` init
- macOS `url()` reads from the wry handle directly; NSString UTF-8 conversion validated
- Windows layout-ready scoped to laid-out geometry; `place` viewport and stacking sync fixes
- Linux GtkPump: adaptive backlog scheduling, attach retry, reparent migration, pause on unmap; initial load routed through GTK pump for `place` layouts; GTK teardown and page-load delivery hardened
- Eval poll: no double callback after timeout; native eval wait cleared on timeout; eval pending buffer overflow queues an empty result
- Event queues compacted; drop counts accounted in Rust and Python
- Navigation hooks run on the Tk thread; sync-hook queues capped with unified defaults
- URL validation tightened — invalid HTTP hosts, ports, schemes, IPv6 zones, and UNC paths; local file existence checks; symlink resolution avoided in file URIs
- Linux `ImportError` message when the `_core` extension is missing (build-from-source hint)

### Changed

- `_runtime.py` renamed to `_linux.py`; macOS Rust code consolidated under `src/macos/`
- README: macOS import order, `focused=True` deferral, and non-fatal window-tabbing disable retries
- Regression tests for off-thread destroy (macOS + Windows), sync hooks, GtkPump, macOS focus/layout, queue drops, eval coalescing, and page-load buffering

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

[0.1.5]: https://github.com/mashu3/tkwry/releases/tag/v0.1.5
[0.1.4]: https://github.com/mashu3/tkwry/releases/tag/v0.1.4
[0.1.3]: https://github.com/mashu3/tkwry/releases/tag/v0.1.3
[0.1.2]: https://github.com/mashu3/tkwry/releases/tag/v0.1.2
[0.1.1]: https://github.com/mashu3/tkwry/releases/tag/v0.1.1
[0.1.0]: https://github.com/mashu3/tkwry/releases/tag/v0.1.0
[0.0.9]: https://github.com/mashu3/tkwry/releases/tag/v0.0.9
[0.0.8]: https://github.com/mashu3/tkwry/releases/tag/v0.0.8
[0.0.7]: https://github.com/mashu3/tkwry/releases/tag/v0.0.7
[0.0.6]: https://github.com/mashu3/tkwry/releases/tag/v0.0.6
[0.0.5]: https://github.com/mashu3/tkwry/releases/tag/v0.0.5
[0.0.4]: https://github.com/mashu3/tkwry/releases/tag/v0.0.4
[0.0.3]: https://github.com/mashu3/tkwry/releases/tag/v0.0.3
[0.0.2]: https://github.com/mashu3/tkwry/releases/tag/v0.0.2
[0.0.1]: https://github.com/mashu3/tkwry/releases/tag/v0.0.1
