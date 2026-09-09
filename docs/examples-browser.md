# Mini-browser example (`tkwry_browser.py`)

The flagship demo: a small multi-WebView browser built only with Tk + tkwry.
Start here if you want to see embedding, `app=` UI, RPC, sessions, and trust
boundaries working together in one app.

| macOS · dark | Windows · light |
|:---:|:---:|
| ![tkwry browser on macOS (dark)](images/browser-macos-dark.png) | ![tkwry browser on Windows (light)](images/browser-windows-light.png) |

```bash
python examples/tkwry_browser.py
python examples/tkwry_browser.py --private   # ephemeral content session
```

Requires **tkwry >= 0.1.9** (rebuild with `pip install -e .` if import or
version checks fail — a stale `_core.pyd` / `.so` is a common cause).
Unit coverage: ``tests/unit/test_tkwry_browser.py`` (required on the 0.1.9
cut).

**Windows DPI (optional):** install [tkface](https://pypi.org/project/tkface/)
and the demo calls ``tkface.win.enable_dpi_awareness()`` before ``tk.Tk()``,
then scales window / chrome sizes with ``design_to_physical``. Popup menus
that anchor on WebView CSS coordinates also scale those offsets before
``tk_popup``. Do **not** use ``tkface.win.dpi(root)`` with tkwry embeds (see
[Platform notes — DPI](platforms.md)). Without tkface the demo still runs
(unaware / blurrier on high-DPI displays).

The toolbar WebView suppresses engine context menus
(``default_context_menus=False`` on Windows; ``preventDefault`` on
``contextmenu`` on all platforms). Content tabs keep the custom Tk page
menu.

Single file: HTML/CSS/JS for the toolbar strip, side pane, and Settings are
embedded and written to a temp tree at startup, then loaded with `app=`
(keeps `tkwry://` origins — not `html=` for those UI surfaces).

## Layout

| Surface | Role | Session |
|---------|------|---------|
| **Toolbar** | Tabs, URL bar, back/forward/reload/home, profile + app menus | Own UI `WebSession` + RPC |
| **Side pane** | Bookmarks / history tree | Own UI `WebSession` + RPC |
| **Settings tab** | Home, search, downloads, profiles, cookies | Own UI `WebSession` + RPC |
| **Content tabs** | Real pages (and the New Tab start page) | Shared **content** `WebSession` |

UI `app=` roots must not share one `WebSession` (Linux registers `tkwry://`
once per context). Content stays on a **separate** session so browsing data
does not mix with toolbar / Settings cookies.

Tk owns the window menu bar on macOS/Linux (File / View / Help) and pop-up
context menus. Windows skips the native menubar and uses the toolbar hamburger
(in-app menu via RPC) instead. **Help…** shows the tkwry version and can open
the GitHub repository.

## What it exercises

- **Child embedding** — toolbar, side, and content share one Tk layout
  (`PanedWindow`, pack); bounds follow resize / tab switches
- **Local UI** — `app=` + CSP; Settings is fully local
- **RPC / emit** — toolbar and side call Python; Python pushes `state` / `ntp`
- **Trust split** — content uses `bridge_origins="*"` for link interception
  and a small clipboard bridge only (expect the security warning; see
  [Trust](trust.md))
- **New Tab** — `html=` start page (brand, search, bookmark shortcuts);
  Home `about:blank` selects that page; reload re-loads HTML (native
  `reload()` would clear `html=` documents)
- **Profiles** — named dirs under `~/.tkwry/`; switch / create / delete from
  Settings; `--private` uses an ephemeral content session. Default download
  folder is the OS **Downloads** directory (not under the profile tree)

## Shortcuts (demo)

Cmd/Ctrl bindings are installed with `bind_class` ahead of the macOS web
key-guard, plus a JS bridge so keys still reach Python while a WKWebView is
focused. Examples: new tab, reopen closed tab, tab cycle, zoom, focus the
URL bar, Settings.

Clipboard copy/cut/paste goes through Tk (`clipboard_get` / `clipboard_set`)
because WKWebView pasteboard access is unreliable for this demo.

## Packaging

UI assets are embedded in the script (no separate `web/` folder). From a
clone with tkwry installed (`pip install -e .`).

Recipes below: Windows **one-file**, macOS **windowed onedir** ``.app``
(PyInstaller does not support windowed+onefile on macOS). Install **tkface**
as well so Windows DPI awareness is bundled (``--collect-submodules tkface``).
The manual **Freeze** workflow smokes **onedir then onefile** serially on
both OS (no GUI; not on push/tags): onedir asserts ``tkwry._core``; onefile
checks the build (macOS onefile without ``--windowed``). Nuitka recipes are
not covered by Freeze — see [Packaging notes](packaging.md).

### PyInstaller

```bash
pip install pyinstaller tkwry tkface
```

**Windows** — one-file `.exe`:

```bat
pyinstaller --noconsole --onefile --collect-submodules tkwry --collect-submodules tkface --name tkwry-browser examples/tkwry_browser.py
```

**macOS** — onedir `.app`:

```bash
pyinstaller --windowed --onedir --collect-submodules tkwry --collect-submodules tkface --name tkwry-browser examples/tkwry_browser.py
```

### Nuitka

**Windows** — one-file `.exe`:

```bat
python -m nuitka --standalone --onefile --windows-console-mode=disable --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --output-filename=tkwry-browser.exe examples/tkwry_browser.py
```

**macOS** — `.app` bundle:

```bash
python -m nuitka --standalone --macos-create-app-bundle --static-libpython=no --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --macos-app-name=tkwry-browser --output-filename=tkwry-browser examples/tkwry_browser.py
```

Expect to iterate on include/data flags. Same WebView2 / WKWebView runtime
rules as in [Packaging notes](packaging.md).

Profile data still lives under `~/.tkwry/` at runtime (not inside the bundle).

## Not a product browser

This is an **example**, not a supported browser product. Prefer it as a
recipe for session split, toolbar RPC, and content policy — copy patterns,
not the whole file, into real apps.

## Related

- [Usage](usage.md) — `WebView`, `WebSession`, layout, navigation
- [Trust boundaries](trust.md) — why content uses `bridge_origins="*"` carefully
- [IPC / RPC / emit](rpc.md)
- [Platform notes](platforms.md) — macOS focus / DevTools / clip containers
- [Packaging notes](packaging.md) — general freeze recipes and troubleshooting
- [README — Examples](../README.md#-examples)
