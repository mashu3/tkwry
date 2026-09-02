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
gated APIs raise `WebViewCreationError`). The constructor does **not**
raise. There is **no** fallback engine. The typed message is
`WEBVIEW2_MISSING_MESSAGE` in `tkwry/_win32.py` (also `creation_error`):

> Microsoft Edge WebView2 Runtime is not installed. tkwry requires
> WebView2 on Windows — there is no fallback engine. Install from
> https://developer.microsoft.com/en-us/microsoft-edge/webview2/

### WebView2 Runtime (probe and install)

tkwry **never downloads** the Runtime. Install is the user's (or your
installer UI's) job — no silent bootstrap from Python.

**At create** tkwry already probes Evergreen via EdgeUpdate registry
`pv`. Missing → `creation_failed` / `<<WebViewCreateFailed>>` /
`when_failed` / `on_creation_failed=` with
`WEBVIEW2_MISSING_MESSAGE` (constructor still does **not** raise).
Unusual layouts (Fixed Version beside the exe, broken `pv`) can miss
the registry check; native create then fails with the same typed error.

After a failed create, **do not reuse** that `WebView`. Once the user
has installed Evergreen, construct a **new** instance.

```python
from tkwry import WebView

def on_failed(err):
    # err message includes the Microsoft download URL.
    # Show it in your UI; do not fetch the installer yourself.
    print(err)

web = WebView(frame, html="<p>hello</p>", on_creation_failed=on_failed)
```

**User install (Evergreen):** open
[WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/),
run Bootstrapper or Standalone (UAC). Then retry with a new `WebView`.

**Frozen apps:** ship a “needs WebView2” note, or bundle **Fixed
Version** per Microsoft redistribution rules — see
[Packaging notes](packaging.md). Do not call a private `have_webview2`
helper; the create-failed path is the supported probe.

### Tk thread (COM apartment)

All `WebView` APIs run on the **Tk main thread** (the thread that
created `tk.Tk()` and runs `mainloop`). WebView2 uses that same UI
thread. tkwry does **not** call `CoInitialize` / `CoInitializeEx`.

A normal `tk.Tk()` app needs **no** extra STA flags
(`sys.coinit_flags`, `pythoncom.CoInitialize`, …). Add those only if
you already mix COM on that thread and hit a real HRESULT — not as
default boilerplate.

Do **not** create or drive a `WebView` from a worker. Do **not**
`CoInitializeEx` **MTA** on the Tk thread (WebView2 create can fail).

**DPI:** `set_bounds` uses **physical** pixels on Windows. After process DPI
awareness (e.g. `tkface.win.enable_dpi_awareness()` before `tk.Tk()`), Tk
`winfo_*` already reports physical sizes — passing them as wry `Logical`
would double-scale. Prefer awareness + design-pixel→physical sizing in the
host app; do not monkeypatch tkwry bounds from app code.

**DevTools:** see [DevTools](#devtools) (WebView2 open works;
`is_devtools_open` / `close_devtools` do not).

## Linux

**By design in v0.1.x:** no PyPI wheel; install from source (sdist / git).
Support is **best-effort** — not a release blocker for Windows/macOS wheels.
CI runs the integration suite under **Xvfb**; real-desktop / Wayland timing
may still differ. GTK is pumped on a Tk timer automatically after install.

For `place` layouts, pass explicit `width`/`height` so host `winfo_*`
settles; native size follows those `winfo_*` values (see
[Usage — Layout / resize](usage.md#layout--resize)).

**Concurrent eval:** calling `eval_js_with_callback` on **multiple**
WebViews at the same time can stall under WebKitGTK (especially headless /
Xvfb). Prefer one eval at a time — wait for each callback (or error) before
starting the next — when you have several views.

**Shared `app=`:** Linux can register `tkwry://` only once per WebContext.
WebViews that share a non-ephemeral `WebSession` must use the same `app=`
root (`ValueError` on all platforms). See [Trust boundaries](trust.md).

**DevTools:** open / close / `is_devtools_open` work under WebKitGTK when
the runtime supports the inspector (headless / Xvfb may lack a UI).

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

**DevTools:** see [DevTools](#devtools) — need `devtools=True` then
`open_devtools()` (private APIs; avoid Mac App Store builds).

**Notebook / tabs:** unmapped tabs hide the native view (`set_visible(False)`)
and show again on `<Map>` — required because frames share the toplevel
`NSView`. `ready` is layout-based (can stay `True` while hidden); prefer
visible-tab work after the tab is selected. `lift` of still-mapped Frames
does not hide the other native view. No extra app code for tabs/panes —
[`examples/multi_demo.py`](../examples/multi_demo.py).

Lifecycle / IPC / page-load handlers run on the **Tk main thread**. RPC may
use a worker (`thread=True`). `on_navigation` / `on_new_window` /
create-time `permission_handler` still make WebKit wait for a return value —
see [Usage — Navigation / lifecycle callbacks](usage.md#navigation--lifecycle-callbacks).

## DevTools

Public surface (all platforms):

- create-time ``devtools=True`` (wry ``with_devtools``)
- ``web.open_devtools()`` / ``web.close_devtools()`` / ``web.is_devtools_open()``

Ready / Tk-thread only; destroy raises ``WebViewDestroyedError``.

| Platform | Enable | Open | Close | ``is_devtools_open`` |
|----------|--------|------|-------|----------------------|
| **Windows** | ``devtools=True`` | opens inspector | **no-op** (wry/WebView2) | always ``False`` |
| **macOS** | **required** — without it ``open_devtools`` is a no-op; uses private APIs (avoid Mac App Store) | opens | closes | accurate when supported |
| **Linux** | recommended | opens when inspector UI exists | closes | accurate when supported |

Do not invent a fourth API or poll ``is_devtools_open`` on Windows expecting
a true positive. Integration smoke:
``tests/integration/test_create_options.py::test_devtools_open_close_roundtrip``.

## Downloads

Start / complete wrap wry's download handlers. Cancel **before** the
transfer starts with `on_download` returning `False` / `None` (or deny via
`download_allow` / `untrusted`). There is **no** mid-flight abort,
pause/resume, or progress % in wry **0.56.1** — tkwry does **not** invent
`cancel_download()` / progress callbacks. `in_flight_downloads` is
observational (starts that passed the policy hook until complete). See
[Usage](usage.md#navigation--lifecycle-callbacks) and
[Trust — Downloads](trust.md).

## Print

`web.print()` opens the **system** print dialog (wry `WebView::print`).
There is **no** `print_to_pdf`, no headless / silent print, and no
success / fail / cancel callback — the call is fire-and-forget. Do not
add fake kwargs or a shim that “prints” via `window.print()` / a PDF
library and calls it a Capability.

Upstream: wry still has no cross-platform PDF API (open ask
[wry#707](https://github.com/tauri-apps/wry/issues/707); Windows-only
`print_to` was declined in
[wry#1167](https://github.com/tauri-apps/wry/pull/1167)). When wry ships
one, tkwry will wrap it in the next open cut.

**macOS only:** `web.print_with_options(top=…, right=…, bottom=…, left=…)`
wraps wry `WebViewExtMacOS::print_with_options` (margin points). Still
fire-and-forget. On Windows / Linux it raises `OSError` — use `print()`.

## Zoom (page, not window)

`web.set_zoom(scale)` wraps wry `WebView::zoom` (`1.0` = 100%).
`web.reset_zoom()` is `set_zoom(1.0)`. Tk-thread / ready; destroy raises
`WebViewDestroyedError`. tkwry does **not** clamp — engine ranges differ
(WebView2 typically about `0.25`–`5.0`; WKWebView `pageZoom` on macOS 11+;
WebKitGTK `zoom-level`). This is **page** content zoom. Tk window
iconify / zoom / geometry stay on the host **Toplevel** (below).

`WebView(..., hotkeys_zoom=True)` maps to wry `with_hotkeys_zoom` (default
**`False`**, same as wry). On **Windows** this enables Ctrl+/- / Ctrl+wheel
and pinch page zoom in WebView2. It does **not** change `set_zoom` /
`reset_zoom`. macOS / Linux: wry ignores the flag (constructor value is
still stored). WebView2 Runtime before 91.0.865.0 cannot disable pinch
when the flag is `False`.

## Back / forward gestures (swipe)

`WebView(..., back_forward_gestures=True)` maps to wry
`with_back_forward_navigation_gestures` (default **`False`**, same as wry).
Horizontal trackpad / touch swipe then drives history navigation.

This is **not** `go_back` / `go_forward` (those stay explicit API).

**Windows:** WebView2 `IsSwipeNavigationEnabled`. Setting `False` does
nothing on Runtime older than 92.0.902.0 (swipe stays on). **macOS:**
WKWebView `allowsBackForwardNavigationGestures`. **Linux:** WebKitGTK
`enable-back-forward-navigation-gestures`.

## Default context menus

`WebView(..., default_context_menus=False)` maps to wry
`with_default_context_menus` (default **`True`**, same as WebView2).
On **Windows** this hides the page context menu (Inspect, Back, Reload,
Save as, …). Tk / Toplevel chrome menus are **not** affected.

`untrusted=True` does **not** flip this. Pair `False` with viewer mode
when you want a locked-down page. `devtools=True` / `open_devtools()`
still work — they do not depend on the context menu.

macOS / Linux: wry has no equivalent builder flag (constructor value is
still stored). WKWebView / WebKitGTK keep their engine default menus.

## HTTPS scheme (Windows `app=`)

`WebView(..., app=..., https_scheme=True)` maps to wry
`with_https_scheme` (tkwry default **`True`** — already the Windows
`app=` origin since 0.1.2). WebView2 then navigates
`https://tkwry.localhost/...` (a **secure context**: Service Worker,
`crypto.subtle`).

`https_scheme=False` uses wry's own default: `http://tkwry.localhost/...`.
That matches custom-scheme mixed-content on macOS / Linux more closely,
but is **not** a secure context.

macOS / Linux ignore the flag and stay `tkwry://localhost`. Changing the
flag on an existing Windows profile moves the origin (cookies /
`localStorage` do not follow).

## Proxy (HTTP CONNECT / SOCKSv5)

`WebView(..., proxy={"http": "127.0.0.1:8080"})` or
`proxy={"socks5": "127.0.0.1:1080"}` maps to wry `with_proxy_config`
(create-only; default `None`). Exactly **one** of `http` / `socks5`.
Values may be `host:port`, `[ipv6]:port`, or `scheme://host:port`.

wry exposes **host + port only** — credentials in the value raise
`ValueError` and are **never** echoed in the message (same honesty bar as
cookie / header values).

**Windows:** WebView2 `--proxy-server=…`. **Linux:** WebKitGTK custom
network proxy. **macOS:** requires **14.0+** and wry's `mac-proxy`
feature (enabled in tkwry). Older macOS: create may fail if `proxy=` is
set — omit the option on older hosts.

This does **not** change system proxy env vars (`HTTP_PROXY`, …) for
non-WebView Python code.

## Clipboard (Web API opt-in)

`WebView(..., clipboard=True)` → wry `with_clipboard(true)`. Default is
**`False`** (opt-in). On **Windows** and **Linux** this enables the page
Web Clipboard API / related accelerators. On **macOS** the WebView clipboard
path is **always on** — the flag still records the constructor value but
does not toggle a platform switch. This is **not** a Tk↔Web paste bridge
(that stays later). Useful for Monaco / in-page editors.

## JavaScript (create-time)

`WebView(..., javascript_enabled=False)` maps to wry
`with_javascript_disabled`. Default is **`True`**. This is a break-glass
for untrusted pages, not the `untrusted=True` viewer preset (that flag
does not turn JS off). Create-only; `eval_js` / init scripts need JS on.

## Autoplay (create-time)

`WebView(..., autoplay=True)` maps to wry `with_autoplay` (default
**`True`**, same as wry 0.56). Media may start without a user gesture.
`autoplay=False` keeps the engine's gesture requirement.

This is **not** `permission_handler` / `PermissionKind.Autoplay` (a
sync-hook for the page's autoplay *permission* prompt). The constructor
flag is the engine policy.

**Windows:** `True` adds WebView2
`--autoplay-policy=no-user-gesture-required`. **macOS:** `True` sets
`mediaTypesRequiringUserActionForPlayback` to none. **Linux:** `True`
sets WebKitGTK `AutoplayPolicy::Allow`.

## Window chrome (Tk, not the WebView)

Title, icon, geometry, min/max size, fullscreen, `-topmost`, and
iconify/zoom belong on the host **Toplevel**. Use
`configure_window(...)` for the common set:

```python
from tkwry import configure_window

configure_window(
    root,
    title="My App",
    geometry="960x640",
    minsize=(720, 480),
    topmost=False,
    # icon="assets/app.png",  # PNG/GIF/PPM; .ico on Windows
)
```

Omitted kwargs are left unchanged. The WebView only follows its **Frame**
via `sync_bounds()` — there is no `web.set_size` / `web.set_title` /
`web.set_icon`. See [Usage — Layout / resize](usage.md#layout--resize).

ttk themes, host dark/light chrome, extra DPI helpers, custom titlebar
/ Acrylic / menus / tray belong in **tkface** (MIT sibling — apps or
optional tkwry glue may `import tkface`; do not reimplement in
`configure_window`). Windows DPI: `tkface.win.enable_dpi_awareness()`
before `tk.Tk()` (above).

## Screenshot

wry **0.56.1** has no `WebView` screenshot / capture method
([PR #1674](https://github.com/tauri-apps/wry/pull/1674) is still open).
tkwry does **not** add `screenshot()` / `capture()`, and does not ship a
JS visible-region helper. When wry exposes capture, wrap it the same way as
`print()`.

## Find in page

wry **0.56.1** has no find-in-page API
([wry#585](https://github.com/tauri-apps/wry/issues/585);
[PR #593](https://github.com/tauri-apps/wry/pull/593) did not ship).
tkwry does **not** add `find` / `find_next` / `find_previous` /
`clear_find`, and does not wrap `window.find` as a Capability.

**Windows:** WebView2 may show a **native** find UI on Ctrl+F when browser
accelerator keys are enabled (engine chrome — not a tkwry API).
**macOS / Linux:** no portable wry surface yet.

When wry ships a cross-platform find API, wrap it in the next open cut.

## Related

- [Usage](usage.md)
- [Trust boundaries](trust.md)
- [IPC / RPC / emit](rpc.md)
- [README — Known limitations](../README.md#-known-limitations)
