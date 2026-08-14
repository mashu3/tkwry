# Trust boundaries

`window.ipc` / `window.tkwry.call` run with **desktop-app privileges**. A
page that can call them can drive whatever you `expose` or handle over IPC —
including after a redirect or XSS in a third-party script.

Landing examples live in [Usage](usage.md) and the short checklist in
[README.md](../README.md). This page is the contract.

## Constructor patterns

```python
# Local UI with RPC — bridge defaults to tkwry://
web = WebView(frame, app="./web")

# Arbitrary websites — no IPC/RPC, ephemeral storage
web = WebView(frame, url="https://example.com", untrusted=True)

# One trusted origin, or a path prefix (not /application)
web = WebView(
    frame,
    url="https://trusted.example/app",
    bridge_origins=["https://trusted.example/app"],
)
```

## Recipes

**Local app (trusted).** Serve files and expose Python:

```python
web = WebView(frame, app="./web")

@web.expose
def save(payload: dict) -> dict:
    ...
```

**Arbitrary websites (viewer).** No bridge, ephemeral profile, downloads
denied unless you opt in:

```python
web = WebView(frame, url="https://example.com", untrusted=True)
# optional: download_allow=["https://cdn.example.com"] and/or on_download
```

**Mixed UI + external site.** Two WebViews, **two sessions**. Never pass
the app profile into the viewer:

```python
from tkwry import WebSession, WebView

app_session = WebSession(data_directory="~/.myapp/webview")
ui = WebView(frame_ui, app="./web", session=app_session)
viewer = WebView(frame_web, url="https://example.com", untrusted=True)
# viewer owns an ephemeral session — do not pass app_session
```

## Session isolation

A persistent ``WebSession`` / ``data_directory`` is a shared
browser profile (cookies, cache, ``localStorage`` where the engine allows
it). Isolation rules:

- One profile per trust domain. Local ``app=`` UI and an untrusted site
  must **not** share a persistent session.
- WebViews that share a **non-ephemeral** session must use the **same**
  ``app=`` root (``ValueError`` otherwise). Linux can register
  ``tkwry://`` only once per WebContext; tkwry enforces that everywhere.
- Unrelated local apps → separate ``WebSession`` instances.
- ``untrusted=True`` creates an ephemeral session when you omit
  ``session=``. Keep that default.

See [Usage — Shared session](usage.md#shared-session-websession).

## What errors look like

| Situation | What you see |
|-----------|----------------|
| RPC from a page outside ``bridge_origins`` | JS Promise rejects ``RpcOriginError`` (URL + hint to extend ``bridge_origins``) |
| IPC from a disallowed origin | Dropped — no Python handler, no error |
| ``emit`` while the current page is outside ``bridge_origins`` | ``ValueError`` |
| Download URL outside ``download_allow`` (or untrusted with no permit) | Download cancelled; **no** Python exception |
| ``download_allow="https://cdn.example.com"`` (a string) | ``TypeError`` — pass a sequence |
| ``download_allow=["*"]`` | ``ValueError`` — ``*`` is not a download allowlist |
| ``tkwry://`` request with a foreign ``Origin`` / ``Referer`` | HTTP **403** |

## Defaults

- **Bridge origins** — IPC/RPC are accepted only from the initial content
  origin (`html=` → `about:blank`; `app=` → `tkwry://` /
  `https://tkwry.localhost`; `url=` → that site). Foreign pages still see
  `window.ipc` (engine injection) but messages are dropped / RPC rejects with
  `RpcOriginError`. Use `bridge_origins=["https://trusted.example"]` (whole
  origin) or a **path prefix**
  (`bridge_origins=["https://trusted.example/app"]` — `/app` and
  `/app/...`, not `/application`). `bridge_allow=lambda url: ...` can
  further restrict by the full page URL (navigation state).
- **`bridge_origins="*"`** — every page; emits
  `TkwrySecurityWarning`. `expose()` then requires
  `allow_any_origin=True`. `devtools=True` with `"*"` warns again.
  Filter with `PYTHONWARNINGS=ignore::tkwry.TkwrySecurityWarning` only if
  you accept the risk.
- **`app=` navigation** — in-page navigation stays on `tkwry://` (plus
  optional `navigation_allow` origins / path prefixes). New windows are
  denied. Pass `open_external=True` to open off-list http(s) in the system
  browser — **never** create a WebView from `on_new_window` (WKWebView
  deadlocks). Custom `on_navigation` / `on_new_window` replace this policy
  for that direction; use `open_in_browser(url)` from a custom hook.
- **`untrusted=True`** — viewer mode: no IPC handler, no `expose` /
  `emit`, ephemeral session, http(s) only, no `tkwry://` / `file:`, new
  windows denied, **downloads denied**. `download_allow` and/or
  `on_download` can permit specific URLs (handler may set an absolute dest
  or return `False` to cancel). Cannot be combined with `bridge_origins` /
  `bridge_allow`. Use this for arbitrary websites.
- **Downloads (trusted)** — wry default is allow-all. `download_allow`
  restricts by origin / path prefix; `on_download(url, dest)` runs on the Tk
  thread (WebKit waits) and may return `True`, `False`/`None`, or an **absolute**
  save path (relative dests are denied). Use `unique_download_path(dest)` to
  pick a free name (`report.pdf` → `report (1).pdf`); tkwry does not overwrite.
  `on_download_complete(url, dest, success)` is notify-only.
  Completions also set `last_download` and generate
  `<<WebViewDownloadComplete>>` or `<<WebViewDownloadFailed>>`.
- **Dangerous schemes** — `javascript:` / `blob:` / `vbscript:` /
  `mailto:` are denied at the native navigation hook even without Python
  `on_navigation`. `data:` is not blocked there (WebView2 `html=` /
  `NavigateToString`); `app=` still rejects it.
- **`tkwry://` Origin / Referer** — custom-protocol requests with a non-app
  `Origin` or `Referer` return 403 (top-level loads with no Origin still work).

Do **not** enable RPC/IPC on a WebView that shows untrusted sites. Do **not**
share a persistent `WebSession` / `data_directory` between a local app and
an external site. Prefer vendored JS (`app=`) over CDN scripts in pages that
have a bridge — XSS in a CDN script is the page origin.

[`examples/browser_demo.py`](../examples/browser_demo.py) sets
`bridge_origins="*"` on purpose (link interception only; expect the
security warning). Copy that only if every page is trusted, and do not
`expose()` desktop APIs without `allow_any_origin=True`.

## tkwry serving

Constructor `app=` fixes the filesystem root at create time. Later
`load_url("tkwry://localhost/other.html")` can navigate within that root
(Windows WebView2 rewrites this to `https://tkwry.localhost/...` internally).

The `tkwry://` handler percent-decodes each path segment (so `%2e%2e`
cannot bypass `..`), rejects NUL / invalid UTF-8 / Windows drive and UNC
shapes, then opens the file under the app root and checks the opened file's
identity against the canonical path (symlinks, Windows junctions, and
reparse points that escape return 403). Internal links that stay under the
root are allowed.

Successful responses include a default Content-Security-Policy (`'self'` +
inline script/style; no CDN / `eval` / framing). Pass `csp=False` to omit it,
or a policy string / `DEFAULT_CSP` to replace it. `coop=True` /
`corp=True` add `Cross-Origin-Opener-Policy` /
`Cross-Origin-Resource-Policy: same-origin` (opt-in).

See [Usage — Local app assets](usage.md#local-app-assets-app--tkwry)
for SPA fallback, cache headers, and `watch_app()`.

## Related

- [Usage](usage.md) — `app=`, eval, layout, navigation, API table
- [IPC / RPC / emit](rpc.md) — what the bridge can actually do
- [Platform notes](platforms.md) — engine-specific `url()` / session caveats
