# Packaging notes

How to ship a **tkwry** app as a standalone desktop bundle (Windows `.exe` /
macOS `.app`). These are **maintainer notes** for v0.1.x — not a confirmed
matrix (PyInstaller / Nuitka smoke is a **0.2.0** Beta gate). Treat every
recipe as **best-effort** until your target OS is verified.

Recipes here cover **Windows and macOS** (PyPI wheels). Linux is source-only
and out of scope for these freeze samples — see [Platform notes](platforms.md).

Sample commands are **one line** so they paste cleanly into cmd, PowerShell,
or bash (no `^` / `\` continuations).


| Topic             | Doc                                                 |
| ----------------- | --------------------------------------------------- |
| Install / wheels  | [README — Installation](../README.md#-installation) |
| Platform runtimes | [Platform notes](platforms.md)                      |
| Usage / lifecycle | [Usage](usage.md)                                   |
| Flagship demo     | [Mini-browser example](examples-browser.md)         |




## What you are bundling

tkwry is a **Rust + Python** extension (`tkwry._core`) linked against the
system WebView stack ([wry](https://github.com/tauri-apps/wry)):


| OS          | Your bundle must include              | Runtime the user still needs                                                                                    |
| ----------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **Windows** | `tkwry` wheel / `.pyd` + dependencies | [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (Evergreen or fixed version) |
| **macOS**   | `tkwry` wheel / `.so` + dependencies  | System WKWebView (OS-provided)                                                                                  |




## General tips

1. **Build on the target OS** (or CI matrix row) you ship. abi3 wheels help
  across Python 3.10+ on the same OS/arch, not across OSes.
2. **Include the native module.** Freezers must collect `tkwry._core` (and
  usually the whole `tkwry` package). Prefer
   `--collect-submodules tkwry` (PyInstaller) or `--include-package=tkwry`
   (Nuitka). If import fails at runtime, that flag is the first place to look.
3. **Ship Tkinter.** Most CPython builds include it; some minimal embeddable
  builds do not — verify `import tkinter` in the frozen app.
4. **Do not strip platform runtimes.** WebView2 (Windows) is a separate
  installer unless you ship the Fixed Version runtime beside your app
   (Microsoft redistribution rules apply).
5. **Test create → load → destroy** on a clean VM after freezing. Lifecycle
  and wakeup-pipe teardown bugs show up only after repeated Toplevel
   open/close cycles.

Minimal entry script for the samples below:

```python
# main.py
import tkinter as tk
from tkwry import WebView

root = tk.Tk()
root.geometry("900x600")
frame = tk.Frame(root)
frame.pack(fill="both", expand=True)
web = WebView(frame, url="https://example.com")
web.when_failed(lambda exc: print("create failed:", exc))
root.mainloop()
```



## PyInstaller

Not CI-verified in 0.1.x. Install once:

```bash
pip install pyinstaller tkwry
```

### Windows → `.exe`

```bat
pyinstaller --noconsole --onefile --collect-submodules tkwry --name MyApp main.py
```

Output: `dist\MyApp.exe`.

Onedir (easier to debug missing DLLs):

```bat
pyinstaller --noconsole --onedir --collect-submodules tkwry --name MyApp main.py
```

Output: `dist\MyApp\MyApp.exe`.

Document WebView2 for end users, or bundle Fixed Version WebView2 per
Microsoft guidance. Missing WebView2 → `creation_failed` /
`<<WebViewCreateFailed>>` (constructor does not raise).
[Platform notes — WebView2](platforms.md#webview2-runtime-probe-and-install).

### macOS → `.app`

```bash
pyinstaller --windowed --onedir --collect-submodules tkwry --name MyApp main.py
```

Output: `dist/MyApp.app`. Sign and notarize before distribution. Import
`tkwry` before other AppKit startup
([Platform notes — macOS](platforms.md#macos-embedding)).

One-file macOS builds are possible (`--onefile --windowed`) but onedir
`.app` bundles are usually easier to sign.

### Flagship demo (`tkwry_browser.py`)

UI assets are embedded in the script (no separate `web/` folder). From a
clone with tkwry installed (`pip install -e .`):

**Windows** — one-file `.exe`:

```bat
pyinstaller --noconsole --onefile --collect-submodules tkwry --name tkwry-browser examples/tkwry_browser.py
```

**macOS** — onedir `.app`:

```bash
pyinstaller --windowed --onedir --collect-submodules tkwry --name tkwry-browser examples/tkwry_browser.py
```

Profile data still lives under `~/.tkwry/` at runtime (not inside the bundle).

### Static `app=` assets

If your app uses `WebView(..., app="./web")`, add the tree:

**Windows** (`;` in `--add-data`):

```bat
pyinstaller --noconsole --onedir --collect-submodules tkwry --add-data "web;web" --name MyApp main.py
```

**macOS** (`:` in `--add-data`):

```bash
pyinstaller --windowed --onedir --collect-submodules tkwry --add-data "web:web" --name MyApp main.py
```

Resolve the frozen path with `sys._MEIPASS` (PyInstaller) when constructing
`app=`.

## Nuitka

Not CI-verified in 0.1.x. You compile Python **and** still ship the prebuilt
`tkwry._core` extension unless you rebuild from source inside Nuitka's tree.

```bash
pip install nuitka tkwry
```



### Windows → one-file `.exe`

Samples use **`--standalone --onefile`** so you get a single movable `.exe`
(payload extracts to a temp dir on launch). Startup is slower than a folder
build; target PCs still need WebView2.

```bat
python -m nuitka --standalone --onefile --windows-console-mode=disable --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --output-filename=MyApp.exe main.py
```

Output: `MyApp.exe` in the project directory (plus build caches you can
delete). While debugging a silent launch failure, rebuild with
`--windows-console-mode=force`:

```bat
python -m nuitka --standalone --onefile --windows-console-mode=force --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --output-filename=MyApp.exe main.py
```

Folder-only `--standalone` (no `--onefile`) writes `main.dist\` and
requires shipping that whole directory — useful to inspect missing DLLs, not
for end-user copy of a lone `.exe`.

### macOS → `.app`

Homebrew CPython often has no usable static `libpython`; pass
`--static-libpython=no` (python.org / pyenv builds may not need it).

```bash
python -m nuitka --standalone --macos-create-app-bundle --static-libpython=no --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --macos-app-name=MyApp main.py
```

Sign / notarize the resulting bundle like any other macOS app.

### Flagship demo (`tkwry_browser.py`)

**Windows** — one-file `.exe`:

```bat
python -m nuitka --standalone --onefile --windows-console-mode=disable --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --output-filename=tkwry-browser.exe examples/tkwry_browser.py
```

**macOS** — `.app` bundle:

```bash
python -m nuitka --standalone --macos-create-app-bundle --static-libpython=no --enable-plugin=tk-inter --include-package=tkwry --include-distribution-metadata=tkwry --macos-app-name=tkwry-browser --output-filename=tkwry-browser examples/tkwry_browser.py
```

Expect to iterate on include/data flags. Same WebView2 / WKWebView runtime
rules as PyInstaller.

### Troubleshooting (macOS freeze)

1. `Automatic detection of static libpython failed` / `Homebrew Python is unexpectedly broken` — add `--static-libpython=no` (see samples above).

### Troubleshooting (Windows freeze)

1. **No window, no error** — console is disabled. Rebuild with
  `--windows-console-mode=force` and run from `cmd` to see the traceback.
2. **Create-failed dialog / blank content** — install
  [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
   on the machine that runs the exe.
3. **Moved only a folder-build** `.exe` **and it died** — without `--onefile`,
  `--standalone` writes a `*.dist` directory you must ship whole. The
   samples above use `--onefile` for a single portable Windows `.exe`.
4. `Failed to add resources … error code 22` **/ Anti-Virus warnings** —
  Windows Defender (or similar) briefly locked the exe while Nuitka attached
   resources. If the log later says `Succeeded … in attempt N` and
   `Successfully created`, the build is fine. To reduce retries: exclude the
   project / `*.dist` / `*.build` / `*.onefile-build` folders from
   real-time scanning.



## What 0.2.0 will add

Beta 0.2.0 targets automated PyInstaller / Nuitka smoke on Windows and
macOS CI (minimal create/load/destroy). Until then, report packaging gaps as
GitHub issues with OS, freezer version, and traceback.

## Related

- [Release provenance](provenance.md) — SHA-256 checksums and build attestations
- [Usage — Cleanup](usage.md#cleanup)
- [Usage — Observability](usage.md#observability)
- [Platform notes — WebView2](platforms.md#webview2-runtime-probe-and-install)
- [README — Known limitations](../README.md#-known-limitations)

