"""Mini-browser demo for tkwry (single-file; UI assets embedded below).

The toolbar (tabs / URL / nav) and the side pane (bookmarks / history) are
local ``app=`` WebViews with RPC. Each uses its own UI ``WebSession`` (shared
``app=`` roots on one session are not allowed). Content tabs use a
**separate** ``WebSession`` and ``bridge_origins="*"`` for link interception
plus a small clipboard RPC surface (Tk pasteboard bridge).

Requires tkwry ``>= 0.1.8``. Architecture notes: ``docs/examples-browser.md``.

Run::

    python examples/tkwry_browser.py
    python examples/tkwry_browser.py --private
"""

from __future__ import annotations

import atexit
import functools
import json
import re
import shutil
import sys
import tempfile
import tkinter as tk
import uuid
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any
from urllib.parse import quote_plus, urlparse

REQUIRED_TKWRY = "0.1.8"


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _require_tkwry() -> None:
    """Exit unless an installed/built tkwry meets REQUIRED_TKWRY."""
    try:
        import tkwry as _tkwry
    except ImportError as exc:
        raise SystemExit(
            f"This demo needs tkwry >= {REQUIRED_TKWRY} "
            "(rebuild the native extension from this repository).\n"
            "  fix:    pip install -e .\n"
            f"  detail: {exc}"
        ) from exc

    found = getattr(_tkwry, "__version__", "0.0.0")
    if _version_tuple(found) < _version_tuple(REQUIRED_TKWRY):
        raise SystemExit(
            f"This demo needs tkwry >= {REQUIRED_TKWRY} (found {found}).\n"
            f"  imported: {_tkwry.__file__}\n"
            "  fix:      pip install -e .   # bump + rebuild native _core"
        )


_require_tkwry()

from tkwry import (  # noqa: E402
    ContextMenuEvent,
    Download,
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
    PermissionKind,
    PermissionResponse,
    TkwrySecurityWarning,
    WebSession,
    WebView,
    __version__ as TKWRY_VERSION,
    configure_window,
    open_in_browser,
)

# Content tabs only — chrome uses a separate session + app= (no "*").
warnings.filterwarnings("ignore", category=TkwrySecurityWarning)

DEFAULT_HOME = "about:blank"
DEFAULT_SEARCH = "https://www.bing.com/search?q={query}"
DEFAULT_PROFILE = "default"
PROFILES_DIR = Path.home() / ".tkwry"
BLANK_TAB_URL = "about:blank"
TKWRY_REPO_URL = "https://github.com/mashu3/tkwry"
# Icon-only crop of wry-logo.svg (tauri-apps/wry, MIT OR Apache-2.0).
WRY_TAB_ICON = (
    "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg"
    "%22%20viewBox%3D%2220%2040%20140%20120%22%20fill%3D%22none%22%3E%3Cpath%20d%3D"
    "%22M119.04%2069.86a18.3%2018.3%200%2011-36.6%200%2018.3%2018.3%200%200136.6%200z"
    "%22%20fill%3D%22%23FFC131%22%2F%3E%3Ccircle%20cx%3D%2269.97%22%20cy%3D%22122.25"
    "%22%20transform%3D%22rotate%28180%2069.97%20122.25%29%22%20fill%3D%22%23FFC131"
    "%22%20r%3D%2218.3%22%2F%3E%3Cpath%20fill-rule%3D%22evenodd%22%20clip-rule%3D"
    "%22evenodd%22%20d%3D%22M138.66%20128.53a69.85%2069.85%200%2001-24.1%209.8%2049%2049"
    "%200%20002.33-22.12%2049.08%2049.08%200%2010-56.04-74.96%2081.5%2081.5%200%2000-"
    "26.78%207.78%2069.86%2069.86%200%2011104.6%2079.5zM34.9%2061.77l17.12%202.1c.41-"
    "3.32%201.15-6.57%202.21-9.7a69.88%2069.88%200%2000-19.33%207.6z%22%20fill%3D%22"
    "%23FFC131%22%2F%3E%3Cpath%20fill-rule%3D%22evenodd%22%20clip-rule%3D%22evenodd"
    "%22%20d%3D%22M31.94%2063.58a69.85%2069.85%200%200124.3-9.85%2048.97%2048.97%200"
    "%2000-2.74%2022.23%2049.09%2049.09%200%201056.26%2074.88%2081.48%2081.48%200%2000"
    "26.8-7.83A69.86%2069.86%200%201131.94%2063.58zm84.44%2074.33z%22%20fill%3D%22"
    "%23FFC131%22%2F%3E%3C%2Fsvg%3E"
)


def _is_ntp_url(url: str | None) -> bool:
    text = (url or "").strip().lower()
    return text in ("", BLANK_TAB_URL, "about:blank")


MAX_HISTORY = 500
MAX_CLOSED_TABS = 25
MUTED = "#666666"
CHROME_HEIGHT = 96
SIDE_PANE_WIDTH = 220
UI_BG_LIGHT = (244, 245, 247, 255)
UI_BG_DARK = (28, 30, 34, 255)
# Allow https favicons in chrome / side (default app CSP blocks them).
CHROME_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob: data:; "
    "worker-src 'self' blob:; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)
SIDE_CSP = CHROME_CSP

# Settings tab needs only local resources.
SETTINGS_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "worker-src 'self'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)

# ---------------------------------------------------------------------------
# Bundled UI assets (embedded; materialized to a temp dir at first use)
# ---------------------------------------------------------------------------

_UI_BUNDLES: dict[str, dict[str, str]] = {
    "chrome": {
        "index.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>tkwry chrome</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <div id="app">
    <div id="tabs" class="tabs" role="tablist"></div>
    <div class="toolbar">
      <button type="button" class="icon" id="btn-back" title="Back" aria-label="Back">
        <span class="glyph g-back" aria-hidden="true"></span>
      </button>
      <button type="button" class="icon" id="btn-forward" title="Forward" aria-label="Forward">
        <span class="glyph g-forward" aria-hidden="true"></span>
      </button>
      <button type="button" class="icon" id="btn-reload" title="Reload" aria-label="Reload">
        <span class="glyph g-reload" aria-hidden="true"></span>
      </button>
      <button type="button" class="icon" id="btn-home" title="Home" aria-label="Home">
        <span class="glyph g-home" aria-hidden="true"></span>
      </button>
      <form id="url-form" class="url-form" autocomplete="off">
        <div id="url-shell" class="url-shell unknown">
          <span id="security" class="url-sec" title="Security" aria-hidden="true">
            <span class="glyph g-lock" aria-hidden="true"></span>
          </span>
          <span id="scheme" class="scheme" hidden></span>
          <input id="url" type="text" spellcheck="false" placeholder="Search or enter address" />
          <button type="button" class="url-fav" id="btn-fav" title="Favorite" aria-label="Favorite">
            <span class="glyph g-star" aria-hidden="true"></span>
          </button>
        </div>
      </form>
      <button type="button" class="icon" id="btn-profile" title="Switch Profile" aria-label="Switch Profile">
        <span class="glyph g-profile" aria-hidden="true"></span>
      </button>
      <button type="button" class="icon" id="btn-menu" title="Menu" aria-label="Menu">
        <span class="glyph g-menu" aria-hidden="true"></span>
      </button>
    </div>
  </div>
  <script src="app.js"></script>
</body>
</html>
""",
        "styles.css": """:root {
  --bg: #f4f5f7;
  --bg-2: #e8eaed;
  --line: #c9ced6;
  --text: #1a1d23;
  --muted: #5f6773;
  --accent: #2f6fed;
  --accent-soft: #dbe7ff;
  --surface: #ffffff;
  --tab-hover: rgba(255, 255, 255, 0.55);
  --secure: #1a7f37;
  --insecure: #cf222e;
  --font: "SF Pro Text", "Segoe UI", system-ui, sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1e22;
    --bg-2: #14161a;
    --line: #3a3f48;
    --text: #e8eaed;
    --muted: #9aa3af;
    --accent: #6b9fff;
    --accent-soft: rgba(107, 159, 255, 0.22);
    --surface: #252830;
    --tab-hover: rgba(255, 255, 255, 0.06);
    --secure: #3fb950;
    --insecure: #ff7b72;
  }
}

* { box-sizing: border-box; }
html, body {
  margin: 0;
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font: 13px/1.35 var(--font);
  user-select: none;
  overflow: hidden;
}

#app {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-bottom: 1px solid var(--line);
}

.tabs {
  display: flex;
  gap: 2px;
  align-items: flex-end;
  padding: 6px 8px 0 8px;
  min-height: 34px;
  overflow: hidden;
  background: linear-gradient(var(--bg-2), var(--bg));
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 0;
  min-width: 48px;
  max-width: 180px;
  padding: 6px 8px 7px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  background: transparent;
  color: var(--muted);
  cursor: grab;
  white-space: nowrap;
  touch-action: none;
  overflow: hidden;
}

.tab:hover { background: var(--tab-hover); color: var(--text); }

.tab.active {
  background: var(--surface);
  color: var(--text);
  border-color: var(--line);
  box-shadow: 0 -1px 0 var(--surface) inset;
}

.tab.dragging {
  opacity: 0.55;
  cursor: grabbing;
}

.tab.drag-target {
  box-shadow: -2px 0 0 var(--accent);
}

.tab .title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tab-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.tab-icon-img {
  width: 14px;
  height: 14px;
  display: block;
}

.tab-icon .glyph {
  width: 14px;
  height: 14px;
  opacity: 0.75;
}

.tab.loading .tab-icon-img,
.tab.loading .tab-icon .glyph {
  opacity: 0.25;
}

.tab.loading .tab-icon::after {
  content: "";
  position: absolute;
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--accent);
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.tab.loading .tab-icon {
  position: relative;
}

.tab .close {
  border: 0;
  background: transparent;
  color: var(--muted);
  width: 18px;
  height: 18px;
  border-radius: 4px;
  cursor: pointer;
  line-height: 1;
  padding: 0;
  flex-shrink: 0;
  opacity: 0;
}

.tab.active .close,
.tab:hover .close {
  opacity: 1;
}

.tab .close:hover { background: var(--bg-2); color: var(--text); }


.tab.loading .title::before {
  display: none;
}

@keyframes spin { to { transform: rotate(360deg); } }

#btn-new-tab {
  margin: 4px 4px 4px 2px;
  width: 28px;
  height: 26px;
  flex: 0 0 auto;
  border: 1px dashed var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}
#btn-new-tab:hover { background: var(--surface); color: var(--text); }

.toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px 8px;
  background: var(--surface);
}

.icon {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  height: 30px;
  width: 30px;
  min-width: 30px;
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.icon:hover { background: var(--bg-2); }
.icon:disabled { opacity: 0.35; cursor: default; }

/* ----- Icons via SVG masks (stable; no emoji) ----- */

.glyph {
  display: block;
  width: 16px;
  height: 16px;
  background: currentColor;
  -webkit-mask: no-repeat center / contain;
  mask: no-repeat center / contain;
}

.g-back {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M10.2 2.2 4.4 8l5.8 5.8 1.4-1.4L7.2 8l4.4-4.4z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M10.2 2.2 4.4 8l5.8 5.8 1.4-1.4L7.2 8l4.4-4.4z'/%3E%3C/svg%3E");
}
.g-forward {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M5.8 2.2 4.4 3.6 8.8 8l-4.4 4.4 1.4 1.4L11.6 8z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M5.8 2.2 4.4 3.6 8.8 8l-4.4 4.4 1.4 1.4L11.6 8z'/%3E%3C/svg%3E");
}
.g-reload {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M13.5 8A5.5 5.5 0 1 1 8 2.5V1l3 2.5L8 6V4.5A3.5 3.5 0 1 0 11.5 8h2z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M13.5 8A5.5 5.5 0 1 1 8 2.5V1l3 2.5L8 6V4.5A3.5 3.5 0 1 0 11.5 8h2z'/%3E%3C/svg%3E");
}
.g-stop {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect fill='%23000' x='4' y='4' width='8' height='8' rx='1'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect fill='%23000' x='4' y='4' width='8' height='8' rx='1'/%3E%3C/svg%3E");
}
.g-home {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 2.2 1.5 7.5h2V14h4v-4h1v4h4V7.5h2z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 2.2 1.5 7.5h2V14h4v-4h1v4h4V7.5h2z'/%3E%3C/svg%3E");
}
.g-profile {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle fill='%23000' cx='8' cy='5.5' r='3'/%3E%3Cpath fill='%23000' d='M2.5 14.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle fill='%23000' cx='8' cy='5.5' r='3'/%3E%3Cpath fill='%23000' d='M2.5 14.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5z'/%3E%3C/svg%3E");
}
.g-settings {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM5.9 1.2h4.2l.3 1.2 1.2.5 1-.9L14 3.9l-.9 1 1.2.5.3 1.2v4.2l-1.2.3-.5 1.2.9 1-1.4 1.4-1-.9-1.2.5-.3 1.2H5.9l-.3-1.2-1.2-.5-1 .9L2 12.1l.9-1-1.2-.5-.3-1.2V5.2l1.2-.3.5-1.2-.9-1L3.9 1.2l1 .9 1.2-.5.3-1.2z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM5.9 1.2h4.2l.3 1.2 1.2.5 1-.9L14 3.9l-.9 1 1.2.5.3 1.2v4.2l-1.2.3-.5 1.2.9 1-1.4 1.4-1-.9-1.2.5-.3 1.2H5.9l-.3-1.2-1.2-.5-1 .9L2 12.1l.9-1-1.2-.5-.3-1.2V5.2l1.2-.3.5-1.2-.9-1L3.9 1.2l1 .9 1.2-.5.3-1.2z'/%3E%3C/svg%3E");
}
.g-menu {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M2 4h12v1.6H2zm0 3.2h12v1.6H2zm0 3.2h12V12H2z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M2 4h12v1.6H2zm0 3.2h12v1.6H2zm0 3.2h12V12H2z'/%3E%3C/svg%3E");
}
.g-lock {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 1.5A3.5 3.5 0 0 0 4.5 5v2H3v7.5h10V7H11.5V5A3.5 3.5 0 0 0 8 1.5zm0 1.6A1.9 1.9 0 0 1 9.9 5v2H6.1V5A1.9 1.9 0 0 1 8 3.1z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 1.5A3.5 3.5 0 0 0 4.5 5v2H3v7.5h10V7H11.5V5A3.5 3.5 0 0 0 8 1.5zm0 1.6A1.9 1.9 0 0 1 9.9 5v2H6.1V5A1.9 1.9 0 0 1 8 3.1z'/%3E%3C/svg%3E");
}
.url-shell.insecure .g-lock {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 1.5A3.5 3.5 0 0 0 4.5 5h1.6A1.9 1.9 0 0 1 8 3.1 1.9 1.9 0 0 1 9.9 5v2H3v7.5h10V7H11.5V5A3.5 3.5 0 0 0 8 1.5z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='M8 1.5A3.5 3.5 0 0 0 4.5 5h1.6A1.9 1.9 0 0 1 8 3.1 1.9 1.9 0 0 1 9.9 5v2H3v7.5h10V7H11.5V5A3.5 3.5 0 0 0 8 1.5z'/%3E%3C/svg%3E");
}
.g-star {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='m8 1.6 1.7 3.5 3.8.6-2.8 2.7.7 3.8L8 10.4l-3.4 1.8.7-3.8L2.5 5.7l3.8-.6z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='%23000' d='m8 1.6 1.7 3.5 3.8.6-2.8 2.7.7 3.8L8 10.4l-3.4 1.8.7-3.8L2.5 5.7l3.8-.6z'/%3E%3C/svg%3E");
  opacity: 0.45;
}
.url-fav.on .g-star {
  opacity: 1;
  background: #e3b341;
}

/* ----- URL omnibox ----- */

.url-form {
  flex: 1;
  display: flex;
  min-width: 120px;
}

.url-shell {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 4px 0 6px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--bg);
  min-width: 0;
}
.url-shell:focus-within {
  border-color: var(--accent);
  background: var(--surface);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.url-shell.secure .url-sec,
.url-shell.secure .scheme { color: var(--secure); }
.url-shell.insecure .url-sec,
.url-shell.insecure .scheme { color: var(--insecure); }
.url-shell.local .url-sec,
.url-shell.local .scheme,
.url-shell.unknown .url-sec,
.url-shell.blank .url-sec { display: none; }
.url-shell.unknown .scheme,
.url-shell.blank .scheme { color: var(--muted); }

.url-sec {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.scheme {
  flex-shrink: 0;
  font: inherit;
  font-weight: 600;
  font-size: 12px;
  letter-spacing: -0.01em;
  user-select: none;
  pointer-events: none;
}

#url {
  flex: 1;
  min-width: 0;
  height: 26px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--text);
  font: inherit;
  padding: 0 2px;
}

.url-fav {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
}
.url-fav:hover { background: var(--bg-2); }
.url-fav.on { color: #e3b341; }
""",
        "app.js": """(() => {
  const $ = (sel) => document.querySelector(sel);
  const tabsEl = $("#tabs");
  const urlInput = $("#url");
  const urlShell = $("#url-shell");
  const schemeEl = $("#scheme");
  const securityEl = $("#security");
  const btnBack = $("#btn-back");
  const btnForward = $("#btn-forward");
  const btnReload = $("#btn-reload");
  const btnHome = $("#btn-home");
  const btnFav = $("#btn-fav");
  const reloadGlyph = btnReload.querySelector(".glyph");

  let state = {
    tabs: [],
    active: null,
    url: "",
    canGoBack: false,
    canGoForward: false,
    loading: false,
    security: "unknown",
    securityTitle: "Unknown",
    isFavorite: false,
  };
  let editing = false;

  async function call(method, payload = {}) {
    if (!window.tkwry || !window.tkwry.invoke) {
      throw new Error("tkwry bridge unavailable");
    }
    return window.tkwry.invoke(method, payload);
  }

  function splitUrl(url) {
    const text = (url || "").trim();
    if (!text) return { scheme: "", rest: "", kind: "unknown" };
    try {
      const u = new URL(text);
      const scheme = u.protocol + "//";
      const rest = text.startsWith(scheme) ? text.slice(scheme.length) : text;
      const proto = u.protocol.replace(":", "").toLowerCase();
      let kind = "unknown";
      if (proto === "https") kind = "secure";
      else if (proto === "http") kind = "insecure";
      else if (proto === "file" || proto === "tkwry") kind = "local";
      return { scheme, rest, kind, proto };
    } catch (_e) {
      return { scheme: "", rest: text, kind: "unknown", proto: "" };
    }
  }

  function paintUrlField() {
    const full = state.url || "";
    const parts = splitUrl(full);
    const kind = state.security || parts.kind || "unknown";
    urlShell.className = "url-shell " + kind;
    securityEl.title = state.securityTitle || "Security";

    if (editing || document.activeElement === urlInput) {
      schemeEl.hidden = true;
      schemeEl.textContent = "";
      if (document.activeElement !== urlInput) {
        urlInput.value = full;
      }
      return;
    }

    if (parts.scheme && (parts.proto === "https" || parts.proto === "http")) {
      schemeEl.hidden = false;
      schemeEl.textContent = parts.scheme;
      schemeEl.className = "scheme " + parts.proto;
      urlInput.value = parts.rest;
    } else {
      schemeEl.hidden = true;
      schemeEl.textContent = "";
      schemeEl.className = "scheme";
      urlInput.value = full;
    }
  }

  function navigateFromField() {
    const typed = urlInput.value.trim();
    let target = typed;
    if (!editing && !schemeEl.hidden && schemeEl.textContent) {
      // Rest-only field: reattach visible scheme unless user typed a full URL.
      if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(typed)) {
        target = schemeEl.textContent + typed;
      }
    }
    call("navigate", { url: target }).catch(console.error);
  }

  let draggingId = null;
  let dragMoved = false;
  let dragPointerId = null;
  let dragStartX = 0;

  function tabEls() {
    return [...tabsEl.querySelectorAll(".tab")];
  }

  function clearDragTarget() {
    for (const t of tabEls()) t.classList.remove("drag-target");
  }

  function dragAfterTab(x) {
    const tabs = tabEls().filter((t) => t.dataset.id !== draggingId);
    let closest = null;
    let closestOffset = Number.NEGATIVE_INFINITY;
    for (const tab of tabs) {
      const box = tab.getBoundingClientRect();
      const offset = x - box.left - box.width / 2;
      if (offset < 0 && offset > closestOffset) {
        closestOffset = offset;
        closest = tab;
      }
    }
    return closest;
  }

  function placeDraggingTab(clientX) {
    const dragging = tabEls().find((t) => t.dataset.id === draggingId);
    if (!dragging) return;
    const after = dragAfterTab(clientX);
    clearDragTarget();
    const neu = $("#btn-new-tab");
    if (after == null) {
      tabsEl.insertBefore(dragging, neu);
    } else {
      after.classList.add("drag-target");
      tabsEl.insertBefore(dragging, after);
    }
  }

  function commitTabOrder() {
    const order = tabEls().map((t) => t.dataset.id).filter(Boolean);
    const dragging = tabEls().find((t) => t.dataset.id === draggingId);
    if (dragging) dragging.classList.remove("dragging");
    clearDragTarget();
    const moved = dragMoved;
    draggingId = null;
    dragPointerId = null;
    if (!moved || !order.length) return;
    call("reorder_tabs", { order }).catch(console.error);
  }

  function endTabDrag() {
    if (!draggingId) {
      dragPointerId = null;
      return;
    }
    commitTabOrder();
    setTimeout(() => {
      dragMoved = false;
    }, 0);
  }

  function onTabPointerMove(e) {
    if (dragPointerId == null || e.pointerId !== dragPointerId || !draggingId) return;
    if (!dragMoved && Math.abs(e.clientX - dragStartX) < 6) return;
    dragMoved = true;
    e.preventDefault();
    const dragging = tabEls().find((t) => t.dataset.id === draggingId);
    if (dragging) dragging.classList.add("dragging");
    placeDraggingTab(e.clientX);
  }

  function onTabPointerUp(e) {
    if (dragPointerId == null || e.pointerId !== dragPointerId) return;
    window.removeEventListener("pointermove", onTabPointerMove, true);
    window.removeEventListener("pointerup", onTabPointerUp, true);
    window.removeEventListener("pointercancel", onTabPointerUp, true);
    endTabDrag();
  }

  function renderTabs() {
    tabsEl.innerHTML = "";
    for (const tab of state.tabs) {
      const el = document.createElement("div");
      el.className =
        "tab" +
        (tab.id === state.active ? " active" : "") +
        (tab.loading ? " loading" : "");
      el.dataset.id = tab.id;
      el.title = tab.title || "New Tab";
      el.style.touchAction = "none";

      const iconWrap = document.createElement("span");
      iconWrap.className = "tab-icon";
      if (tab.icon === "settings") {
        const glyph = document.createElement("span");
        glyph.className = "glyph g-settings";
        glyph.setAttribute("aria-hidden", "true");
        iconWrap.appendChild(glyph);
      } else if (tab.icon) {
        const img = document.createElement("img");
        img.src = tab.icon;
        img.alt = "";
        img.className = "tab-icon-img";
        img.draggable = false;
        img.addEventListener("error", () => {
          img.remove();
        });
        iconWrap.appendChild(img);
      }
      el.appendChild(iconWrap);

      const title = document.createElement("span");
      title.className = "title";
      title.textContent = tab.title || "New Tab";
      el.appendChild(title);

      const close = document.createElement("button");
      close.type = "button";
      close.className = "close";
      close.title = "Close";
      close.textContent = "×";
      close.addEventListener("click", (e) => {
        e.stopPropagation();
        call("close_tab", { tab_id: tab.id }).catch(console.error);
      });
      el.appendChild(close);

      el.addEventListener("click", (e) => {
        if (dragMoved) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        call("select_tab", { tab_id: tab.id }).catch(console.error);
      });
      el.addEventListener("auxclick", (e) => {
        if (e.button === 1) {
          e.preventDefault();
          call("close_tab", { tab_id: tab.id }).catch(console.error);
        }
      });
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        if (e.target.closest(".close")) return;
        draggingId = tab.id;
        dragMoved = false;
        dragPointerId = e.pointerId;
        dragStartX = e.clientX;
        window.addEventListener("pointermove", onTabPointerMove, true);
        window.addEventListener("pointerup", onTabPointerUp, true);
        window.addEventListener("pointercancel", onTabPointerUp, true);
      });
      tabsEl.appendChild(el);
    }

    const neu = document.createElement("button");
    neu.type = "button";
    neu.id = "btn-new-tab";
    neu.title = "New Tab";
    neu.textContent = "+";
    neu.addEventListener("click", () => call("new_tab", {}).catch(console.error));
    tabsEl.appendChild(neu);
  }

  function applyState(next) {
    state = { ...state, ...next };
    paintUrlField();
    btnBack.disabled = !state.canGoBack;
    btnForward.disabled = !state.canGoForward;
    if (state.loading) {
      reloadGlyph.className = "glyph g-stop";
      btnReload.title = "Stop";
    } else {
      reloadGlyph.className = "glyph g-reload";
      btnReload.title = "Reload";
    }
    btnFav.classList.toggle("on", !!state.isFavorite);
    btnFav.title = state.isFavorite ? "Remove bookmark" : "Add bookmark";
    // Avoid wiping an in-progress tab drag (chrome state ticks ~350ms).
    if (!draggingId) renderTabs();
  }

  function menuAnchor(el) {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.bottom + 2),
    };
  }

  btnBack.addEventListener("click", () => call("go_back", {}).catch(console.error));
  btnForward.addEventListener("click", () => call("go_forward", {}).catch(console.error));
  btnReload.addEventListener("click", () => call("reload_or_stop", {}).catch(console.error));
  btnHome.addEventListener("click", () => call("go_home", {}).catch(console.error));
  btnFav.addEventListener("click", () => call("toggle_favorite", {}).catch(console.error));
  $("#btn-profile").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    call("open_profile_menu", menuAnchor(e.currentTarget)).catch(console.error);
  });
  $("#btn-menu").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    call("open_app_menu", menuAnchor(e.currentTarget)).catch(console.error);
  });

  urlInput.addEventListener("focus", () => {
    editing = true;
    urlInput.value = state.url || urlInput.value;
    schemeEl.hidden = true;
    call("url_editing", { active: true }).catch(() => {});
    requestAnimationFrame(() => urlInput.select());
  });
  urlInput.addEventListener("blur", () => {
    editing = false;
    call("url_editing", { active: false }).catch(() => {});
    paintUrlField();
  });

  function insertAtSelection(text) {
    const start = urlInput.selectionStart ?? urlInput.value.length;
    const end = urlInput.selectionEnd ?? start;
    const before = urlInput.value.slice(0, start);
    const after = urlInput.value.slice(end);
    urlInput.value = before + text + after;
    const caret = start + text.length;
    urlInput.setSelectionRange(caret, caret);
    editing = true;
  }

  function selectedUrlText() {
    const start = urlInput.selectionStart ?? 0;
    const end = urlInput.selectionEnd ?? 0;
    if (end > start) return urlInput.value.slice(start, end);
    return urlInput.value;
  }

  window.chromePasteUrl = function (text) {
    if (document.activeElement !== urlInput && !editing) return false;
    urlInput.focus();
    insertAtSelection(String(text ?? ""));
    return true;
  };

  window.chromeCopyUrl = function () {
    if (document.activeElement !== urlInput && !editing) return "";
    return selectedUrlText();
  };

  window.chromeCutUrl = function () {
    if (document.activeElement !== urlInput && !editing) return "";
    const start = urlInput.selectionStart ?? 0;
    const end = urlInput.selectionEnd ?? 0;
    const text = selectedUrlText();
    if (end > start) {
      urlInput.value = urlInput.value.slice(0, start) + urlInput.value.slice(end);
      urlInput.setSelectionRange(start, start);
      editing = true;
    }
    return text;
  };

  urlInput.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return;
    const key = e.key.toLowerCase();
    if (key === "v") {
      e.preventDefault();
      e.stopPropagation();
      call("clipboard_get", {})
        .then((text) => insertAtSelection(String(text || "")))
        .catch(console.error);
      return;
    }
    if (key === "c") {
      e.preventDefault();
      e.stopPropagation();
      call("clipboard_set", { text: selectedUrlText() }).catch(console.error);
      return;
    }
    if (key === "x") {
      e.preventDefault();
      e.stopPropagation();
      const text = window.chromeCutUrl();
      call("clipboard_set", { text }).catch(console.error);
      return;
    }
    if (key === "a") {
      e.preventDefault();
      urlInput.select();
    }
  });

  $("#url-form").addEventListener("submit", (e) => {
    e.preventDefault();
    navigateFromField();
    urlInput.blur();
  });

  function boot() {
    if (!window.tkwry) {
      setTimeout(boot, 50);
      return;
    }
    window.tkwry.on("state", applyState);
    call("get_state", {}).then(applyState).catch(console.error);

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const syncTheme = () =>
      call("set_ui_theme", { dark: mq.matches }).catch(() => {});
    syncTheme();
    if (mq.addEventListener) mq.addEventListener("change", syncTheme);
    else if (mq.addListener) mq.addListener(syncTheme);
  }

  boot();
})();
""",
    },
    "side": {
        "index.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>tkwry side</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <div id="app">
    <div id="tree" class="tree" role="tree"></div>
  </div>
  <script src="app.js"></script>
</body>
</html>
""",
        "styles.css": """:root {
  --bg: #f4f5f7;
  --bg-2: #e8eaed;
  --line: #c9ced6;
  --text: #1a1d23;
  --muted: #5f6773;
  --accent: #2f6fed;
  --row-hover: rgba(0, 0, 0, 0.05);
  --row-active: #dbe7ff;
  --font: "SF Pro Text", "Segoe UI", system-ui, sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1e22;
    --bg-2: #14161a;
    --line: #3a3f48;
    --text: #e8eaed;
    --muted: #9aa3af;
    --accent: #6b9fff;
    --row-hover: rgba(255, 255, 255, 0.06);
    --row-active: rgba(107, 159, 255, 0.28);
  }
}

* { box-sizing: border-box; }
html, body {
  margin: 0;
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font: 11px/1.2 var(--font);
  user-select: none;
  overflow: hidden;
}

#app {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-right: 1px solid var(--line);
}

.tree {
  flex: 1;
  overflow: auto;
  padding: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 0 4px;
  cursor: pointer;
  height: 16px;
  color: var(--text);
}
.row:hover { background: var(--row-hover); }
.row.selected { background: var(--row-active); }

.row.section {
  height: 16px;
  margin-top: 2px;
}
.row.section:first-child { margin-top: 0; }
.row.section .label {
  font-weight: 600;
  font-size: 10px;
  color: var(--muted);
}

.row.folder .label { font-weight: 600; }

.twist {
  width: 10px;
  flex-shrink: 0;
  text-align: center;
  color: var(--muted);
  font-size: 8px;
  line-height: 1;
}
.twist.empty { visibility: hidden; }

.icon {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  border-radius: 2px;
  object-fit: contain;
  background: transparent;
}
.icon.placeholder {
  background: var(--bg-2);
  border: 1px solid var(--line);
}

.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  font-size: 11px;
}

.meta {
  margin-left: auto;
  flex-shrink: 0;
  color: var(--muted);
  font-size: 9px;
  padding-left: 2px;
}

.empty {
  padding: 12px 8px;
  color: var(--muted);
  text-align: center;
  line-height: 1.35;
  font-size: 11px;
}

/* ----- Settings navigation (TOC) ----- */

.settings-nav {
  padding: 8px 0;
}

.settings-heading {
  padding: 8px 12px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.row.settings-item {
  height: 28px;
  padding: 0 12px;
  border-radius: 0;
}

.row.settings-item .label {
  font-size: 12px;
}

.row.settings-item.selected {
  background: var(--row-active);
  color: var(--accent);
  font-weight: 600;
}

.row.settings-item.selected .label {
  color: var(--accent);
}
""",
        "app.js": """(() => {
  const treeEl = document.querySelector("#tree");
  // Sections + "Today" start open (Firefox-like).
  const openFolders = new Set(["__bookmarks__", "__history__", "hist-day:today"]);
  let state = { mode: "library", tree: [], active_section: "" };
  let selectedId = null;

  async function call(method, payload = {}) {
    if (!window.tkwry || !window.tkwry.invoke) {
      throw new Error("tkwry bridge unavailable");
    }
    return window.tkwry.invoke(method, payload);
  }

  function menuAnchor(_el, event) {
    return {
      x: Math.round(event ? event.clientX : 8),
      y: Math.round(event ? event.clientY : 8),
    };
  }

  function isBranch(node) {
    return (
      node.kind === "section" ||
      node.kind === "folder" ||
      node.kind === "day"
    );
  }

  function renderSettingsNav() {
    treeEl.innerHTML = "";
    treeEl.className = "tree settings-nav";

    const title = document.createElement("div");
    title.className = "settings-heading";
    title.textContent = "Settings";
    treeEl.appendChild(title);

    for (const node of state.tree) {
      const row = document.createElement("div");
      row.className = "row settings-item";
      if (node.id === state.active_section) {
        row.classList.add("selected");
      }
      row.dataset.id = node.id;
      row.title = node.title || "";

      const label = document.createElement("span");
      label.className = "label";
      label.textContent = node.title || "Untitled";
      row.appendChild(label);

      row.addEventListener("click", () => {
        call("open_settings_section", { section_id: node.id }).catch(console.error);
      });

      treeEl.appendChild(row);
    }
  }

  function renderLibrary() {
    treeEl.className = "tree";
    treeEl.innerHTML = "";
    if (!state.tree.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Library is empty.";
      treeEl.appendChild(empty);
      return;
    }

    function walk(nodes, depth) {
      for (const node of nodes) {
        const branch = isBranch(node);
        const row = document.createElement("div");
        row.className = "row " + (node.kind || "item");
        if (node.id === selectedId) row.classList.add("selected");
        row.style.paddingLeft = `${4 + depth * 10}px`;
        row.dataset.id = node.id;
        row.dataset.kind = node.kind;
        if (node.url) row.dataset.url = node.url;
        row.title = node.url || node.title || "";

        const twist = document.createElement("span");
        twist.className = "twist" + (branch ? "" : " empty");
        const opened = openFolders.has(node.id);
        twist.textContent = branch ? (opened ? "▾" : "▸") : "";
        row.appendChild(twist);

        if (node.icon) {
          const img = document.createElement("img");
          img.className = "icon";
          img.src = node.icon;
          img.alt = "";
          img.draggable = false;
          img.addEventListener("error", () => {
            img.classList.add("placeholder");
            img.removeAttribute("src");
          });
          row.appendChild(img);
        } else if (node.kind === "folder" || node.kind === "day") {
          const mark = document.createElement("span");
          mark.className = "icon placeholder";
          row.appendChild(mark);
        } else if (node.kind === "bookmark" || node.kind === "history") {
          const mark = document.createElement("span");
          mark.className = "icon placeholder";
          row.appendChild(mark);
        }

        const label = document.createElement("span");
        label.className = "label";
        label.textContent = node.title || node.url || "Untitled";
        row.appendChild(label);

        if (node.when && node.kind === "history") {
          const meta = document.createElement("span");
          meta.className = "meta";
          meta.textContent = node.when;
          row.appendChild(meta);
        }

        row.addEventListener("click", () => {
          selectedId = node.id;
          if (branch) {
            if (openFolders.has(node.id)) openFolders.delete(node.id);
            else openFolders.add(node.id);
          }
          render();
        });

        row.addEventListener("dblclick", (e) => {
          e.preventDefault();
          if (node.url && (node.kind === "bookmark" || node.kind === "history")) {
            call("open_url", { url: node.url }).catch(console.error);
          }
        });

        row.addEventListener("contextmenu", (e) => {
          e.preventDefault();
          e.stopPropagation();
          selectedId = node.id;
          render();
          const anchor = menuAnchor(row, e);
          if (node.kind === "bookmark" || node.kind === "folder") {
            call("open_bookmark_menu", {
              node_id: node.id,
              kind: node.kind,
              ...anchor,
            }).catch(console.error);
          } else if (node.kind === "section" && node.id === "__bookmarks__") {
            call("open_bookmark_menu", {
              node_id: "",
              kind: "root",
              ...anchor,
            }).catch(console.error);
          } else if (
            node.kind === "history" ||
            node.kind === "day" ||
            node.id === "__history__"
          ) {
            call("open_history_menu", {
              entry_id: node.kind === "history" ? node.id : "",
              url: node.url || "",
              ...anchor,
            }).catch(console.error);
          }
        });

        treeEl.appendChild(row);

        if (branch && opened && Array.isArray(node.children)) {
          if (!node.children.length && node.kind === "section") {
            const empty = document.createElement("div");
            empty.className = "row";
            empty.style.paddingLeft = `${4 + (depth + 1) * 10}px`;
            empty.style.color = "var(--muted)";
            empty.style.cursor = "default";
            const t = document.createElement("span");
            t.className = "twist empty";
            empty.appendChild(t);
            const l = document.createElement("span");
            l.className = "label";
            l.textContent =
              node.id === "__bookmarks__"
                ? "No bookmarks — use ☆ to add"
                : "No history yet";
            empty.appendChild(l);
            treeEl.appendChild(empty);
          } else {
            walk(node.children, depth + 1);
          }
        }
      }
    }

    walk(state.tree, 0);
  }

  function render() {
    if (state.mode === "settings") {
      renderSettingsNav();
      return;
    }
    renderLibrary();
  }

  function applyState(next) {
    state = { ...state, ...next };
    if (Array.isArray(next.focusOpen)) {
      for (const id of next.focusOpen) openFolders.add(id);
    }
    render();
  }

  treeEl.addEventListener("contextmenu", (e) => {
    if (state.mode === "settings") return;
    if (e.target.closest(".row")) return;
    e.preventDefault();
    call("open_bookmark_menu", {
      node_id: "",
      kind: "root",
      ...menuAnchor(treeEl, e),
    }).catch(console.error);
  });

  function boot() {
    if (!window.tkwry) {
      setTimeout(boot, 50);
      return;
    }
    window.tkwry.on("state", applyState);

    async function loadState(retries = 40) {
      try {
        applyState(await call("get_state", {}));
      } catch (err) {
        if (retries > 0) {
          setTimeout(() => loadState(retries - 1), 50);
        } else {
          console.error(err);
        }
      }
    }
    loadState();

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const syncTheme = () =>
      call("set_ui_theme", { dark: mq.matches }).catch(() => {});
    syncTheme();
    if (mq.addEventListener) mq.addEventListener("change", syncTheme);
    else if (mq.addListener) mq.addListener(syncTheme);
  }

  boot();
})();
""",
    },
    "settings": {
        "index.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Settings</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="settings">
    <header class="settings-header">
      <h1>Settings</h1>
    </header>

    <section class="card" id="general-section">
      <h2>General</h2>
      <div class="field">
        <label for="home-url">Home URL</label>
        <input id="home-url" type="text" spellcheck="false" />
        <p class="hint">Use <code>about:blank</code> for the New Tab start page.</p>
      </div>
      <div class="field">
        <label for="search-url">Search URL</label>
        <input id="search-url" type="text" spellcheck="false" />
        <p class="hint">Use <code>{query}</code> as the placeholder.</p>
      </div>
      <div class="field">
        <label for="download-dir">Downloads</label>
        <div class="row">
          <input id="download-dir" type="text" spellcheck="false" />
          <button type="button" id="browse-downloads">Browse…</button>
        </div>
      </div>
      <div class="actions">
        <button type="button" class="primary" id="save-general">Save</button>
        <button type="button" id="clear-data">Clear cookies &amp; cache…</button>
      </div>
    </section>

    <section class="card" id="profiles-section">
      <h2>Profiles</h2>
      <p class="current-profile" id="current-profile"></p>
      <ul class="profile-list" id="profile-list"></ul>
      <div class="actions">
        <button type="button" id="switch-profile">Switch</button>
        <button type="button" class="danger" id="delete-profile">Delete…</button>
      </div>
      <div class="field">
        <label for="new-profile-name">New profile name</label>
        <div class="row">
          <input id="new-profile-name" type="text" spellcheck="false" />
          <button type="button" id="create-profile">Create Profile</button>
        </div>
      </div>
    </section>

    <section class="card" id="cookies-section">
      <h2>Cookies</h2>
      <div class="cookie-header">
        <p class="hint" id="cookie-note"></p>
        <button type="button" id="refresh-cookies">Refresh</button>
      </div>
      <div class="table-wrap">
        <table class="cookie-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Domain</th>
              <th>Path</th>
              <th>Secure</th>
            </tr>
          </thead>
          <tbody id="cookie-table-body"></tbody>
        </table>
      </div>
      <p class="hint">Cookie values are omitted.</p>
    </section>
  </main>

  <script src="app.js"></script>
</body>
</html>
""",
        "styles.css": """:root {
  --bg: #f4f5f7;
  --surface: #ffffff;
  --line: #c9ced6;
  --text: #1a1d23;
  --muted: #5f6773;
  --accent: #2f6fed;
  --accent-soft: #dbe7ff;
  --danger: #cf222e;
  --font: "SF Pro Text", "Segoe UI", system-ui, sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1e22;
    --surface: #252830;
    --line: #3a3f48;
    --text: #e8eaed;
    --muted: #9aa3af;
    --accent: #6b9fff;
    --accent-soft: rgba(107, 159, 255, 0.22);
    --danger: #ff7b72;
  }
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font: 13px/1.45 var(--font);
}

.settings {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px 20px 48px;
}

.settings-header h1 {
  margin: 0 0 16px;
  font-size: 20px;
  font-weight: 600;
}

.card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 16px;
}

.card h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.field {
  margin-bottom: 12px;
}

.field label {
  display: block;
  margin-bottom: 4px;
  color: var(--muted);
  font-weight: 500;
}

.field input {
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font: inherit;
}

.field input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.hint code {
  background: var(--bg);
  padding: 1px 4px;
  border-radius: 4px;
}

.row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.row input {
  flex: 1;
  min-width: 0;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

button {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
  cursor: pointer;
}

button:hover {
  background: var(--bg);
}

button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

button.primary:hover {
  filter: brightness(1.05);
}

button.danger {
  color: var(--danger);
}

button.danger:hover {
  border-color: var(--danger);
}

.current-profile {
  margin: 0 0 8px;
  color: var(--muted);
}

.profile-list {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  max-height: 140px;
  overflow-y: auto;
}

.profile-list li {
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
}

.profile-list li:hover {
  background: var(--bg);
}

.profile-list li.selected {
  background: var(--accent-soft);
  color: var(--accent);
}

.profile-list li.current {
  font-weight: 600;
}

.cookie-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.cookie-header .hint {
  margin: 0;
  flex: 1;
}

.table-wrap {
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: auto;
  max-height: 220px;
}

.cookie-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.cookie-table th,
.cookie-table td {
  padding: 6px 8px;
  text-align: left;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.cookie-table th {
  background: var(--bg);
  color: var(--muted);
  font-weight: 600;
  position: sticky;
  top: 0;
}

.cookie-table tr:last-child td {
  border-bottom: 0;
}
""",
        "app.js": """(() => {
  const $ = (sel) => document.querySelector(sel);
  const homeInput = $("#home-url");
  const searchInput = $("#search-url");
  const downloadInput = $("#download-dir");
  const currentProfileEl = $("#current-profile");
  const profileList = $("#profile-list");
  const newProfileInput = $("#new-profile-name");
  const cookieNote = $("#cookie-note");
  const cookieBody = $("#cookie-table-body");

  let state = {
    home: "",
    search: "",
    download_dir: "",
    current_profile: "",
    profiles: [],
    cookie_note: "",
    cookies: [],
  };
  let selectedProfile = "";

  async function call(method, payload = {}) {
    if (!window.tkwry || !window.tkwry.invoke) {
      throw new Error("tkwry bridge unavailable");
    }
    return window.tkwry.invoke(method, payload);
  }

  function renderGeneral() {
    homeInput.value = state.home || "";
    searchInput.value = state.search || "";
    downloadInput.value = state.download_dir || "";
  }

  function renderProfiles() {
    currentProfileEl.textContent = `Current profile: ${state.current_profile || "default"}`;
    profileList.innerHTML = "";
    for (const name of state.profiles) {
      const li = document.createElement("li");
      li.textContent = name;
      li.dataset.name = name;
      if (name === state.current_profile) {
        li.classList.add("current");
        li.textContent = `● ${name}`;
      }
      if (name === selectedProfile) {
        li.classList.add("selected");
      }
      li.addEventListener("click", () => {
        selectedProfile = name;
        renderProfiles();
      });
      li.addEventListener("dblclick", () => {
        selectedProfile = name;
        switchProfile();
      });
      profileList.appendChild(li);
    }
  }

  function renderCookies() {
    cookieNote.textContent = state.cookie_note || "";
    cookieBody.innerHTML = "";
    for (const c of state.cookies) {
      const tr = document.createElement("tr");
      for (const key of ["name", "domain", "path"]) {
        const td = document.createElement("td");
        td.textContent = c[key] || "";
        tr.appendChild(td);
      }
      const td = document.createElement("td");
      td.textContent = c.secure ? "yes" : "no";
      tr.appendChild(td);
      cookieBody.appendChild(tr);
    }
    if (!state.cookies.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.textContent = "(none)";
      td.style.color = "var(--muted)";
      tr.appendChild(td);
      cookieBody.appendChild(tr);
    }
  }

  function render() {
    renderGeneral();
    renderProfiles();
    renderCookies();
  }

  function applyState(next) {
    state = { ...state, ...next };
    if (!selectedProfile && state.current_profile) {
      selectedProfile = state.current_profile;
    }
    render();
  }

  function saveGeneral() {
    call("save_settings", {
      home: homeInput.value.trim(),
      search: searchInput.value.trim(),
      download_dir: downloadInput.value.trim(),
    }).catch(console.error);
  }

  function switchProfile() {
    if (!selectedProfile) {
      return;
    }
    call("switch_profile", { name: selectedProfile }).catch(console.error);
  }

  function deleteProfile() {
    if (!selectedProfile) {
      return;
    }
    call("delete_profile", { name: selectedProfile }).catch(console.error);
  }

  function createProfile() {
    const name = newProfileInput.value.trim();
    if (!name) {
      return;
    }
    call("create_profile", { name }).catch(console.error);
  }

  function refreshCookies() {
    call("refresh_cookies", {}).catch(console.error);
  }

  function browseDownloads() {
    call("browse_download_dir", {}).catch(console.error);
  }

  function clearData() {
    call("clear_browsing_data", {}).catch(console.error);
  }

  async function loadState(retries = 30) {
    try {
      applyState(await call("get_state", {}));
    } catch (err) {
      if (retries > 0) {
        setTimeout(() => loadState(retries - 1), 100);
      } else {
        console.error(err);
      }
    }
  }

  function boot() {
    if (!window.tkwry) {
      setTimeout(boot, 50);
      return;
    }
    window.tkwry.on("state", applyState);
    loadState();

    $("#save-general").addEventListener("click", saveGeneral);
    $("#clear-data").addEventListener("click", clearData);
    $("#browse-downloads").addEventListener("click", browseDownloads);
    $("#switch-profile").addEventListener("click", switchProfile);
    $("#delete-profile").addEventListener("click", deleteProfile);
    $("#create-profile").addEventListener("click", createProfile);
    $("#refresh-cookies").addEventListener("click", refreshCookies);

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const syncTheme = () =>
      call("set_ui_theme", { dark: mq.matches }).catch(() => {});
    syncTheme();
    if (mq.addEventListener) mq.addEventListener("change", syncTheme);
    else if (mq.addListener) mq.addListener(syncTheme);
  }

  boot();
})();
""",
    },
}


@functools.lru_cache(maxsize=1)
def _ui_asset_dirs() -> tuple[Path, Path, Path]:
    """Write bundled HTML/CSS/JS to a temp tree for ``app=`` loading."""
    base = Path(tempfile.mkdtemp(prefix="tkwry-browser-ui-"))
    for subdir, files in _UI_BUNDLES.items():
        target = base / subdir
        target.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (target / name).write_text(text, encoding="utf-8")
    atexit.register(shutil.rmtree, base, ignore_errors=True)
    return base / "chrome", base / "side", base / "settings"


DEFAULT_BOOKMARKS: tuple[tuple[str, str], ...] = (
    ("tkwry", "https://github.com/mashu3/tkwry"),
    ("tkipw", "https://github.com/mashu3/tkipw"),
    ("tkface", "https://github.com/mashu3/tkface"),
    ("mashu3", "https://github.com/mashu3"),
)


def _flatten_bookmark_links(nodes: list[Any], *, limit: int = 8) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def walk(items: list[Any]) -> None:
        for node in items:
            if len(out) >= limit:
                return
            kind = getattr(node, "kind", "")
            if kind == "bookmark":
                url = (getattr(node, "url", None) or "").strip()
                if url:
                    title = getattr(node, "title", None) or url
                    out.append({"title": str(title), "url": url})
            elif kind == "folder":
                walk(list(getattr(node, "children", []) or []))

    walk(nodes)
    return out


def _blank_tab_html(*, dark: bool = False) -> str:
    """Self-contained new-tab page (search + shortcuts); state via IPC/emit."""
    theme = "dark" if dark else "light"
    seeds = [
        {"title": title, "url": url} for title, url in DEFAULT_BOOKMARKS
    ]
    seed_json = json.dumps(seeds, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>New Tab</title>
  <link rel="icon" href="{WRY_TAB_ICON}" />
  <style>
    :root {{
      --ink: #15202b;
      --muted: #5a6878;
      --accent: #2a6fd6;
      --accent-soft: rgba(42, 111, 214, 0.14);
      --field: rgba(255, 255, 255, 0.55);
      --field-border: rgba(21, 32, 43, 0.12);
      --chip: #1d4f91;
      --glow-a: #b9d0ea;
      --glow-b: #c5ddd4;
      --base-0: #eef2f6;
      --base-1: #e3e9f0;
      --base-2: #d7e0ea;
      --font-display: "Avenir Next", "Segoe UI Variable Display", "Trebuchet MS", sans-serif;
      --font-body: "Avenir Next", "Segoe UI Variable", "Trebuchet MS", sans-serif;
    }}
    html[data-theme="dark"] {{
      --ink: #e8eef5;
      --muted: #9aa8b8;
      --accent: #7eb0ff;
      --accent-soft: rgba(126, 176, 255, 0.16);
      --field: rgba(20, 24, 32, 0.55);
      --field-border: rgba(232, 238, 245, 0.12);
      --chip: #8eb7ef;
      --glow-a: #1a3352;
      --glow-b: #163832;
      --base-0: #12151a;
      --base-1: #171b22;
      --base-2: #1c222c;
    }}
    @media (prefers-color-scheme: dark) {{
      html:not([data-theme="light"]) {{
        --ink: #e8eef5;
        --muted: #9aa8b8;
        --accent: #7eb0ff;
        --accent-soft: rgba(126, 176, 255, 0.16);
        --field: rgba(20, 24, 32, 0.55);
        --field-border: rgba(232, 238, 245, 0.12);
        --chip: #8eb7ef;
        --glow-a: #1a3352;
        --glow-b: #163832;
        --base-0: #12151a;
        --base-1: #171b22;
        --base-2: #1c222c;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      min-height: 100%;
      color: var(--ink);
      font: 15px/1.45 var(--font-body);
      background:
        radial-gradient(ellipse 90% 55% at 50% -8%, var(--glow-a) 0%, transparent 58%),
        radial-gradient(ellipse 55% 45% at 100% 100%, var(--glow-b) 0%, transparent 50%),
        linear-gradient(168deg, var(--base-0) 0%, var(--base-1) 48%, var(--base-2) 100%);
      background-attachment: fixed;
    }}
    body {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: clamp(24px, 6vh, 64px) 20px 48px;
      min-height: 100vh;
    }}
    .stage {{
      width: min(100%, 36rem);
      text-align: center;
      animation: rise 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(14px); }}
      to {{ opacity: 1; transform: none; }}
    }}
    .brand {{
      margin: 0 0 0.35rem;
      font: 600 clamp(2.6rem, 7vw, 3.6rem)/1.05 var(--font-display);
      letter-spacing: -0.045em;
      color: var(--ink);
    }}
    .tag {{
      margin: 0 0 1.75rem;
      color: var(--muted);
      font-size: 1.02rem;
      letter-spacing: 0.01em;
      animation: rise 0.75s cubic-bezier(0.22, 1, 0.36, 1) 0.08s both;
    }}
    form.search {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
      width: 100%;
      padding: 0.15rem 0 0.55rem;
      border-bottom: 1.5px solid var(--field-border);
      background: transparent;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
      animation: rise 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.14s both;
    }}
    form.search:focus-within {{
      border-bottom-color: var(--accent);
      box-shadow: 0 1px 0 var(--accent-soft);
    }}
    form.search input {{
      flex: 1;
      min-width: 0;
      border: 0;
      outline: none;
      background: transparent;
      color: var(--ink);
      font: 1.05rem/1.4 var(--font-body);
      padding: 0.55rem 0;
    }}
    form.search input::placeholder {{ color: var(--muted); opacity: 0.85; }}
    form.search button {{
      border: 0;
      background: transparent;
      color: var(--accent);
      font: 600 0.92rem/1 var(--font-body);
      letter-spacing: 0.02em;
      cursor: pointer;
      padding: 0.45rem 0.15rem;
      opacity: 0.9;
    }}
    form.search button:hover {{ opacity: 1; }}
    .shortcuts {{
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 0.35rem 1.35rem;
      margin: 2rem 0 0;
      padding: 0;
      list-style: none;
      animation: rise 0.85s cubic-bezier(0.22, 1, 0.36, 1) 0.22s both;
    }}
    .shortcuts a {{
      display: inline-flex;
      align-items: center;
      gap: 0.55rem;
      color: var(--ink);
      text-decoration: none;
      padding: 0.35rem 0.1rem;
      border-radius: 0;
      opacity: 0.88;
      transition: opacity 0.15s ease, transform 0.15s ease;
    }}
    .shortcuts a:hover {{ opacity: 1; transform: translateY(-1px); }}
    .mark {{
      display: inline-grid;
      place-items: center;
      width: 1.7rem;
      height: 1.7rem;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--chip);
      font: 600 0.78rem/1 var(--font-display);
      letter-spacing: 0.02em;
    }}
    .label {{
      font-size: 0.95rem;
      letter-spacing: 0.01em;
    }}
  </style>
</head>
<body>
  <main class="stage">
    <h1 class="brand">tkwry</h1>
    <p class="tag">Search the web or open a page</p>
    <form class="search" id="search" autocomplete="off">
      <input id="q" type="search" name="q" placeholder="Search or enter address"
        spellcheck="false" autofocus enterkeyhint="go" />
      <button type="submit">Go</button>
    </form>
    <ul class="shortcuts" id="shortcuts" aria-label="Shortcuts"></ul>
  </main>
  <script>
    const SEED = {seed_json};
    function post(payload) {{
      if (window.ipc && window.ipc.postMessage) {{
        window.ipc.postMessage(JSON.stringify(payload));
      }}
    }}
    function initial(title) {{
      const t = String(title || "?").trim();
      const m = t.match(/[A-Za-z0-9]/);
      return (m ? m[0] : "?").toUpperCase();
    }}
    function paintShortcuts(items) {{
      const el = document.getElementById("shortcuts");
      el.innerHTML = "";
      (items || []).forEach((item) => {{
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = item.url;
        a.title = item.url;
        a.innerHTML = '<span class="mark"></span><span class="label"></span>';
        a.querySelector(".mark").textContent = initial(item.title);
        a.querySelector(".label").textContent = item.title;
        a.addEventListener("click", (e) => {{
          e.preventDefault();
          post({{ action: "navigate", href: item.url }});
        }});
        li.appendChild(a);
        el.appendChild(li);
      }});
    }}
    function applyNtp(state) {{
      if (!state || typeof state !== "object") return;
      if (state.dark === true) document.documentElement.setAttribute("data-theme", "dark");
      else if (state.dark === false) document.documentElement.setAttribute("data-theme", "light");
      if (Array.isArray(state.shortcuts)) paintShortcuts(state.shortcuts);
      const q = document.getElementById("q");
      if (q && state.focus) q.focus();
    }}
    document.getElementById("search").addEventListener("submit", (e) => {{
      e.preventDefault();
      const q = document.getElementById("q").value.trim();
      if (!q) return;
      post({{ action: "navigate", q }});
    }});
    paintShortcuts(SEED);
    function boot() {{
      if (window.tkwry && window.tkwry.on) {{
        if (!window._tkwryNtp) {{
          window._tkwryNtp = true;
          window.tkwry.on("ntp", applyNtp);
        }}
        post({{ action: "ntp_ready" }});
        return;
      }}
      setTimeout(boot, 40);
    }}
    boot();
  </script>
</body>
</html>
"""


LINK_HELPER_JS = """
(function () {
  function absHref(href) {
    try { return new URL(href, location.href).href; } catch (e) { return href || ""; }
  }
  function openable(href) {
    try {
      var u = new URL(href, location.href);
      return (
        u.protocol === "http:" ||
        u.protocol === "https:" ||
        u.protocol === "file:" ||
        u.protocol === "tkwry:"
      );
    } catch (e) { return false; }
  }
  function post(payload) {
    if (window.ipc && window.ipc.postMessage) {
      window.ipc.postMessage(JSON.stringify(payload));
    }
  }
  function maybeNewTab(e) {
    var a = e.target && e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    var href = absHref(a.getAttribute("href") || a.href);
    if (!openable(href)) return;
    var blank = (a.target || "").toLowerCase() === "_blank";
    var modified = e.metaKey || e.ctrlKey || e.button === 1;
    if (!blank && !modified) return;
    e.preventDefault();
    e.stopPropagation();
    post({ action: "newtab", href: href });
  }
  document.addEventListener("click", maybeNewTab, true);
  document.addEventListener("auxclick", maybeNewTab, true);
  window.open = function (url) {
    if (url) {
      var href = absHref(String(url));
      if (openable(href)) post({ action: "newtab", href: href });
    }
    return null;
  };
  function showNotice(payload) {
    var text = "";
    if (payload && typeof payload === "object" && payload.text) {
      text = String(payload.text);
    } else if (payload != null) {
      text = String(payload);
    }
    if (!text) return;
    var el = document.createElement("div");
    el.textContent = text;
    el.style.cssText = [
      "position:fixed","z-index:2147483647","right:12px","bottom:12px",
      "max-width:min(360px,80vw)","padding:10px 14px","border-radius:8px",
      "background:#111827","color:#f9fafb","font:13px/1.4 system-ui,sans-serif",
      "box-shadow:0 8px 24px rgba(0,0,0,.35)",
    ].join(";");
    (document.body || document.documentElement).appendChild(el);
    setTimeout(function () { el.remove(); }, 3500);
  }
  function bootNotice() {
    if (!window.tkwry || !window.tkwry.on || window._tkwryNotice) return;
    window._tkwryNotice = true;
    window.tkwry.on("notice", showNotice);
  }
  bootNotice();
  document.addEventListener("DOMContentLoaded", bootNotice);
})();
"""

# WKWebView often cannot reach the system pasteboard in this embed; bridge via Tk.
# Also maps Cmd/Ctrl browser shortcuts into Python (RPC or IPC).
SHORTCUT_BRIDGE_JS = """
(function () {
  if (window._tkwryBrowserShortcuts) return;
  window._tkwryBrowserShortcuts = true;

  function typingTarget(el) {
    if (!el || el === document.documentElement || el === document.body) return false;
    var tag = String(el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function isTextField(el) {
    if (!el) return false;
    var tag = String(el.tagName || "").toLowerCase();
    if (tag === "textarea") return true;
    if (tag !== "input") return false;
    var type = String(el.type || "text").toLowerCase();
    return (
      type === "text" ||
      type === "search" ||
      type === "url" ||
      type === "password" ||
      type === ""
    );
  }

  function pageSelection() {
    try {
      return String(window.getSelection() || "");
    } catch (e) {
      return "";
    }
  }

  function fieldSelected(el) {
    try {
      var start = el.selectionStart ?? 0;
      var end = el.selectionEnd ?? 0;
      if (end > start) return el.value.slice(start, end);
      return el.value;
    } catch (e) {
      return pageSelection();
    }
  }

  function insertFieldText(el, text) {
    text = String(text ?? "");
    if (isTextField(el)) {
      var start = el.selectionStart ?? el.value.length;
      var end = el.selectionEnd ?? start;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      var caret = start + text.length;
      try { el.setSelectionRange(caret, caret); } catch (e) {}
      return;
    }
    try {
      document.execCommand("insertText", false, text);
    } catch (e) {}
  }

  function postIpc(payload) {
    if (window.ipc && window.ipc.postMessage) {
      window.ipc.postMessage(JSON.stringify(payload));
    }
  }

  function clipboardSet(text) {
    text = String(text ?? "");
    if (window.tkwry && typeof window.tkwry.invoke === "function") {
      return window.tkwry.invoke("clipboard_set", { text: text });
    }
    postIpc({ action: "clipboard_set", text: text });
    return Promise.resolve();
  }

  function clipboardGet() {
    if (window.tkwry && typeof window.tkwry.invoke === "function") {
      return window.tkwry.invoke("clipboard_get", {});
    }
    return new Promise(function (resolve) {
      var id = "c" + String(Date.now()) + Math.random().toString(16).slice(2);
      window._tkwryClipWait = window._tkwryClipWait || {};
      window._tkwryClipWait[id] = resolve;
      if (window.tkwry && window.tkwry.on && !window._tkwryClipListen) {
        window._tkwryClipListen = true;
        window.tkwry.on("clipboard", function (payload) {
          if (!payload || typeof payload !== "object") return;
          var rid = payload.id;
          var fn = window._tkwryClipWait && window._tkwryClipWait[rid];
          if (!fn) return;
          delete window._tkwryClipWait[rid];
          fn(payload.text || "");
        });
      }
      postIpc({ action: "clipboard_get", id: id });
      setTimeout(function () {
        if (window._tkwryClipWait && window._tkwryClipWait[id]) {
          delete window._tkwryClipWait[id];
          resolve("");
        }
      }, 1500);
    });
  }

  function handleClipboardKey(e) {
    var key = String(e.key || "").toLowerCase();
    var mod = e.metaKey || e.ctrlKey;
    if (!mod) return false;
    if (key !== "c" && key !== "x" && key !== "v" && key !== "a") return false;

    var el = e.target;
    var typing = typingTarget(el);

    if (key === "a" && typing) {
      e.preventDefault();
      e.stopPropagation();
      if (isTextField(el)) {
        try { el.select(); } catch (err) {}
      } else {
        try { document.execCommand("selectAll"); } catch (err) {}
      }
      return true;
    }

    if (key === "c") {
      var copyText = typing && isTextField(el) ? fieldSelected(el) : pageSelection();
      if (!copyText && typing) copyText = fieldSelected(el);
      if (!copyText) return false;
      e.preventDefault();
      e.stopPropagation();
      clipboardSet(copyText).catch(function () {});
      return true;
    }

    if (key === "x") {
      if (!typing) return false;
      var cutText = isTextField(el) ? fieldSelected(el) : pageSelection();
      if (!cutText) return false;
      e.preventDefault();
      e.stopPropagation();
      clipboardSet(cutText)
        .then(function () {
          if (isTextField(el)) {
            var start = el.selectionStart ?? 0;
            var end = el.selectionEnd ?? 0;
            if (end > start) {
              el.value = el.value.slice(0, start) + el.value.slice(end);
              try { el.setSelectionRange(start, start); } catch (err) {}
            }
          } else {
            try { document.execCommand("delete"); } catch (err) {}
          }
        })
        .catch(function () {});
      return true;
    }

    if (key === "v") {
      if (!typing) return false;
      e.preventDefault();
      e.stopPropagation();
      clipboardGet()
        .then(function (text) {
          insertFieldText(el, text);
        })
        .catch(function () {});
      return true;
    }
    return false;
  }

  function shortcutName(e) {
    var key = String(e.key || "").toLowerCase();
    var mod = e.metaKey || e.ctrlKey;
    if (e.key === "F5") return "reload";
    if (e.key === "F12") return "devtools";
    if (e.altKey && (e.key === "ArrowLeft" || e.key === "Left")) return "back";
    if (e.altKey && (e.key === "ArrowRight" || e.key === "Right")) return "forward";
    if (e.altKey && e.key === "Home") return "home";
    // Match Tk Control-Tab (do not steal macOS Cmd+Tab app switcher).
    if (key === "tab" && e.ctrlKey && !e.metaKey && !e.shiftKey) return "next_tab";
    if (key === "tab" && e.ctrlKey && !e.metaKey && e.shiftKey) return "prev_tab";
    if (!mod) return null;
    if (key === "c" || key === "x" || key === "v" || key === "a") return null;
    if (key === "[") return "back";
    if (key === "]") return "forward";
    if (key === "t" && e.shiftKey) return "restore_tab";
    if (key === "t") return "new_tab";
    if (key === "n" && e.shiftKey) return "private";
    if (key === "n") return "new_window";
    if (key === "w") return "close_tab";
    if (key === "l") return "focus_url";
    if (key === "r") return "reload";
    if (key === "d") return "bookmark";
    if (key === "b") return "side_pane";
    if (key === "h") return "history";
    if (key === "p") return "print";
    if (key === "i" && e.shiftKey) return "devtools";
    if (key === "," || e.code === "Comma") return "settings";
    if (key === "=" || key === "+") return "zoom_in";
    if (key === "-") return "zoom_out";
    if (key === "0") return "zoom_reset";
    if (key >= "1" && key <= "8") return "tab_" + key;
    if (key === "9") return "tab_last";
    return null;
  }

  function postShortcut(name) {
    if (window.tkwry && typeof window.tkwry.invoke === "function") {
      window.tkwry.invoke("run_shortcut", { name: name }).catch(function () {});
      return;
    }
    postIpc({ action: "shortcut", name: name });
  }

  document.addEventListener(
    "keydown",
    function (e) {
      if (handleClipboardKey(e)) return;
      var name = shortcutName(e);
      if (!name) return;
      e.preventDefault();
      e.stopPropagation();
      postShortcut(name);
    },
    true
  );
})();
"""

LINK_HELPER_JS = LINK_HELPER_JS + SHORTCUT_BRIDGE_JS

_LOOKS_LIKE_HOST = re.compile(
    r"^(?:localhost|(?:[\w-]+\.)+[a-zA-Z]{2,})(?::\d+)?(?:[/?#].*)?$"
)


# ----- storage -----


@dataclass
class HistoryItem:
    url: str
    title: str
    timestamp: str


@dataclass
class BookmarkNode:
    id: str
    kind: str  # "folder" | "bookmark"
    title: str = ""
    url: str = ""
    children: list[BookmarkNode] = field(default_factory=list)

    @classmethod
    def folder(
        cls, title: str, *, children: list[BookmarkNode] | None = None
    ) -> BookmarkNode:
        return cls(
            id=_new_bookmark_id(),
            kind="folder",
            title=title,
            children=list(children or ()),
        )

    @classmethod
    def bookmark(cls, title: str, url: str) -> BookmarkNode:
        return cls(
            id=_new_bookmark_id(),
            kind="bookmark",
            title=title,
            url=url,
        )

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> BookmarkNode | None:
        kind = str(raw.get("kind") or "").strip()
        if kind not in {"folder", "bookmark"}:
            return None
        node_id = str(raw.get("id") or _new_bookmark_id())
        title = str(raw.get("title") or "").strip()
        if kind == "bookmark":
            url = str(raw.get("url") or "").strip()
            if not url:
                return None
            return cls(id=node_id, kind="bookmark", title=title or url, url=url)
        children_raw = raw.get("children")
        children: list[BookmarkNode] = []
        if isinstance(children_raw, list):
            for item in children_raw:
                if isinstance(item, dict):
                    child = cls.from_json(item)
                    if child is not None:
                        children.append(child)
        return cls(
            id=node_id,
            kind="folder",
            title=title or "Folder",
            children=children,
        )


def _new_bookmark_id() -> str:
    return uuid.uuid4().hex[:12]


def _default_bookmarks() -> list[BookmarkNode]:
    links = BookmarkNode.folder("Links")
    for title, url in DEFAULT_BOOKMARKS:
        links.children.append(BookmarkNode.bookmark(title, url))
    return [links]


def _bookmark_to_json(node: BookmarkNode) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind,
        "title": node.title,
    }
    if node.kind == "bookmark":
        payload["url"] = node.url
    else:
        payload["children"] = [_bookmark_to_json(child) for child in node.children]
    return payload


@dataclass
class BrowserSettings:
    home: str = DEFAULT_HOME
    search_template: str = DEFAULT_SEARCH
    download_dir: str = ""

    def search_url(self, query: str) -> str:
        return self.search_template.format(query=quote_plus(query.strip()))


class BrowserStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._favorites_path = root / "favorites.json"
        self._history_path = root / "history.json"
        self._settings_path = root / "settings.json"
        self._bookmarks_seeded = False
        self.bookmarks = self._load_bookmarks()
        self.history = self._load_history()
        self.settings = self._load_settings()
        if self._bookmarks_seeded or not self._favorites_path.is_file():
            self.save_bookmarks()
            self._bookmarks_seeded = False
        if not self.settings.download_dir:
            self.settings.download_dir = str(root / "downloads")
            Path(self.settings.download_dir).mkdir(parents=True, exist_ok=True)
            self.save_settings()

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_bookmarks(self) -> list[BookmarkNode]:
        if not self._favorites_path.is_file():
            self._bookmarks_seeded = True
            return _default_bookmarks()
        raw = self._read_json(self._favorites_path, [])
        if not isinstance(raw, list) or not raw:
            self._bookmarks_seeded = True
            return _default_bookmarks()
        first = raw[0]
        if not isinstance(first, dict):
            self._bookmarks_seeded = True
            return _default_bookmarks()
        if str(first.get("kind") or "") in {"folder", "bookmark"}:
            nodes: list[BookmarkNode] = []
            for item in raw:
                if isinstance(item, dict):
                    node = BookmarkNode.from_json(item)
                    if node is not None:
                        nodes.append(node)
            if not nodes:
                self._bookmarks_seeded = True
                return _default_bookmarks()
            return nodes
        # Legacy flat favorites.json
        nodes = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            nodes.append(BookmarkNode.bookmark(str(item.get("title") or url), url))
        if not nodes:
            self._bookmarks_seeded = True
            return _default_bookmarks()
        self._bookmarks_seeded = True
        return nodes

    def _load_history(self) -> list[HistoryItem]:
        raw = self._read_json(self._history_path, [])
        out: list[HistoryItem] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            out.append(
                HistoryItem(
                    url=url,
                    title=str(item.get("title") or url),
                    timestamp=str(item.get("timestamp") or ""),
                )
            )
        return out

    def _load_settings(self) -> BrowserSettings:
        raw = self._read_json(self._settings_path, {})
        if not isinstance(raw, dict):
            return BrowserSettings()
        return BrowserSettings(
            home=str(raw.get("home") or DEFAULT_HOME),
            search_template=str(raw.get("search_template") or DEFAULT_SEARCH),
            download_dir=str(raw.get("download_dir") or ""),
        )

    def save_bookmarks(self) -> None:
        self._write_json(
            self._favorites_path,
            [_bookmark_to_json(node) for node in self.bookmarks],
        )

    def save_history(self) -> None:
        self._write_json(self._history_path, [asdict(item) for item in self.history])

    def save_settings(self) -> None:
        self._write_json(self._settings_path, asdict(self.settings))

    def _find_node(
        self, node_id: str, nodes: list[BookmarkNode] | None = None
    ) -> tuple[list[BookmarkNode], int, BookmarkNode] | None:
        items = self.bookmarks if nodes is None else nodes
        for index, node in enumerate(items):
            if node.id == node_id:
                return items, index, node
            if node.kind == "folder":
                found = self._find_node(node_id, node.children)
                if found is not None:
                    return found
        return None

    def node_by_id(self, node_id: str) -> BookmarkNode | None:
        found = self._find_node(node_id)
        return found[2] if found is not None else None

    def iter_bookmark_urls(self) -> list[str]:
        urls: list[str] = []

        def walk(nodes: list[BookmarkNode]) -> None:
            for node in nodes:
                if node.kind == "bookmark":
                    urls.append(node.url)
                elif node.kind == "folder":
                    walk(node.children)

        walk(self.bookmarks)
        return urls

    def is_favorite(self, url: str) -> bool:
        return url in self.iter_bookmark_urls()

    def toggle_favorite(self, url: str, title: str) -> bool:
        url = url.strip()
        if not url or url in ("about:blank",):
            return False
        found = self._find_bookmark_by_url(url)
        if found is not None:
            parent, index, _node = found
            del parent[index]
            self.save_bookmarks()
            return False
        self.bookmarks.insert(
            0,
            BookmarkNode.bookmark((title or url).strip() or url, url),
        )
        self.save_bookmarks()
        return True

    def _find_bookmark_by_url(
        self, url: str, nodes: list[BookmarkNode] | None = None
    ) -> tuple[list[BookmarkNode], int, BookmarkNode] | None:
        items = self.bookmarks if nodes is None else nodes
        for index, node in enumerate(items):
            if node.kind == "bookmark" and node.url == url:
                return items, index, node
            if node.kind == "folder":
                found = self._find_bookmark_by_url(url, node.children)
                if found is not None:
                    return found
        return None

    def remove_node(self, node_id: str) -> bool:
        found = self._find_node(node_id)
        if found is None:
            return False
        parent, index, _node = found
        del parent[index]
        self.save_bookmarks()
        return True

    def add_folder(self, parent_id: str, title: str) -> BookmarkNode:
        folder = BookmarkNode.folder(title.strip() or "New Folder")
        if parent_id:
            parent = self.node_by_id(parent_id)
            if parent is None or parent.kind != "folder":
                raise ValueError("parent folder not found")
            parent.children.append(folder)
        else:
            self.bookmarks.append(folder)
        self.save_bookmarks()
        return folder

    def add_bookmark(self, parent_id: str, url: str, title: str) -> BookmarkNode:
        url = url.strip()
        if not url:
            raise ValueError("url required")
        bookmark = BookmarkNode.bookmark((title or url).strip() or url, url)
        if parent_id:
            parent = self.node_by_id(parent_id)
            if parent is None or parent.kind != "folder":
                raise ValueError("parent folder not found")
            parent.children.append(bookmark)
        else:
            self.bookmarks.insert(0, bookmark)
        self.save_bookmarks()
        return bookmark

    def rename_node(self, node_id: str, title: str) -> bool:
        node = self.node_by_id(node_id)
        if node is None:
            return False
        clean = title.strip()
        if not clean:
            return False
        node.title = clean
        self.save_bookmarks()
        return True

    def record_history(self, url: str, title: str) -> None:
        url = url.strip()
        if not url or url in ("about:blank",):
            return
        if url.startswith(("tkwry:", "data:", "about:")):
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        today = now[:10]
        for item in self.history:
            if item.url == url and item.timestamp.startswith(today):
                item.title = (title or item.title or url).strip() or url
                item.timestamp = now
                self.history.sort(key=lambda h: h.timestamp, reverse=True)
                self.history = self.history[:MAX_HISTORY]
                self.save_history()
                return
        self.history.insert(
            0,
            HistoryItem(
                url=url,
                title=(title or url).strip() or url,
                timestamp=now,
            ),
        )
        self.history = self.history[:MAX_HISTORY]
        self.save_history()

    def clear_history(self) -> None:
        self.history.clear()
        self.save_history()


# ----- helpers -----


def normalize_input(raw: str, *, home: str, search_url: Callable[[str], str]) -> str:
    text = raw.strip()
    if not text:
        return home
    if "://" in text:
        return text
    if text.startswith("//"):
        return "https:" + text
    if _LOOKS_LIKE_HOST.match(text) or text.startswith("localhost"):
        return "https://" + text
    return search_url(text)


def security_indicator(url: str | None) -> tuple[str, str]:
    """Return (kind, tooltip) for the URL bar — kinds drive CSS, not emoji."""
    if not url:
        return "unknown", "Unknown"
    try:
        scheme = urlparse(url).scheme.lower()
    except ValueError:
        return "unknown", "Unknown"
    if scheme == "https":
        return "secure", "Secure connection (HTTPS)"
    if scheme == "http":
        return "insecure", "Not secure (HTTP)"
    if scheme in ("file", "tkwry"):
        return "local", "Local or special URL"
    if scheme == "about":
        return "blank", "Internal page"
    return "unknown", "Unknown"


def _short_status_url(url: str, *, limit: int = 72) -> str:
    url = (url or "").strip()
    if not url:
        return "page"
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            text = parsed.netloc + (parsed.path or "")
            if parsed.query:
                text += "?" + parsed.query
            if len(text) > limit:
                return text[: limit - 1] + "…"
            return text
    except ValueError:
        pass
    if len(url) > limit:
        return url[: limit - 1] + "…"
    return url


def tab_label(title: str, *, limit: int = 24) -> str:
    label = (title or "New Tab").strip() or "New Tab"
    if len(label) > limit:
        return label[: limit - 1] + "…"
    return label


def is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme.lower() in ("http", "https")
    except ValueError:
        return False


# ----- helpers / payloads -----


def _favicon_url(page_url: str) -> str:
    try:
        host = urlparse(page_url).hostname
    except ValueError:
        host = None
    if not host:
        return ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=16"


def list_profile_names() -> list[str]:
    names: list[str] = []
    if PROFILES_DIR.is_dir():
        for path in sorted(PROFILES_DIR.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                names.append(path.name)
    if DEFAULT_PROFILE not in names:
        names.insert(0, DEFAULT_PROFILE)
    return names


def _bookmark_tree_payload(nodes: list[BookmarkNode]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        if node.kind == "folder":
            out.append(
                {
                    "id": node.id,
                    "kind": "folder",
                    "title": node.title,
                    "children": _bookmark_tree_payload(node.children),
                }
            )
        else:
            item: dict[str, Any] = {
                "id": node.id,
                "kind": "bookmark",
                "title": node.title,
                "url": node.url,
            }
            icon = _favicon_url(node.url)
            if icon:
                item["icon"] = icon
            out.append(item)
    return out


def _history_local_date(timestamp: str) -> date | None:
    text = (timestamp or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _history_day_label(day: date, *, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    age = (today - day).days
    if 0 < age < 7:
        return day.strftime("%A")  # Monday, …
    return day.strftime("%b %d, %Y")


def _history_tree_payload(items: list[HistoryItem]) -> list[dict[str, Any]]:
    """Firefox-style date folders: Today / Yesterday / weekday / older dates."""
    today = date.today()
    buckets: dict[date, list[dict[str, Any]]] = {}
    order: list[date] = []
    for index, item in enumerate(items):
        day = _history_local_date(item.timestamp) or today
        if day not in buckets:
            buckets[day] = []
            order.append(day)
        when = ""
        try:
            raw = item.timestamp
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            when = dt.astimezone().strftime("%H:%M")
        except ValueError:
            when = item.timestamp.replace("T", " ")[11:16] if item.timestamp else ""
        buckets[day].append(
            {
                "id": f"hist:{index}",
                "kind": "history",
                "title": (item.title or item.url).strip() or item.url,
                "url": item.url,
                "when": when,
                **({"icon": icon} if (icon := _favicon_url(item.url)) else {}),
            }
        )
    order.sort(reverse=True)
    out: list[dict[str, Any]] = []
    for day in order:
        key = (
            "today"
            if day == today
            else "yesterday"
            if day == today - timedelta(days=1)
            else day.isoformat()
        )
        out.append(
            {
                "id": f"hist-day:{key}",
                "kind": "day",
                "title": _history_day_label(day, today=today),
                "children": buckets[day],
            }
        )
    return out


SETTINGS_TAB_ID = "tkwry:settings"
SETTINGS_SECTIONS: tuple[tuple[str, str], ...] = (
    ("general-section", "General"),
    ("profiles-section", "Profiles"),
    ("cookies-section", "Cookies"),
)


def sanitize_profile_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name.strip())
    return cleaned or DEFAULT_PROFILE


def profile_dir(name: str) -> Path:
    safe = sanitize_profile_name(name)
    path = (PROFILES_DIR / safe).resolve()
    base = PROFILES_DIR.resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Invalid profile name: {name!r}") from exc
    return path


def delete_profile_data(name: str) -> None:
    path = profile_dir(name)
    if not path.is_dir():
        return
    shutil.rmtree(path)


def tab_icon(tab_id: str, tab: Tab) -> str | None:
    if tab_id == SETTINGS_TAB_ID or tab.kind == "settings":
        return "settings"
    if tab.kind == "ntp":
        return WRY_TAB_ICON
    if tab.web is None or tab.web.destroyed:
        return None
    try:
        url = tab.web.url or ""
    except Exception:
        url = ""
    if not url or _is_ntp_url(url):
        return WRY_TAB_ICON
    icon = _favicon_url(url)
    return icon or None


# ----- app -----


@dataclass
class Tab:
    frame: tk.Frame
    web: WebView | None = None
    title: str = "New Tab"
    loading: bool = False
    kind: str = "content"


class BrowserShortcutBindings:
    """Browser shortcuts via bind_class (ahead of macOS web key-guard) + WebView JS.

    Content / toolbar WebViews often eat Cmd/Ctrl keys before Tcl; those paths
    also post ``shortcut`` IPC / RPC into :meth:`BrowserApp.run_shortcut`.
    """

    TAG = "TkwryBrowserShortcuts"
    NEW_TAB = ("<Command-t>", "<Command-T>", "<Control-t>", "<Control-T>")
    CLOSE_TAB = ("<Command-w>", "<Command-W>", "<Control-w>", "<Control-W>")
    RESTORE_TAB = (
        "<Command-Shift-t>",
        "<Command-Shift-T>",
        "<Control-Shift-t>",
        "<Control-Shift-T>",
    )
    FOCUS_URL = ("<Command-l>", "<Command-L>", "<Control-l>", "<Control-L>")
    RELOAD = (
        "<F5>",
        "<Command-r>",
        "<Command-R>",
        "<Control-r>",
        "<Control-R>",
    )
    BOOKMARK = ("<Command-d>", "<Command-D>", "<Control-d>", "<Control-D>")
    SIDE_PANE = ("<Command-b>", "<Command-B>", "<Control-b>", "<Control-B>")
    HISTORY = ("<Command-h>", "<Command-H>", "<Control-h>", "<Control-H>")
    BACK = (
        "<Alt-Left>",
        "<Meta-Left>",
        "<Command-bracketleft>",
        "<Control-bracketleft>",
    )
    FORWARD = (
        "<Alt-Right>",
        "<Meta-Right>",
        "<Command-bracketright>",
        "<Control-bracketright>",
    )
    HOME = ("<Alt-Home>", "<Meta-Home>")
    NEXT_TAB = ("<Control-Tab>", "<Control-Next>")
    PREV_TAB = (
        "<Control-Shift-Tab>",
        "<Control-ISO_Left_Tab>",
        "<Control-Prior>",
    )
    ZOOM_IN = (
        "<Command-equal>",
        "<Command-plus>",
        "<Control-equal>",
        "<Control-plus>",
        "<Control-KP_Add>",
    )
    ZOOM_OUT = (
        "<Command-minus>",
        "<Control-minus>",
        "<Control-KP_Subtract>",
    )
    ZOOM_RESET = (
        "<Command-0>",
        "<Control-0>",
        "<Command-KP_0>",
        "<Control-KP_0>",
    )
    PRINT = ("<Command-p>", "<Command-P>", "<Control-p>", "<Control-P>")
    DEVTOOLS = (
        "<F12>",
        "<Command-Shift-i>",
        "<Command-Shift-I>",
        "<Control-Shift-i>",
        "<Control-Shift-I>",
    )
    SETTINGS = ("<Command-comma>", "<Control-comma>")
    PRIVATE = (
        "<Command-Shift-n>",
        "<Command-Shift-N>",
        "<Control-Shift-n>",
        "<Control-Shift-N>",
    )
    NEW_WINDOW = (
        "<Command-n>",
        "<Command-N>",
        "<Control-n>",
        "<Control-N>",
    )
    TAB_1 = ("<Command-1>", "<Control-1>", "<Command-KP_1>", "<Control-KP_1>")
    TAB_2 = ("<Command-2>", "<Control-2>", "<Command-KP_2>", "<Control-KP_2>")
    TAB_3 = ("<Command-3>", "<Control-3>", "<Command-KP_3>", "<Control-KP_3>")
    TAB_4 = ("<Command-4>", "<Control-4>", "<Command-KP_4>", "<Control-KP_4>")
    TAB_5 = ("<Command-5>", "<Control-5>", "<Command-KP_5>", "<Control-KP_5>")
    TAB_6 = ("<Command-6>", "<Control-6>", "<Command-KP_6>", "<Control-KP_6>")
    TAB_7 = ("<Command-7>", "<Control-7>", "<Command-KP_7>", "<Control-KP_7>")
    TAB_8 = ("<Command-8>", "<Control-8>", "<Command-KP_8>", "<Control-KP_8>")
    TAB_LAST = ("<Command-9>", "<Control-9>", "<Command-KP_9>", "<Control-KP_9>")
    COPY = (
        "<Command-c>",
        "<Command-C>",
        "<Control-c>",
        "<Control-C>",
        "<<Copy>>",
    )
    CUT = (
        "<Command-x>",
        "<Command-X>",
        "<Control-x>",
        "<Control-X>",
        "<<Cut>>",
    )
    PASTE = (
        "<Command-v>",
        "<Command-V>",
        "<Control-v>",
        "<Control-V>",
        "<<Paste>>",
    )

    @classmethod
    def _bind_sequence(
        cls,
        root: tk.Misc,
        sequence: str,
        handler: Callable[[tk.Event], str | None],
    ) -> None:
        """Bind one sequence; skip keysyms the local Tcl build rejects (e.g. Windows)."""
        try:
            root.bind_class(cls.TAG, sequence, handler)
            root.bind_all(sequence, handler, add="+")
        except tk.TclError:
            # X11-only names (ISO_Left_Tab) and some KP_*/Command forms fail on Win.
            return

    @classmethod
    def install(
        cls,
        root: tk.Misc,
        bindings: list[tuple[tuple[str, ...], Callable[[tk.Event], str | None]]],
    ) -> None:
        # bind_class + prepend so macOS web key-guard does not swallow these first.
        for sequences, handler in bindings:
            for sequence in sequences:
                cls._bind_sequence(root, sequence, handler)
        cls.refresh_bindtags(root)

    @classmethod
    def refresh_bindtags(cls, root: tk.Misc) -> None:
        cls._prepend_tag_tree(root, cls.TAG)

    @staticmethod
    def wrap(action: Callable[[], None]) -> Callable[[tk.Event], str]:
        def handler(_event: tk.Event) -> str:
            action()
            return "break"

        return handler

    @staticmethod
    def wrap_if(
        predicate: Callable[[], bool], action: Callable[[], None]
    ) -> Callable[[tk.Event], str | None]:
        def handler(_event: tk.Event) -> str | None:
            if not predicate():
                return None
            action()
            return "break"

        return handler

    @staticmethod
    def _prepend_tag_tree(widget: tk.Misc, tag: str) -> None:
        tags = widget.bindtags()
        if not tags or tags[0] != tag:
            widget.bindtags((tag, *tuple(t for t in tags if t != tag)))
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            BrowserShortcutBindings._prepend_tag_tree(child, tag)


@dataclass
class BrowserApp:
    """Mini-browser wiring Tk widgets to tkwry WebViews via RPC."""

    root: tk.Tk
    chrome_session: WebSession
    side_session: WebSession
    settings_session: WebSession
    content_session: WebSession
    store: BrowserStore
    ephemeral: bool = False
    tabs: dict[str, Tab] = field(default_factory=dict)
    selected_id: str | None = None
    _last_content_id: str | None = None
    _chrome_after: str | None = None
    _zoom: float = 1.0
    _side_visible: bool = True
    _ui_dark: bool = False
    _settings_active_section: str = "general-section"
    _ui_epoch: int = 0
    _after_ids: list[str] = field(default_factory=list)
    _url_editing: bool = False
    _closed_tabs: list[dict[str, str]] = field(default_factory=list)

    chrome: WebView = field(init=False)
    side: WebView = field(init=False)
    chrome_frame: tk.Frame = field(init=False)
    side_frame: tk.Frame = field(init=False)
    content_host: tk.Frame = field(init=False)
    content_split: ttk.Panedwindow = field(init=False)
    outer: ttk.Frame = field(init=False)
    status_var: tk.StringVar = field(init=False)

    def _alive(self) -> bool:
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def _cancel_pending_after(self) -> None:
        for after_id in list(self._after_ids):
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._after_ids.clear()

    def _schedule_after(self, ms: int, callback: Callable[[], None]) -> None:
        epoch = self._ui_epoch

        def _run() -> None:
            try:
                self._after_ids.remove(after_id)
            except ValueError:
                pass
            if epoch != self._ui_epoch or not self._alive():
                return
            callback()

        after_id = self.root.after(ms, _run)
        self._after_ids.append(after_id)

    def _safe_when_ready(self, web: WebView, callback: Callable[[], None]) -> None:
        epoch = self._ui_epoch

        def _run() -> None:
            if epoch != self._ui_epoch or not self._alive():
                return
            if web.destroyed:
                return
            callback()

        web.when_ready(_run)

    def build(self) -> None:
        self._chrome_dir, self._side_dir, self._settings_dir = _ui_asset_dirs()

        title = "tkwry browser (Private)" if self.ephemeral else "tkwry browser"
        profile_name = self.store.root.name
        if not self.ephemeral and profile_name != DEFAULT_PROFILE:
            title = f"{title} — {profile_name}"
        configure_window(
            self.root, title=title, geometry="1100x720", minsize=(720, 480)
        )

        # Native menubar on macOS/Linux; Windows relies on the in-app toolbar menu.
        if sys.platform != "win32":
            self._install_menubar()

        self.outer = ttk.Frame(self.root)
        self.outer.pack(fill="both", expand=True, padx=6, pady=6)

        self.chrome_frame = tk.Frame(self.outer, height=CHROME_HEIGHT)
        self.chrome_frame.pack(fill="x")
        self.chrome_frame.pack_propagate(False)

        self.content_split = ttk.Panedwindow(self.outer, orient="horizontal")
        self.content_split.pack(fill="both", expand=True, pady=(6, 0))
        self.content_split.bind(
            "<Configure>", self._on_content_split_configure, add="+"
        )

        self.side_frame = tk.Frame(self.content_split, width=SIDE_PANE_WIDTH)
        self.side_frame.pack_propagate(False)
        self.content_host = tk.Frame(self.content_split)
        self.content_split.add(self.side_frame, weight=0)
        self.content_split.add(self.content_host, weight=1)

        self.status_var = tk.StringVar(value="")
        status = ttk.Frame(self.root)
        status.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(fill="x")

        ui_bg = UI_BG_LIGHT
        self.chrome = WebView(
            self.chrome_frame,
            app=self._chrome_dir,
            session=self.chrome_session,
            focused=False,
            background_color=ui_bg,
            csp=CHROME_CSP,
            clipboard=True,
            initialization_script=SHORTCUT_BRIDGE_JS,
            user_agent="tkwry-browser-chrome/1.0",
            on_creation_failed=lambda exc: messagebox.showerror(
                "Chrome WebView failed", str(exc), parent=self.root
            ),
        )
        self.side = WebView(
            self.side_frame,
            app=self._side_dir,
            session=self.side_session,
            focused=False,
            background_color=ui_bg,
            csp=SIDE_CSP,
            clipboard=True,
            initialization_script=SHORTCUT_BRIDGE_JS,
            user_agent="tkwry-browser-side/1.0",
            on_creation_failed=lambda exc: messagebox.showerror(
                "Side WebView failed", str(exc), parent=self.root
            ),
        )
        self._bind_chrome_rpc()
        self._bind_side_rpc()
        self._install_shortcuts()

        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)

        self._closed_tabs = []
        self.add_tab(self.store.settings.home)
        self._safe_when_ready(self.chrome, self.push_chrome_state)
        self._safe_when_ready(self.side, self.push_side_state)
        self._schedule_chrome_refresh()
        self._schedule_after(50, lambda: self.content_split.sashpos(0, SIDE_PANE_WIDTH))
        self._schedule_after(80, self._sync_side_webview)
        self._schedule_after(120, self.push_side_state)

    def _install_menubar(self) -> None:
        mod = "Command" if sys.platform == "darwin" else "Ctrl"
        menubar = tk.Menu(self.root, tearoff=0)
        file_m = tk.Menu(menubar, tearoff=0)
        file_m.add_command(
            label="New Tab",
            accelerator=f"{mod}+T",
            command=self.new_blank_tab,
        )
        file_m.add_command(
            label="Reopen Closed Tab",
            accelerator=f"{mod}+Shift+T",
            command=self.restore_closed_tab,
        )
        file_m.add_command(
            label="New Window",
            accelerator=f"{mod}+N",
            command=self.open_new_window,
        )
        file_m.add_command(
            label="New Private Window",
            accelerator=f"{mod}+Shift+N",
            command=self.open_private_window,
        )
        file_m.add_command(
            label="Close Tab",
            accelerator=f"{mod}+W",
            command=self.close_selected,
        )
        file_m.add_separator()
        file_m.add_command(
            label="Print…",
            accelerator=f"{mod}+P",
            command=self.print_current,
        )
        file_m.add_separator()
        file_m.add_command(label="Quit", command=self.on_quit)
        menubar.add_cascade(label="File", menu=file_m)

        view_m = tk.Menu(menubar, tearoff=0)
        view_m.add_command(
            label="Back",
            accelerator="Alt+Left",
            command=self.go_back,
        )
        view_m.add_command(
            label="Forward",
            accelerator="Alt+Right",
            command=self.go_forward,
        )
        view_m.add_command(
            label="Reload",
            accelerator=f"{mod}+R",
            command=self.reload_or_stop,
        )
        view_m.add_command(label="Home", command=self.go_home)
        view_m.add_separator()
        view_m.add_command(
            label="Focus Address Bar",
            accelerator=f"{mod}+L",
            command=self.focus_url,
        )
        view_m.add_command(
            label="Toggle Side Pane",
            accelerator=f"{mod}+B",
            command=self.toggle_side_pane,
        )
        view_m.add_command(
            label="History",
            accelerator=f"{mod}+H",
            command=self.show_history_section,
        )
        view_m.add_separator()
        view_m.add_command(
            label="Zoom In",
            accelerator=f"{mod}+=",
            command=lambda: self.nudge_zoom(0.1),
        )
        view_m.add_command(
            label="Zoom Out",
            accelerator=f"{mod}+-",
            command=lambda: self.nudge_zoom(-0.1),
        )
        view_m.add_command(
            label="Reset Zoom",
            accelerator=f"{mod}+0",
            command=self.reset_zoom,
        )
        view_m.add_separator()
        view_m.add_command(
            label="Open DevTools",
            accelerator="F12",
            command=self.open_devtools,
        )
        settings_kw: dict[str, object] = {
            "label": "Settings",
            "command": self.open_settings,
        }
        if sys.platform == "darwin":
            settings_kw["accelerator"] = "Command+,"
        else:
            settings_kw["accelerator"] = "Ctrl+,"
        view_m.add_command(**settings_kw)
        menubar.add_cascade(label="View", menu=view_m)

        help_m = tk.Menu(menubar, tearoff=0)
        help_m.add_command(label="Help…", command=self.show_help)
        menubar.add_cascade(label="Help", menu=help_m)
        self.root.config(menu=menubar)

    def _install_shortcuts(self) -> None:
        wrap = BrowserShortcutBindings.wrap
        B = BrowserShortcutBindings
        BrowserShortcutBindings.install(
            self.root,
            [
                (B.NEW_TAB, wrap(self.new_blank_tab)),
                (B.RESTORE_TAB, wrap(self.restore_closed_tab)),
                (B.CLOSE_TAB, wrap(self.close_selected)),
                (B.FOCUS_URL, wrap(self.focus_url)),
                (B.RELOAD, wrap(self.reload_or_stop)),
                (B.BOOKMARK, wrap(self.toggle_favorite)),
                (B.SIDE_PANE, wrap(self.toggle_side_pane)),
                (B.HISTORY, wrap(self.show_history_section)),
                (B.BACK, wrap(self.go_back)),
                (B.FORWARD, wrap(self.go_forward)),
                (B.HOME, wrap(self.go_home)),
                (B.NEXT_TAB, wrap(lambda: self.cycle_tab(1))),
                (B.PREV_TAB, wrap(lambda: self.cycle_tab(-1))),
                (B.ZOOM_IN, wrap(lambda: self.nudge_zoom(0.1))),
                (B.ZOOM_OUT, wrap(lambda: self.nudge_zoom(-0.1))),
                (B.ZOOM_RESET, wrap(self.reset_zoom)),
                (B.PRINT, wrap(self.print_current)),
                (B.DEVTOOLS, wrap(self.open_devtools)),
                (B.SETTINGS, wrap(self.open_settings)),
                (B.PRIVATE, wrap(self.open_private_window)),
                (B.NEW_WINDOW, wrap(self.open_new_window)),
                (B.TAB_1, wrap(lambda: self.select_tab_at(0))),
                (B.TAB_2, wrap(lambda: self.select_tab_at(1))),
                (B.TAB_3, wrap(lambda: self.select_tab_at(2))),
                (B.TAB_4, wrap(lambda: self.select_tab_at(3))),
                (B.TAB_5, wrap(lambda: self.select_tab_at(4))),
                (B.TAB_6, wrap(lambda: self.select_tab_at(5))),
                (B.TAB_7, wrap(lambda: self.select_tab_at(6))),
                (B.TAB_8, wrap(lambda: self.select_tab_at(7))),
                (B.TAB_LAST, wrap(self.select_last_tab)),
                (B.COPY, B.wrap_if(lambda: self._url_editing, self._copy_url_bar)),
                (B.CUT, B.wrap_if(lambda: self._url_editing, self._cut_url_bar)),
                (B.PASTE, B.wrap_if(lambda: self._url_editing, self._paste_url_bar)),
            ],
        )

    def run_shortcut(self, name: str) -> None:
        """Dispatch a named shortcut from Tk binds or WebView JS bridges."""
        key = (name or "").strip().lower()

        def bookmark() -> None:
            self.toggle_favorite()

        actions: dict[str, Callable[[], None]] = {
            "new_tab": self.new_blank_tab,
            "restore_tab": self.restore_closed_tab,
            "close_tab": self.close_selected,
            "focus_url": self.focus_url,
            "reload": self.reload_or_stop,
            "bookmark": bookmark,
            "side_pane": self.toggle_side_pane,
            "history": self.show_history_section,
            "back": self.go_back,
            "forward": self.go_forward,
            "home": self.go_home,
            "next_tab": lambda: self.cycle_tab(1),
            "prev_tab": lambda: self.cycle_tab(-1),
            "zoom_in": lambda: self.nudge_zoom(0.1),
            "zoom_out": lambda: self.nudge_zoom(-0.1),
            "zoom_reset": self.reset_zoom,
            "print": self.print_current,
            "devtools": self.open_devtools,
            "settings": self.open_settings,
            "private": self.open_private_window,
            "new_window": self.open_new_window,
            "tab_1": lambda: self.select_tab_at(0),
            "tab_2": lambda: self.select_tab_at(1),
            "tab_3": lambda: self.select_tab_at(2),
            "tab_4": lambda: self.select_tab_at(3),
            "tab_5": lambda: self.select_tab_at(4),
            "tab_6": lambda: self.select_tab_at(5),
            "tab_7": lambda: self.select_tab_at(6),
            "tab_8": lambda: self.select_tab_at(7),
            "tab_last": self.select_last_tab,
        }
        fn = actions.get(key)
        if fn is not None:
            fn()

    def _paste_url_bar(self) -> None:
        if self.chrome.destroyed or not self.chrome.ready:
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        try:
            self.chrome.focus()
        except Exception:
            pass
        self.chrome.eval_js(
            f"window.chromePasteUrl && window.chromePasteUrl({json.dumps(text)});"
        )

    def _copy_url_bar(self) -> None:
        if self.chrome.destroyed or not self.chrome.ready:
            return

        def on_text(raw: str) -> None:
            try:
                text = json.loads(raw) if isinstance(raw, str) else str(raw)
            except (json.JSONDecodeError, TypeError):
                text = str(raw or "")
            if not text:
                return
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
            except tk.TclError:
                pass

        self.chrome.eval_js_with_callback(
            "(window.chromeCopyUrl && window.chromeCopyUrl()) || ''",
            on_text,
        )

    def _cut_url_bar(self) -> None:
        if self.chrome.destroyed or not self.chrome.ready:
            return

        def on_text(raw: str) -> None:
            try:
                text = json.loads(raw) if isinstance(raw, str) else str(raw)
            except (json.JSONDecodeError, TypeError):
                text = str(raw or "")
            if not text:
                return
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
            except tk.TclError:
                pass

        self.chrome.eval_js_with_callback(
            "(window.chromeCutUrl && window.chromeCutUrl()) || ''",
            on_text,
        )

    def _clipboard_get_text(self) -> str:
        try:
            return str(self.root.clipboard_get())
        except tk.TclError:
            return ""

    def _clipboard_set_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(text))
        except tk.TclError:
            pass

    def _expose_clipboard(self, web: WebView, *, allow_any_origin: bool = False) -> None:
        """Expose Tk pasteboard helpers; content tabs need ``allow_any_origin``."""
        any_origin = allow_any_origin or getattr(web, "bridge_origins", None) == "*"

        @web.expose(allow_any_origin=any_origin)
        def clipboard_get() -> str:
            return self._clipboard_get_text()

        @web.expose(allow_any_origin=any_origin)
        def clipboard_set(text: str = "") -> None:
            self._clipboard_set_text(text)

    def _bind_chrome_rpc(self) -> None:
        chrome = self.chrome
        self._expose_clipboard(chrome)

        @chrome.expose
        def get_state() -> dict[str, Any]:
            return self.chrome_state()

        @chrome.expose
        def navigate(url: str = "") -> None:
            self.navigate_from_chrome(str(url))

        @chrome.expose
        def go_back() -> None:
            self.go_back()

        @chrome.expose
        def go_forward() -> None:
            self.go_forward()

        @chrome.expose
        def reload_or_stop() -> None:
            self.reload_or_stop()

        @chrome.expose
        def go_home() -> None:
            self.go_home()

        @chrome.expose
        def new_tab(url: str | None = None) -> None:
            if url:
                self.add_tab(str(url))
            else:
                self.new_blank_tab()

        @chrome.expose
        def close_tab(tab_id: str = "") -> None:
            if tab_id:
                self.close_tab(str(tab_id))

        @chrome.expose
        def select_tab(tab_id: str = "") -> None:
            if tab_id:
                self.select_tab(str(tab_id))

        @chrome.expose
        def reorder_tabs(order: list[str] | None = None) -> None:
            self.reorder_tabs([str(x) for x in (order or [])])

        @chrome.expose
        def toggle_favorite() -> bool:
            return self.toggle_favorite()

        @chrome.expose
        def open_profile_menu(x: int = 0, y: int = 0) -> None:
            xx, yy = int(x), int(y)
            self._schedule_after(0, lambda: self.open_profile_menu(xx, yy))

        @chrome.expose
        def open_app_menu(x: int = 0, y: int = 0) -> None:
            xx, yy = int(x), int(y)
            self._schedule_after(0, lambda: self.open_app_menu(xx, yy))

        @chrome.expose
        def set_ui_theme(dark: bool = False) -> None:
            self.apply_ui_theme(bool(dark))

        @chrome.expose
        def url_editing(active: bool = False) -> None:
            self._url_editing = bool(active)

        @chrome.expose
        def run_shortcut(name: str = "") -> None:
            if name:
                self.run_shortcut(str(name))

    def _bind_side_rpc(self) -> None:
        side = self.side
        self._expose_clipboard(side)

        @side.expose
        def get_state() -> dict[str, Any]:
            return self.side_state()

        @side.expose
        def open_url(url: str = "") -> None:
            if url:
                self.navigate_current(str(url))

        @side.expose
        def open_bookmark_menu(
            node_id: str = "", kind: str = "root", x: int = 0, y: int = 0
        ) -> None:
            nid = str(node_id) or None
            k = str(kind)
            xx, yy = int(x), int(y)
            self._schedule_after(0, lambda: self._popup_bookmarks_menu(nid, k, xx, yy))

        @side.expose
        def open_history_menu(
            entry_id: str = "", url: str = "", x: int = 0, y: int = 0
        ) -> None:
            eid = str(entry_id) or None
            u = str(url)
            xx, yy = int(x), int(y)
            self._schedule_after(0, lambda: self._popup_history_menu(eid, u, xx, yy))

        @side.expose
        def open_settings_section(section_id: str = "") -> None:
            if section_id:
                self.scroll_settings_section(str(section_id))

        @side.expose
        def set_ui_theme(dark: bool = False) -> None:
            self.apply_ui_theme(bool(dark))

        @side.expose
        def run_shortcut(name: str = "") -> None:
            if name:
                self.run_shortcut(str(name))

    # ----- chrome sync -----

    def chrome_state(self) -> dict[str, Any]:
        tab = self.current_tab()
        url = ""
        can_back = False
        can_forward = False
        loading = False
        is_settings_tab = False
        if tab is not None and (
            tab.kind == "settings" or self.selected_id == SETTINGS_TAB_ID
        ):
            url = SETTINGS_TAB_ID
            is_settings_tab = True
        elif tab is not None and tab.web is not None and not tab.web.destroyed:
            try:
                state = tab.web.get_state()
                url = state.url or ""
                can_back = state.can_go_back
                can_forward = state.can_go_forward
                loading = state.loading
                tab.loading = loading
                self._zoom = state.zoom
            except Exception:
                url = tab.web.url or ""
                loading = tab.loading
        sec_kind, sec_title = security_indicator(
            url if tab and tab.web and not is_settings_tab else None
        )
        return {
            "tabs": [
                {
                    "id": tid,
                    "title": tab_label(t.title),
                    "loading": t.loading,
                    **({"icon": icon} if (icon := tab_icon(tid, t)) else {}),
                }
                for tid, t in self.tabs.items()
            ],
            "active": self.selected_id,
            "url": url,
            "canGoBack": can_back,
            "canGoForward": can_forward,
            "loading": loading,
            "security": sec_kind,
            "securityTitle": sec_title,
            "isFavorite": self.store.is_favorite(url)
            if url and tab and tab.web and not is_settings_tab
            else False,
            "zoom": self._zoom,
        }

    def push_chrome_state(self) -> None:
        if self.chrome.destroyed or not self.chrome.ready:
            return
        try:
            self.chrome.emit("state", self.chrome_state())
        except Exception:
            pass

    def side_state(self) -> dict[str, Any]:
        tab = self.current_tab()
        if tab is not None and tab.kind == "settings":
            return {
                "mode": "settings",
                "active_section": self._settings_active_section,
                "tree": [
                    {
                        "id": section_id,
                        "kind": "settings",
                        "title": title,
                    }
                    for section_id, title in SETTINGS_SECTIONS
                ],
            }
        return {
            "mode": "library",
            "tree": [
                {
                    "id": "__bookmarks__",
                    "kind": "section",
                    "title": "Bookmarks",
                    "children": _bookmark_tree_payload(self.store.bookmarks),
                },
                {
                    "id": "__history__",
                    "kind": "section",
                    "title": "History",
                    "children": _history_tree_payload(self.store.history),
                },
            ],
        }

    def scroll_settings_section(self, section_id: str) -> None:
        if not self._alive():
            return
        valid = {sid for sid, _ in SETTINGS_SECTIONS}
        if section_id not in valid:
            return
        self._settings_active_section = section_id
        settings_tab = self.tabs.get(SETTINGS_TAB_ID)
        if (
            settings_tab is not None
            and settings_tab.web is not None
            and settings_tab.web.ready
        ):
            try:
                settings_tab.web.eval_js(
                    "var el=document.getElementById("
                    + json.dumps(section_id)
                    + ");if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}"
                )
            except Exception:
                pass
        self.push_side_state()

    def push_side_state(self) -> None:
        if self.side.destroyed or not self.side.ready:
            return
        try:
            self.side.emit("state", self.side_state())
        except Exception:
            pass
        # Bookmark shortcuts on NTP track the same store as the side tree.
        self.push_ntp_states()

    def ntp_state(self, *, focus: bool = False) -> dict[str, Any]:
        shortcuts = _flatten_bookmark_links(self.store.bookmarks, limit=8)
        if not shortcuts:
            shortcuts = [
                {"title": title, "url": url} for title, url in DEFAULT_BOOKMARKS
            ]
        return {
            "dark": self._ui_dark,
            "shortcuts": shortcuts,
            "focus": focus,
        }

    def push_ntp_state(self, web: WebView, *, focus: bool = False) -> None:
        if web.destroyed or not web.ready:
            return
        try:
            web.emit("ntp", self.ntp_state(focus=focus))
        except Exception:
            pass

    def push_ntp_states(self) -> None:
        for tab in self.tabs.values():
            if tab.kind != "ntp" or tab.web is None:
                continue
            self.push_ntp_state(tab.web, focus=False)

    def apply_ui_theme(self, dark: bool) -> None:
        if dark == self._ui_dark:
            return
        self._ui_dark = dark
        color = UI_BG_DARK if dark else UI_BG_LIGHT
        for web in (self.chrome, self.side):
            if web.destroyed or not web.ready:
                continue
            try:
                web.set_background_color(*color)
            except Exception:
                pass
        settings_tab = self.tabs.get(SETTINGS_TAB_ID)
        if (
            settings_tab is not None
            and settings_tab.web is not None
            and not settings_tab.web.destroyed
            and settings_tab.web.ready
        ):
            try:
                settings_tab.web.set_background_color(*color)
            except Exception:
                pass
        for tab in self.tabs.values():
            if tab.kind != "ntp" or tab.web is None or tab.web.destroyed:
                continue
            if not tab.web.ready:
                continue
            try:
                tab.web.set_background_color(*color)
            except Exception:
                pass
        self.push_ntp_states()

    def _schedule_chrome_refresh(self) -> None:
        if not self._alive():
            return
        self.push_chrome_state()
        epoch = self._ui_epoch

        def _tick() -> None:
            self._chrome_after = None
            if epoch != self._ui_epoch or not self._alive():
                return
            self._schedule_chrome_refresh()

        self._chrome_after = self.root.after(350, _tick)

    def focus_url(self) -> None:
        if self.chrome.ready:
            self._url_editing = True
            self.chrome.eval_js(
                "var i=document.getElementById('url');if(i){i.focus();i.select();}"
            )

    # ----- side pane -----

    def refresh_side_pane(self) -> None:
        self.push_side_state()

    def toggle_side_pane(self) -> None:
        if self._side_visible:
            self.hide_side_pane()
        else:
            self.show_side_pane()

    def _side_pane_attached(self) -> bool:
        try:
            return str(self.side_frame) in self.content_split.panes()
        except tk.TclError:
            return False

    def hide_side_pane(self) -> None:
        if not self._side_visible:
            return
        try:
            self.content_split.forget(self.side_frame)
        except tk.TclError:
            pass
        self._side_visible = False

    def show_side_pane(self) -> None:
        if self._side_visible and self._side_pane_attached():
            return
        # Re-add panes only when truly missing — forget/re-add blanks embedded
        # WKWebViews on macOS if done after create.
        panes = []
        try:
            panes = list(self.content_split.panes())
        except tk.TclError:
            pass
        if str(self.side_frame) not in panes or str(self.content_host) not in panes:
            for pane in panes:
                try:
                    self.content_split.forget(pane)
                except tk.TclError:
                    pass
            self.content_split.add(self.side_frame, weight=0)
            self.content_split.add(self.content_host, weight=1)
        self._side_visible = True
        self._schedule_after(20, lambda: self.content_split.sashpos(0, SIDE_PANE_WIDTH))
        self._schedule_after(40, self._sync_side_webview)
        self._schedule_after(60, self.push_side_state)

    def _sync_side_webview(self) -> None:
        if self.side.destroyed or not self.side.ready:
            return
        try:
            self.side.sync_bounds()
        except Exception:
            pass

    def _repair_side_pane(self) -> None:
        if not self._side_visible:
            return
        # Only fix sash width. Do not forget/re-add panes here — that destroys
        # native WebView attachment on startup Configure storms.
        if not self._side_pane_attached():
            return
        try:
            if self.side_frame.winfo_width() < SIDE_PANE_WIDTH // 2:
                self.content_split.sashpos(0, SIDE_PANE_WIDTH)
                self._schedule_after(0, self._sync_side_webview)
        except tk.TclError:
            pass

    def _on_content_split_configure(self, event: tk.Event) -> None:
        if event.widget is not self.content_split:
            return
        self._schedule_after(0, self._repair_side_pane)

    def _sync_embedded_webviews(self) -> None:
        if sys.platform == "darwin":
            try:
                from tkwry._macos import sync_mac_webview_layout

                sync_mac_webview_layout(self.root, devtools_web=self.content_web())
                return
            except Exception:
                pass
        for web in (self.chrome, self.side):
            if web.ready and not web.destroyed:
                web.sync_bounds()
        web = self.content_web()
        if web is not None and web.ready:
            web.sync_bounds()

    def show_history_section(self) -> None:
        self.show_side_pane()
        if self.side.destroyed or not self.side.ready:
            return
        try:
            payload = self.side_state()
            payload["focusOpen"] = ["__history__", "hist-day:today"]
            self.side.emit("state", payload)
        except Exception:
            pass

    def _side_menu_point(self, x: int, y: int) -> tuple[int, int]:
        try:
            ox = self.side_frame.winfo_rootx()
            oy = self.side_frame.winfo_rooty()
            if x > 0 or y > 0:
                return ox + int(x), oy + int(y)
            return ox + 12, oy + 40
        except tk.TclError:
            return self.root.winfo_rootx() + 40, self.root.winfo_rooty() + 120

    def _current_page_bookmark(self) -> tuple[str, str] | None:
        web = self.content_web()
        if web is None:
            return None
        url = (web.url or "").strip()
        if not url or url in ("about:blank",):
            return None
        tab = (
            self.tabs.get(self._last_content_id or "")
            if self._last_content_id
            else None
        )
        title = (tab.title if tab else url).strip() or url
        return url, title

    def _add_current_bookmark(self, parent_id: str = "") -> None:
        page = self._current_page_bookmark()
        if page is None:
            self.status_var.set("No page to bookmark")
            return
        url, title = page
        if self.store.is_favorite(url):
            self.status_var.set("Already bookmarked")
            return
        self.store.add_bookmark(parent_id, url, title)
        self.push_side_state()
        self.push_chrome_state()
        self.status_var.set("Bookmark added")

    def _popup_bookmarks_menu(
        self, node_id: str | None, kind: str, x: int, y: int
    ) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        page = self._current_page_bookmark()
        node = self.store.node_by_id(node_id) if node_id else None

        if kind == "bookmark" and node is not None and node.url:
            url = node.url
            menu.add_command(label="Open", command=lambda: self.navigate_current(url))
            menu.add_command(label="Open in New Tab", command=lambda: self.add_tab(url))
            menu.add_separator()
            menu.add_command(
                label="Rename…",
                command=lambda: self._rename_bookmark_node(node_id or ""),
            )
            menu.add_command(
                label="Delete",
                command=lambda: self._delete_bookmark_node(node_id or ""),
            )
        elif kind == "folder" and node_id:
            menu.add_command(
                label="New Folder…",
                command=lambda: self._new_bookmark_folder(node_id),
            )
            if page is not None:
                menu.add_command(
                    label="Add Current Page",
                    command=lambda: self._add_current_bookmark(node_id),
                )
            menu.add_separator()
            menu.add_command(
                label="Rename…",
                command=lambda: self._rename_bookmark_node(node_id),
            )
            menu.add_command(
                label="Delete",
                command=lambda: self._delete_bookmark_node(node_id),
            )
        else:
            menu.add_command(
                label="New Folder…",
                command=lambda: self._new_bookmark_folder(""),
            )
            if page is not None:
                menu.add_command(
                    label="Add Current Page",
                    command=lambda: self._add_current_bookmark(""),
                )

        px, py = self._side_menu_point(x, y)
        try:
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _new_bookmark_folder(self, parent_id: str) -> None:
        title = simpledialog.askstring("New Folder", "Folder name:", parent=self.root)
        if not title:
            return
        self.store.add_folder(parent_id, title)
        self.push_side_state()
        self.status_var.set("Folder created")

    def _rename_bookmark_node(self, node_id: str) -> None:
        node = self.store.node_by_id(node_id)
        if node is None:
            return
        title = simpledialog.askstring(
            "Rename",
            "Name:",
            initialvalue=node.title,
            parent=self.root,
        )
        if not title:
            return
        self.store.rename_node(node_id, title)
        self.push_side_state()

    def _delete_bookmark_node(self, node_id: str) -> None:
        node = self.store.node_by_id(node_id)
        if node is None:
            return
        label = "folder" if node.kind == "folder" else "bookmark"
        if not messagebox.askyesno(
            "Delete",
            f"Delete this {label}?",
            parent=self.root,
        ):
            return
        self.store.remove_node(node_id)
        self.push_side_state()
        self.push_chrome_state()
        self.status_var.set("Bookmark deleted")

    def _popup_history_menu(
        self, entry_id: str | None, url: str, x: int, y: int
    ) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        if url:
            menu.add_command(label="Open", command=lambda: self.navigate_current(url))
            menu.add_command(label="Open in New Tab", command=lambda: self.add_tab(url))
            menu.add_separator()
        menu.add_command(label="Clear History…", command=self._clear_history)
        px, py = self._side_menu_point(x, y)
        try:
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _clear_history(self) -> None:
        if not messagebox.askyesno(
            "Clear History", "Clear all browsing history?", parent=self.root
        ):
            return
        self.store.clear_history()
        self.push_side_state()
        self.status_var.set("History cleared")

    # ----- content tabs -----

    def current_tab(self) -> Tab | None:
        return self.tabs.get(self.selected_id) if self.selected_id else None

    def content_web(self) -> WebView | None:
        """Active content WebView (skips Settings tab)."""
        tab = self.current_tab()
        if (
            tab is not None
            and tab.kind != "settings"
            and tab.web is not None
            and not tab.web.destroyed
        ):
            return tab.web
        if self._last_content_id:
            prev = self.tabs.get(self._last_content_id)
            if (
                prev is not None
                and prev.kind != "settings"
                and prev.web is not None
                and not prev.web.destroyed
            ):
                return prev.web
        for t in self.tabs.values():
            if t.kind != "settings" and t.web is not None and not t.web.destroyed:
                return t.web
        return None

    def new_blank_tab(self) -> None:
        self.add_tab(BLANK_TAB_URL)

    def new_tab_home(self) -> None:
        self.add_tab(self.store.settings.home)

    def navigate_from_chrome(self, raw: str) -> None:
        if (raw or "").strip() in (SETTINGS_TAB_ID, "settings"):
            self.open_settings()
            return
        tab = self.current_tab()
        if tab is not None and tab.kind == "settings":
            self.add_tab(
                normalize_input(
                    raw,
                    home=self.store.settings.home,
                    search_url=self.store.settings.search_url,
                )
            )
            return
        target = normalize_input(
            raw,
            home=self.store.settings.home,
            search_url=self.store.settings.search_url,
        )
        self.navigate_current(target)

    def navigate_current(self, url: str) -> None:
        if _is_ntp_url(url):
            self.show_ntp()
            return
        tab = self.current_tab()
        if tab is None or tab.web is None:
            self.add_tab(url)
            return
        if tab.kind == "settings":
            self.add_tab(url)
            return
        try:
            tab.web.load_url(url)
        except ValueError as exc:
            messagebox.showerror("Invalid URL", str(exc), parent=self.root)
            return
        tab.kind = "content"
        self.status_var.set(f"Loading {_short_status_url(url)}…")
        self.push_chrome_state()

    def show_ntp(self) -> None:
        """Show the New Tab start page in the current content tab (or a new one)."""
        tab = self.current_tab()
        if tab is None or tab.web is None or tab.kind == "settings":
            self.add_tab(BLANK_TAB_URL)
            return
        html = _blank_tab_html(dark=self._ui_dark)
        try:
            tab.web.load_html(html)
        except Exception:
            self.add_tab(BLANK_TAB_URL)
            return
        tab.kind = "ntp"
        tab.title = "New Tab"
        tab.loading = True
        self.status_var.set("New Tab")
        self.push_chrome_state()
        self.push_side_state()

    def add_tab(self, url: str) -> Tab:
        frame = tk.Frame(self.content_host)
        tab_id = str(frame)
        blank = _is_ntp_url(url)
        web = self._create_content_webview(frame, url, tab_id)
        tab = Tab(frame=frame, web=web, kind="ntp" if blank else "content")
        self.tabs[tab_id] = tab
        self.select_tab(tab_id)
        BrowserShortcutBindings.refresh_bindtags(self.root)
        return tab

    def select_tab(self, tab_id: str) -> None:
        if tab_id not in self.tabs:
            return
        self.selected_id = tab_id
        tab = self.tabs[tab_id]
        if tab.web is not None and tab.kind != "settings":
            self._last_content_id = tab_id
        for tid, item in self.tabs.items():
            if tid == tab_id:
                item.frame.pack(fill="both", expand=True)
                if item.web is not None:
                    item.web.sync_bounds()
            else:
                item.frame.pack_forget()
        if tab.kind == "settings":
            if tab.web is not None and tab.web.ready:
                self.push_settings_state()
                self.scroll_settings_section(self._settings_active_section)
            elif tab.web is not None:
                self._safe_when_ready(tab.web, self.push_settings_state)
                self._safe_when_ready(
                    tab.web,
                    lambda: self.scroll_settings_section(self._settings_active_section),
                )
        elif tab.kind == "ntp" and tab.web is not None:
            if tab.web.ready:
                self.push_ntp_state(tab.web, focus=True)
            else:
                self._safe_when_ready(
                    tab.web, lambda: self.push_ntp_state(tab.web, focus=True)
                )
        self._refresh_status()
        self.push_chrome_state()
        self.push_side_state()

    def select_tab_at(self, index: int) -> None:
        ids = list(self.tabs)
        if 0 <= index < len(ids):
            self.select_tab(ids[index])

    def select_last_tab(self) -> None:
        ids = list(self.tabs)
        if ids:
            self.select_tab(ids[-1])

    def reorder_tabs(self, order: list[str]) -> None:
        """Reorder ``self.tabs`` to match chrome drag-and-drop order."""
        if not order:
            return
        seen: set[str] = set()
        ordered: dict[str, Tab] = {}
        for tid in order:
            if tid in self.tabs and tid not in seen:
                ordered[tid] = self.tabs[tid]
                seen.add(tid)
        for tid, tab in self.tabs.items():
            if tid not in seen:
                ordered[tid] = tab
        if list(ordered) == list(self.tabs):
            return
        self.tabs = ordered
        self.push_chrome_state()

    def cycle_tab(self, delta: int) -> None:
        ids = list(self.tabs)
        if not ids:
            return
        if self.selected_id in ids:
            idx = ids.index(self.selected_id)
        else:
            idx = 0
        self.select_tab(ids[(idx + delta) % len(ids)])

    def close_tab(self, tab_id: str) -> None:
        tab = self.tabs.get(tab_id)
        if tab is None:
            return
        snap = self._snapshot_tab(tab_id, tab)
        ids = list(self.tabs)
        idx = ids.index(tab_id)
        was_selected = self.selected_id == tab_id
        if tab.web is not None:
            tab.web.destroy()
        tab.frame.destroy()
        del self.tabs[tab_id]
        if self._last_content_id == tab_id:
            self._last_content_id = None
        if snap is not None:
            self._closed_tabs.insert(0, snap)
            self._closed_tabs = self._closed_tabs[:MAX_CLOSED_TABS]
        if not self.tabs:
            self.new_blank_tab()
            return
        if was_selected:
            nxt = ids[idx + 1] if idx + 1 < len(ids) else ids[idx - 1]
            if nxt in self.tabs:
                self.select_tab(nxt)
            else:
                self.selected_id = None
                self.push_chrome_state()
        else:
            self.push_chrome_state()

    def close_selected(self) -> None:
        if self.selected_id:
            self.close_tab(self.selected_id)

    def _snapshot_tab(self, tab_id: str, tab: Tab) -> dict[str, str] | None:
        if tab.kind == "settings" or tab_id == SETTINGS_TAB_ID:
            return {"kind": "settings", "title": "Settings", "url": ""}
        url = ""
        if tab.web is not None and not tab.web.destroyed:
            try:
                url = str(tab.web.url or "")
            except Exception:
                url = ""
        if tab.kind == "ntp" or _is_ntp_url(url):
            return {
                "kind": "ntp",
                "title": tab.title or "New Tab",
                "url": BLANK_TAB_URL,
            }
        if not url:
            return None
        return {
            "kind": "content",
            "title": tab.title or url,
            "url": url,
        }

    def _open_session_entry(self, entry: dict[str, str]) -> None:
        kind = str(entry.get("kind") or "content")
        url = str(entry.get("url") or "").strip()
        if kind == "settings":
            self.open_settings()
            return
        if kind == "ntp" or _is_ntp_url(url):
            self.add_tab(BLANK_TAB_URL)
            return
        if url:
            self.add_tab(url)
        else:
            self.add_tab(BLANK_TAB_URL)

    def restore_closed_tab(self) -> None:
        if not self._closed_tabs:
            self.status_var.set("No recently closed tabs")
            return
        entry = self._closed_tabs.pop(0)
        self._open_session_entry(entry)
        self.status_var.set(f"Reopened {entry.get('title') or 'tab'}")

    def _create_content_webview(
        self, frame: tk.Frame, url: str, tab_id: str
    ) -> WebView:
        def on_title(title: str) -> None:
            tab = self.tabs.get(tab_id)
            if tab is None:
                return
            cleaned = (title or "").strip()
            if cleaned.lower() in ("about:blank", "new tab", ""):
                tab.title = "New Tab"
            else:
                tab.title = cleaned or "New Tab"
            if self.selected_id == tab_id and not tab.loading:
                self._refresh_status()
            self.push_chrome_state()

        def on_page_load(event: PageLoadEvent, page_url: str) -> None:
            tab = self.tabs.get(tab_id)
            if tab is None:
                return
            if event is PageLoadEvent.Started:
                tab.loading = True
            elif event is PageLoadEvent.Finished:
                tab.loading = False
                if page_url and not _is_ntp_url(page_url):
                    tab.kind = "content"
                    self.store.record_history(page_url, tab.title)
                    self.push_side_state()
                elif tab.kind == "ntp" and tab.web is not None:
                    self.push_ntp_state(tab.web, focus=True)
            if self.selected_id == tab_id:
                self._refresh_status(page_url=page_url or None)
            self.push_chrome_state()

        def on_download(download: Download) -> str | bool:
            dest_dir = Path(self.store.settings.download_dir).expanduser()
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save download",
                initialdir=str(dest_dir),
                initialfile=download.suggested_filename,
            )
            if not path:
                self.status_var.set("Download cancelled")
                return False
            abs_path = str(Path(path).expanduser().resolve())
            self.status_var.set(f"Downloading → {Path(abs_path).name}")
            return abs_path

        def on_download_complete(url: str, dest: str | None, success: bool) -> None:
            if success:
                name = Path(dest or url).name
                self.status_var.set(f"Saved {name}")
                self.content_session.emit_all("notice", {"text": f"Saved {name}"})
            else:
                self.status_var.set("Download failed")

        def on_drop(event: DragDropEvent, paths: list[str], _pos: object) -> None:
            if event is not DragDropEvent.Drop or not paths:
                return
            for path in paths:
                p = Path(path)
                if p.suffix.lower() in {".html", ".htm", ".svg", ".xhtml"}:
                    self.add_tab(p.resolve().as_uri())

        def on_ipc(message: str) -> None:
            try:
                payload = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(payload, dict) or payload.get("__tkwry"):
                return
            action = payload.get("action")
            tab = self.tabs.get(tab_id)
            if action == "newtab":
                href = str(payload.get("href") or "").strip()
                if href:
                    self.add_tab(href)
            elif action == "shortcut":
                name = str(payload.get("name") or "").strip()
                if name:
                    self.run_shortcut(name)
            elif action == "ntp_ready":
                if tab is not None and tab.web is not None:
                    self.push_ntp_state(tab.web, focus=True)
            elif action == "navigate":
                raw = str(payload.get("q") or payload.get("href") or "").strip()
                if not raw:
                    return
                target = normalize_input(
                    raw,
                    home=self.store.settings.home,
                    search_url=self.store.settings.search_url,
                )
                if tab is None or tab.web is None or tab.web.destroyed:
                    self.add_tab(target)
                    return
                try:
                    tab.web.load_url(target)
                except ValueError as exc:
                    messagebox.showerror("Invalid URL", str(exc), parent=self.root)
                    return
                tab.kind = "content"
                self.status_var.set(f"Loading {_short_status_url(target)}…")
                self.push_chrome_state()
            elif action == "clipboard_set":
                self._clipboard_set_text(str(payload.get("text") or ""))
            elif action == "clipboard_get":
                req_id = str(payload.get("id") or "")
                text = self._clipboard_get_text()
                if tab is not None and tab.web is not None and not tab.web.destroyed:
                    try:
                        tab.web.emit(
                            "clipboard", {"id": req_id, "text": text}
                        )
                    except Exception:
                        pass

        def permission_handler(kind: PermissionKind) -> PermissionResponse:
            if kind is PermissionKind.ClipboardRead:
                return PermissionResponse.Allow
            return PermissionResponse.Default

        blank = _is_ntp_url(url)
        bg = UI_BG_DARK if self._ui_dark else UI_BG_LIGHT
        web_kwargs: dict[str, Any] = {
            "session": self.content_session,
            "focused": False,
            "bridge_origins": "*",
            "ipc_handler": on_ipc,
            "initialization_script": LINK_HELPER_JS,
            "on_title_changed": on_title,
            "on_page_load": on_page_load,
            "on_new_window": lambda _url: NewWindowResponse.Deny,
            "on_creation_failed": lambda exc: messagebox.showerror(
                "WebView failed", str(exc), parent=self.root
            ),
            "on_download": on_download,
            "on_download_started": lambda d: self.status_var.set(
                f"Download started: {d.suggested_filename}"
            ),
            "on_download_complete": on_download_complete,
            "on_download_failed": lambda u, _d: self.status_var.set(
                f"Download failed: {u}"
            ),
            "drag_drop_handler": on_drop,
            "permission_handler": permission_handler,
            "on_context_menu": self._popup_context_menu,
            "default_context_menus": False,
            "devtools": True,
            "hotkeys_zoom": True,
            "back_forward_gestures": True,
            "clipboard": True,
            "background_color": bg,
            "user_agent": "tkwry-browser-demo/1.0",
        }
        if blank:
            web_kwargs["html"] = _blank_tab_html(dark=self._ui_dark)
        else:
            web_kwargs["url"] = url
        web = WebView(frame, **web_kwargs)
        self._expose_clipboard(web)
        return web

    # ----- navigation actions -----

    def go_back(self) -> None:
        web = self.content_web()
        if web is not None and web.ready and web.can_go_back():
            web.go_back()

    def go_forward(self) -> None:
        web = self.content_web()
        if web is not None and web.ready and web.can_go_forward():
            web.go_forward()

    def reload_or_stop(self) -> None:
        tab = self.current_tab()
        if tab is None or tab.web is None or not tab.web.ready:
            return
        if tab.loading:
            tab.web.eval_js("window.stop();")
            tab.loading = False
            self.status_var.set("Stopped")
            self.push_chrome_state()
            return
        # NTP is html= content (no real URL); native reload() clears to empty.
        if tab.kind == "ntp" or _is_ntp_url(getattr(tab.web, "url", None)):
            self.show_ntp()
            return
        tab.web.reload()

    def go_home(self) -> None:
        home = (self.store.settings.home or "").strip() or DEFAULT_HOME
        if _is_ntp_url(home):
            self.show_ntp()
        else:
            self.navigate_current(home)

    def nudge_zoom(self, delta: float) -> None:
        web = self.content_web()
        if web is None or not web.ready:
            return
        self._zoom = max(0.25, min(3.0, round(self._zoom + delta, 2)))
        web.set_zoom(self._zoom)
        self.status_var.set(f"Zoom {int(self._zoom * 100)}%")
        self.push_chrome_state()

    def reset_zoom(self) -> None:
        web = self.content_web()
        if web is None or not web.ready:
            return
        self._zoom = 1.0
        web.set_zoom(1.0)
        self.status_var.set("Zoom 100%")
        self.push_chrome_state()

    def toggle_favorite(self) -> bool:
        web = self.content_web()
        if web is None:
            return False
        url = web.url or ""
        if not url:
            return False
        tab = (
            self.tabs.get(self._last_content_id)
            if self._last_content_id
            else self.current_tab()
        )
        title = tab.title if tab else url
        starred = self.store.toggle_favorite(url, title)
        self.push_side_state()
        self.push_chrome_state()
        self.status_var.set("Bookmarked" if starred else "Bookmark removed")
        return starred

    def clear_browsing_data(self) -> None:
        web = self.content_web()
        if web is None or not web.ready:
            self.status_var.set("No content tab to clear")
            return
        web.clear_all_browsing_data()
        self.status_var.set("Browsing data cleared")
        self.content_session.emit_all("notice", {"text": "Browsing data cleared"})
        self.push_settings_state()

    def open_profile_menu(self, x: int = 0, y: int = 0) -> None:
        """Show a Tk popup listing available profiles to switch to."""
        menu = tk.Menu(self.root, tearoff=0)
        current = self.store.root.name
        profiles = list_profile_names()

        for name in profiles:
            label = f"● {name}" if name == current else name
            menu.add_command(
                label=label,
                command=lambda n=name: self._switch_profile(n),
                state="disabled" if name == current else "normal",
            )

        menu.add_separator()
        menu.add_command(
            label="New Profile…",
            command=lambda: self.open_settings(focus_profiles=True),
        )

        try:
            ox = self.chrome_frame.winfo_rootx()
            oy = self.chrome_frame.winfo_rooty()
            if x > 0 or y > 0:
                px = ox + int(x)
                py = oy + int(y)
            else:
                px = ox + max(0, self.chrome_frame.winfo_width() - 40)
                py = oy + self.chrome_frame.winfo_height()
        except tk.TclError:
            px = self.root.winfo_rootx() + 40
            py = self.root.winfo_rooty() + 80
        try:
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def _destroy_app_ui(self) -> None:
        self._ui_epoch += 1
        self._cancel_pending_after()
        if self._chrome_after is not None:
            try:
                self.root.after_cancel(self._chrome_after)
            except tk.TclError:
                pass
            self._chrome_after = None
        for tab_id in list(self.tabs):
            tab = self.tabs[tab_id]
            if tab.web is not None:
                try:
                    tab.web.destroy()
                except Exception:
                    pass
            try:
                tab.frame.destroy()
            except tk.TclError:
                pass
        self.tabs.clear()
        self.selected_id = None
        self._last_content_id = None
        for web in (getattr(self, "chrome", None), getattr(self, "side", None)):
            if web is not None:
                try:
                    web.destroy()
                except Exception:
                    pass
        for session in (
            getattr(self, "content_session", None),
            getattr(self, "settings_session", None),
            getattr(self, "side_session", None),
            getattr(self, "chrome_session", None),
        ):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        self.root.config(menu="")
        for child in list(self.root.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass

    def _switch_profile(self, name: str) -> None:
        """Reload the current window with the chosen profile."""
        if self.ephemeral:
            messagebox.showinfo(
                "Profiles",
                "Profile switching is not available in private windows.",
                parent=self.root,
            )
            return
        name = sanitize_profile_name(name)
        if name == self.store.root.name:
            return
        geometry = self.root.geometry()
        profile = profile_dir(name)
        profile.mkdir(parents=True, exist_ok=True)
        self._destroy_app_ui()
        self.chrome_session = WebSession(data_directory=profile / "chrome")
        self.side_session = WebSession(data_directory=profile / "side")
        self.settings_session = WebSession(data_directory=profile / "settings")
        self.content_session = WebSession(data_directory=profile / "webview")
        self.store = BrowserStore(profile)
        self._side_visible = True
        self._ui_dark = False
        self._zoom = 1.0
        self.build()
        self.root.geometry(geometry)
        self.status_var.set(f"Switched to profile “{name}”")

    def open_settings(self, *, focus_profiles: bool = False) -> None:
        self.show_side_pane()
        if focus_profiles:
            self._settings_active_section = "profiles-section"
        if SETTINGS_TAB_ID in self.tabs:
            self.select_tab(SETTINGS_TAB_ID)
            if focus_profiles:
                self._schedule_after(
                    100, lambda: self.scroll_settings_section("profiles-section")
                )
            return
        frame = tk.Frame(self.content_host)
        web = WebView(
            frame,
            app=self._settings_dir,
            session=self.settings_session,
            focused=False,
            background_color=UI_BG_DARK if self._ui_dark else UI_BG_LIGHT,
            csp=SETTINGS_CSP,
            clipboard=True,
            initialization_script=SHORTCUT_BRIDGE_JS,
            user_agent="tkwry-browser-settings/1.0",
        )
        self._bind_settings_rpc(web)
        self._safe_when_ready(web, self.push_settings_state)
        self._safe_when_ready(
            web, lambda: self.scroll_settings_section(self._settings_active_section)
        )
        self.tabs[SETTINGS_TAB_ID] = Tab(
            frame=frame, web=web, title="Settings", kind="settings"
        )
        self.select_tab(SETTINGS_TAB_ID)
        BrowserShortcutBindings.refresh_bindtags(self.root)
        if focus_profiles:
            self._schedule_after(
                100, lambda: self.scroll_settings_section("profiles-section")
            )

    def _bind_settings_rpc(self, web: WebView) -> None:
        self._expose_clipboard(web)

        @web.expose
        def get_state() -> dict[str, Any]:
            return self.settings_state()

        @web.expose
        def save_settings(
            home: str = "", search: str = "", download_dir: str = ""
        ) -> None:
            self._save_settings_form(
                home=home, search=search, download_dir=download_dir
            )

        @web.expose
        def browse_download_dir() -> None:
            self._browse_download_dir()

        @web.expose
        def clear_browsing_data() -> None:
            self._confirm_clear_browsing_data()

        @web.expose
        def switch_profile(name: str = "") -> None:
            if name:
                self._switch_profile(str(name))

        @web.expose
        def create_profile(name: str = "") -> None:
            if name:
                self._settings_create_profile(name=str(name))

        @web.expose
        def delete_profile(name: str = "") -> None:
            if name:
                self._settings_delete_profile(str(name))

        @web.expose
        def refresh_cookies() -> None:
            self.push_settings_state()

        @web.expose
        def set_ui_theme(dark: bool = False) -> None:
            self.apply_ui_theme(bool(dark))

        @web.expose
        def run_shortcut(name: str = "") -> None:
            if name:
                self.run_shortcut(str(name))

    def settings_state(self) -> dict[str, Any]:
        cookies: list[dict[str, Any]] = []
        note = "Open a page tab to inspect cookies."
        content = self.content_web()
        if content is not None and content.ready:
            url = content.url or ""
            try:
                raw = content.cookies_for_url(url) if url else content.cookies()
            except Exception as exc:
                note = f"Failed to read cookies: {exc}"
                raw = []
            if raw is not None:
                label = _short_status_url(url) if url else "(all)"
                note = f"Cookies for {label}"
                cookies = [
                    {
                        "name": c.name,
                        "domain": c.domain,
                        "path": c.path,
                        "secure": c.secure,
                    }
                    for c in raw
                ]
            elif not cookies:
                note = f"Cookies for {_short_status_url(url) if url else '(all)'}"
        return {
            "home": self.store.settings.home,
            "search": self.store.settings.search_template,
            "download_dir": self.store.settings.download_dir,
            "current_profile": self.store.root.name,
            "profiles": list_profile_names(),
            "cookie_note": note,
            "cookies": cookies,
        }

    def push_settings_state(self) -> None:
        tab = self.tabs.get(SETTINGS_TAB_ID)
        if tab is None or tab.web is None or tab.web.destroyed or not tab.web.ready:
            return
        try:
            tab.web.emit("state", self.settings_state())
        except Exception:
            pass

    def _settings_create_profile(self, name: str = "") -> None:
        raw = name or ""
        if not raw:
            return
        name = sanitize_profile_name(raw)
        profile = profile_dir(name)
        if profile.exists():
            messagebox.showerror(
                "Profiles",
                f"Profile “{name}” already exists.",
                parent=self.root,
            )
            return
        self._switch_profile(name)

    def _settings_delete_profile(self, name: str) -> None:
        name = sanitize_profile_name(name)
        if name == self.store.root.name:
            messagebox.showerror(
                "Profiles",
                "You cannot delete the profile you are currently using.\n"
                "Switch to another profile first.",
                parent=self.root,
            )
            return
        path = profile_dir(name)
        if not path.is_dir():
            messagebox.showinfo(
                "Profiles",
                f"Profile “{name}” does not exist on disk.",
                parent=self.root,
            )
            self.push_settings_state()
            return
        if not messagebox.askyesno(
            "Delete profile",
            f"Delete profile “{name}”?\n\n"
            f"This removes bookmarks, history, cookies, cache, and downloads "
            f"stored under:\n{path}",
            parent=self.root,
            icon="warning",
        ):
            return
        try:
            delete_profile_data(name)
        except OSError as exc:
            messagebox.showerror(
                "Profiles",
                f"Could not delete profile “{name}”:\n{exc}",
                parent=self.root,
            )
            return
        self.push_settings_state()
        self.status_var.set(f"Deleted profile “{name}”")

    def _browse_download_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.store.settings.download_dir or None,
        )
        if path:
            self.store.settings.download_dir = path
            self.push_settings_state()

    def _save_settings_form(
        self, home: str = "", search: str = "", download_dir: str = ""
    ) -> None:
        home = (home or "").strip() or BrowserSettings().home
        search = (search or "").strip() or BrowserSettings().search_template
        if "{query}" not in search:
            messagebox.showerror(
                "Settings", "Search URL must contain {query}.", parent=self.root
            )
            return
        self.store.settings.home = home
        self.store.settings.search_template = search
        self.store.settings.download_dir = (download_dir or "").strip()
        self.store.save_settings()
        self.status_var.set("Settings saved")
        self.push_settings_state()

    def _confirm_clear_browsing_data(self) -> None:
        if messagebox.askyesno(
            "Clear browsing data",
            "Clear cookies, cache, and other browsing data for this profile?\n"
            "(Bookmarks / history files are kept.)",
            parent=self.root,
        ):
            self.clear_browsing_data()

    def open_app_menu(self, x: int = 0, y: int = 0) -> None:
        """Native Tk popup — HTML menus clip inside the chrome WebView."""
        mod = "Command" if sys.platform == "darwin" else "Ctrl"
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="New Tab",
            accelerator=f"{mod}+T",
            command=self.new_blank_tab,
        )
        menu.add_command(
            label="Reopen Closed Tab",
            accelerator=f"{mod}+Shift+T",
            command=self.restore_closed_tab,
        )
        menu.add_command(
            label="New Window",
            accelerator=f"{mod}+N",
            command=self.open_new_window,
        )
        menu.add_command(
            label="New Private Window",
            accelerator=f"{mod}+Shift+N",
            command=self.open_private_window,
        )
        menu.add_separator()
        side_label = "Hide Side Pane" if self._side_visible else "Show Side Pane"
        menu.add_command(label=side_label, command=self.toggle_side_pane)
        menu.add_separator()
        menu.add_command(label="Zoom In", command=lambda: self.nudge_zoom(0.1))
        menu.add_command(label="Zoom Out", command=lambda: self.nudge_zoom(-0.1))
        menu.add_separator()
        menu.add_command(label="Print…", command=self.print_current)
        menu.add_command(label="Open DevTools", command=self.open_devtools)
        menu.add_command(label="Open in System Browser", command=self.open_external)
        menu.add_separator()
        menu.add_command(label="Settings", command=self.open_settings)
        menu.add_command(label="Help…", command=self.show_help)

        try:
            ox = self.chrome_frame.winfo_rootx()
            oy = self.chrome_frame.winfo_rooty()
            if x > 0 or y > 0:
                px = ox + int(x)
                py = oy + int(y)
            else:
                px = ox + max(0, self.chrome_frame.winfo_width() - 12)
                py = oy + self.chrome_frame.winfo_height()
        except tk.TclError:
            px = self.root.winfo_rootx() + 40
            py = self.root.winfo_rooty() + 80
        try:
            menu.tk_popup(px, py)
        finally:
            menu.grab_release()

    def print_current(self) -> None:
        web = self.content_web()
        if web is None or not web.ready:
            return
        web.print()
        self.status_var.set("Print dialog")

    def open_devtools(self) -> None:
        web = self.content_web()
        if web is None or not web.ready:
            return
        web.open_devtools()
        self._schedule_after(0, self._repair_side_pane)

    def open_external(self) -> None:
        web = self.content_web()
        url = web.url if web else ""
        if not url or not is_http_url(url):
            self.status_var.set("Nothing to open externally")
            return
        if open_in_browser(url):
            self.status_var.set("Opened in system browser")

    def open_tkwry_repo(self) -> None:
        if open_in_browser(TKWRY_REPO_URL):
            self.status_var.set(f"Opened tkwry {TKWRY_VERSION}")
        else:
            self.status_var.set(TKWRY_REPO_URL)

    def show_help(self) -> None:
        if messagebox.askyesno(
            "Help",
            f"tkwry {TKWRY_VERSION}\n\n"
            f"Open the repository?\n{TKWRY_REPO_URL}",
            parent=self.root,
        ):
            self.open_tkwry_repo()

    def _popup_context_menu(self, event: ContextMenuEvent) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        href = (event.link_url or "").strip()
        if href:
            menu.add_command(
                label="Open Link in New Tab", command=lambda: self.add_tab(href)
            )
            menu.add_command(
                label="Copy Link",
                command=lambda: (
                    self.root.clipboard_clear(),
                    self.root.clipboard_append(href),
                ),
            )
            if is_http_url(href):
                menu.add_command(
                    label="Open Link in System Browser",
                    command=lambda: open_in_browser(href),
                )
            menu.add_separator()
        menu.add_command(label="Back", command=self.go_back)
        menu.add_command(label="Forward", command=self.go_forward)
        menu.add_command(label="Reload", command=self.reload_or_stop)
        menu.add_separator()
        menu.add_command(label="DevTools", command=self.open_devtools)
        try:
            menu.tk_popup(int(event.x), int(event.y))
        finally:
            menu.grab_release()

    def _refresh_status(self, *, page_url: str | None = None) -> None:
        tab = self.current_tab()
        if tab is None:
            self.status_var.set("")
            return
        if tab.web is None:
            self.status_var.set(tab.title or "Settings")
            return
        url = (page_url or "").strip()
        if not url:
            try:
                url = (tab.web.url or "").strip()
            except Exception:
                url = ""
        if tab.loading:
            self.status_var.set(f"Loading {_short_status_url(url)}…")
            return
        title = (tab.title or "").strip()
        if title and title != "New Tab":
            self.status_var.set(title)
            return
        if url:
            self.status_var.set(_short_status_url(url))
            return
        self.status_var.set("")

    def open_new_window(self) -> None:
        """Open another non-private window on the current (or default) profile."""
        if self.ephemeral:
            profile = PROFILES_DIR / DEFAULT_PROFILE
        else:
            profile = self.store.root
        profile.mkdir(parents=True, exist_ok=True)
        win = tk.Toplevel(self.root)
        chrome_session = WebSession(data_directory=profile / "chrome")
        side_session = WebSession(data_directory=profile / "side")
        settings_session = WebSession(data_directory=profile / "settings")
        content_session = WebSession(data_directory=profile / "webview")
        store = BrowserStore(profile)
        BrowserApp(
            root=win,
            chrome_session=chrome_session,
            side_session=side_session,
            settings_session=settings_session,
            content_session=content_session,
            store=store,
            ephemeral=False,
        ).build()

    def open_private_window(self) -> None:
        win = tk.Toplevel(self.root)
        profile = Path(tempfile.mkdtemp(prefix="tkwry-browser-private-"))
        atexit.register(shutil.rmtree, profile, ignore_errors=True)
        chrome_session = WebSession(ephemeral=True)
        side_session = WebSession(ephemeral=True)
        settings_session = WebSession(ephemeral=True)
        content_session = WebSession(ephemeral=True)
        store = BrowserStore(profile)
        BrowserApp(
            root=win,
            chrome_session=chrome_session,
            side_session=side_session,
            settings_session=settings_session,
            content_session=content_session,
            store=store,
            ephemeral=True,
        ).build()

    def on_quit(self) -> None:
        self._ui_epoch += 1
        self._cancel_pending_after()
        if self._chrome_after is not None:
            try:
                self.root.after_cancel(self._chrome_after)
            except tk.TclError:
                pass
            self._chrome_after = None
        try:
            self.content_session.close()
        except Exception:
            pass
        try:
            self.side_session.close()
        except Exception:
            pass
        try:
            self.settings_session.close()
        except Exception:
            pass
        try:
            self.chrome_session.close()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    ephemeral = "--private" in sys.argv
    if ephemeral:
        profile = Path(tempfile.mkdtemp(prefix="tkwry-browser-private-"))
        atexit.register(shutil.rmtree, profile, ignore_errors=True)
        chrome_session = WebSession(ephemeral=True)
        side_session = WebSession(ephemeral=True)
        settings_session = WebSession(ephemeral=True)
        content_session = WebSession(ephemeral=True)
    else:
        profile = PROFILES_DIR / DEFAULT_PROFILE
        profile.mkdir(parents=True, exist_ok=True)
        chrome_session = WebSession(data_directory=profile / "chrome")
        side_session = WebSession(data_directory=profile / "side")
        settings_session = WebSession(data_directory=profile / "settings")
        content_session = WebSession(data_directory=profile / "webview")

    store = BrowserStore(profile)
    root = tk.Tk()
    BrowserApp(
        root=root,
        chrome_session=chrome_session,
        side_session=side_session,
        settings_session=settings_session,
        content_session=content_session,
        store=store,
        ephemeral=ephemeral,
    ).build()
    root.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
