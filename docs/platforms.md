# Platform notes

Pre-built **abi3** wheels ship for **Windows** and **macOS**. **Linux** is
source-only (**best-effort** by design). Install steps live in
[README.md](../README.md#-installation).

| OS | Arch | Parent handle | Engine |
|----|------|---------------|--------|
| **Windows** | x86_64, arm64 | `Frame.winfo_id()` → HWND | WebView2 |
| **macOS** | arm64, x86_64 | Toplevel content `NSView` | WKWebView |
| **Linux** | — | `winfo_id()` → X11 window ID | WebKitGTK |

## Windows

[WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
must be installed (common on Windows 10/11). Without it, native create is
abandoned (`creation_failed` / `<<WebViewCreateFailed>>` / `when_failed`;
gated APIs raise `WebViewCreationError` with an install link). The constructor
does **not** raise. There is **no** fallback engine.

**DPI:** `set_bounds` uses **physical** pixels on Windows. After process DPI
awareness (e.g. `tkface.win.enable_dpi_awareness()` before `tk.Tk()`), Tk
`winfo_*` already reports physical sizes — passing them as wry `Logical`
would double-scale. Prefer awareness + design-pixel→physical sizing in the
host app; do not monkeypatch tkwry bounds from app code.

**DevTools:** wry/WebView2 reports `is_devtools_open()` as `False` and
`close_devtools()` is a no-op; `open_devtools()` still opens the inspector.

## Linux

**By design in v0.1.x:** no PyPI wheel; install from source (sdist / git).
Support is **best-effort** — not a release blocker for Windows/macOS wheels.
CI runs the integration suite under **Xvfb**; real-desktop / Wayland timing
may still differ. GTK is pumped on a Tk timer automatically after install.

For `place` layouts, pass explicit `width`/`height` so host `winfo_*`
settles; native size follows those `winfo_*` values (see
[README — Layout / resize](../README.md#layout--resize)).

**Concurrent eval:** calling `eval_js_with_callback` on **multiple**
WebViews at the same time can stall under WebKitGTK (especially headless /
Xvfb). Prefer one eval at a time — wait for each callback (or error) before
starting the next — when you have several views.

**Shared `app=`:** Linux can register `tkwry://` only once per WebContext.
WebViews that share a non-ephemeral `WebSession` must use the same `app=`
root (`ValueError` on all platforms). See [Trust boundaries](trust.md).

## macOS embedding

Tk child `Frame`s usually **do not** get their own `NSView` (Tk Aqua). tkwry
attaches to the **toplevel content view**, positions with `set_bounds` on
`<Configure>`, and hides with `set_visible(False)` on `<Unmap>` (e.g.
another Notebook tab). Per-frame native views would need upstream Tk changes.

**Keyboard focus:** clicks are hit-tested at the `NSEvent` layer; Python
drains focus signals on the Tk main thread. Use `web.focus()` /
`web.focus_parent()` for explicit control
([`examples/browser_demo.py`](../examples/browser_demo.py)). On
macOS/Windows, `focused=True` waits for `<<WebViewReady>>`, then calls
`focus()` (create-time focus breaks child WKWebView / WebView2). Call
`focus()` yourself after later layout changes.

**IME:** composition stays with the current first responder. Switching Tk ↔
WebView mid-composition (or fighting the system candidate window) can cancel
or mis-deliver input vs Safari. **Not** a v0.1 goal — finish composition
before changing focus, or keep IME editing in one surface.

**Import order / double titlebar:** import `tkwry` **before** anything that
starts `AppKit` / `NSApplication`. On import, tkwry disables process-level
automatic window tabbing on the main thread. If AppKit starts first, macOS
may show a **double titlebar** strip. If per-window tabbing disable during
create fails, tkwry logs and retries asynchronously (non-fatal).

**`url()`:** may be `None` for inline HTML (`html=` / `load_html`) or when
WKWebView has no document `NSURL`. After `load_url`, it becomes the concrete
URI.

**DevTools:** create with `devtools=True`, then `open_devtools()` (flag alone
does not open; `open_devtools()` without the flag is a no-op on macOS). Uses
private APIs — avoid in Mac App Store builds.

**Notebook / tabs:** unmapped tabs hide the native view (`set_visible(False)`)
and show again on `<Map>` — required because frames share the toplevel
`NSView`. `ready` is layout-based (can stay `True` while hidden); prefer
visible-tab work after the tab is selected. No extra app code for tabs/panes —
[`examples/multi_demo.py`](../examples/multi_demo.py).

Lifecycle / IPC / page-load handlers run on the **Tk main thread**. RPC may
use a worker (`thread=True`). `on_navigation` / `on_new_window` still make
WebKit wait for a return value — see
[README — Navigation / lifecycle callbacks](../README.md#navigation--lifecycle-callbacks).

## Related

- [Trust boundaries](trust.md)
- [IPC / RPC / emit](rpc.md)
- [README — Known limitations](../README.md#-known-limitations)
