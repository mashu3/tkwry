# Packaging notes

How to ship a **tkwry** app as a standalone desktop bundle. These are
**maintainer notes** for v0.1.x — not a confirmed matrix (PyInstaller / Nuitka
smoke is a **0.2.0** Beta gate). Treat every recipe as **best-effort** until
your target OS is verified.

| Topic | Doc |
|-------|-----|
| Install / wheels | [README — Installation](../README.md#-installation) |
| Platform runtimes | [Platform notes](platforms.md) |
| Usage / lifecycle | [Usage](usage.md) |

## What you are bundling

tkwry is a **Rust + Python** extension (`tkwry._core`) linked against the
system WebView stack ([wry](https://github.com/tauri-apps/wry)):

| OS | Your bundle must include | Runtime the user still needs |
|----|--------------------------|------------------------------|
| **Windows** | `tkwry` wheel / `.pyd` + dependencies | [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (Evergreen or fixed version) |
| **macOS** | `tkwry` wheel / `.so` + dependencies | System WKWebView (OS-provided) |
| **Linux** | Source-built extension in the bundle | WebKitGTK + GTK 3 + X11/Wayland display |

Pre-built PyPI wheels exist for **Windows and macOS only**. Linux bundles
require building tkwry from source on a compatible distro (or inside a
container that matches your users' GLib/GTK stack).

## General Python packaging tips

1. **Build on the target OS** (or CI matrix row) you ship. abi3 wheels help
   across Python 3.10+ on the same OS/arch, not across OSes.
2. **Include the native module.** Freezers must collect `tkwry._core` (and
   usually `tkwry` package data). If import fails at runtime, check hidden
   imports / `--collect-submodules tkwry`.
3. **Ship Tkinter.** Most CPython builds include it; some minimal embeddable
   builds do not — verify `import tkinter` in the frozen app.
4. **Do not strip platform runtimes.** WebView2 (Windows) is a separate
   installer unless you ship the Fixed Version runtime beside your app
   (Microsoft redistribution rules apply).
5. **Test create → load → destroy** on a clean VM after freezing. Lifecycle
   and wakeup-pipe teardown bugs show up only after repeated Toplevel
   open/close cycles.

## PyInstaller (sketch)

Not CI-verified in 0.1.x. Typical starting point:

```bash
pip install pyinstaller tkwry
pyinstaller --onefile --windowed \
  --collect-submodules tkwry \
  --name myapp \
  main.py
```

Adjust for your entry script and data files (`web/` trees for `app=`,
icons, etc.). Use `--add-data` / `.spec` `datas` for static assets.

**Windows:** document WebView2 installation for end users, or bundle Fixed
Version WebView2 per Microsoft guidance. Missing WebView2 →
`creation_failed` / `<<WebViewCreateFailed>>` (constructor does not raise).
Probe + Evergreen install recipe (no silent download):
[Platform notes — WebView2](platforms.md#webview2-runtime-probe-and-install).
Tk main thread / COM: [Platform notes — Tk thread](platforms.md#tk-thread-com-apartment).

**macOS:** sign and notarize the `.app`; WKWebView is system-provided.
Import `tkwry` before other AppKit startup ([Platform notes — macOS](platforms.md#macos-embedding)).

**Linux:** prefer `--onedir` over `--onefile` while debugging GLib/GTK
load paths; WebKitGTK `.so` dependencies are easy to miss in one-file mode.

## Nuitka (sketch)

Not CI-verified in 0.1.x. You are compiling Python **and** still shipping
the prebuilt `tkwry._core` extension unless you rebuild from source inside
Nuitka's build tree:

```bash
pip install nuitka tkwry
python -m nuitka --standalone --enable-plugin=tk-inter main.py
```

Expect to iterate on `--include-module=tkwry` / data-dir flags. Same
WebView2 / WebKitGTK runtime requirements as above.

## Local `app=` assets

For `app=mydir/` layouts, include the entire `web/` directory in `datas`.
`tkwry://` serves from disk at runtime — no localhost HTTP server required.

## What 0.2.0 will add

Beta gate **B9** targets automated PyInstaller / Nuitka smoke on Windows and
macOS CI (minimal create/load/destroy). Until then, report packaging gaps as
GitHub issues with OS, freezer version, and traceback.

## Related

- [Release provenance](provenance.md) — SHA-256 checksums and build attestations
- [Usage — Cleanup](usage.md#cleanup)
- [Usage — Observability](usage.md#observability)
- [Platform notes — WebView2](platforms.md#webview2-runtime-probe-and-install)
- [README — Known limitations](../README.md#-known-limitations)
