"""Local app path resolution for ``WebView(app=...)``.

Maps a directory or HTML entry file onto a ``tkwry://`` URL served by the
native custom protocol (no localhost HTTP server).
"""

from __future__ import annotations

import os
from collections.abc import Collection
from pathlib import Path

APP_SCHEME = "tkwry"
APP_HOST = "localhost"

WATCH_DEFAULT_SUFFIXES = frozenset(
    {
        ".html",
        ".htm",
        ".js",
        ".mjs",
        ".cjs",
        ".css",
        ".json",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".wasm",
        ".map",
        ".txt",
        ".md",
        ".xml",
        ".toml",
        ".yaml",
        ".yml",
        ".webmanifest",
    }
)
WATCH_DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".vendor",
    }
)
WATCH_DEFAULT_MAX_FILES = 2000


def app_url(entry: str = "index.html") -> str:
    """Build a ``tkwry://localhost/...`` URL for *entry* (relative path)."""
    rel = entry.replace("\\", "/").lstrip("/")
    if not rel:
        rel = "index.html"
    return f"{APP_SCHEME}://{APP_HOST}/{rel}"


def resolve_app(app: str | Path) -> tuple[str, str]:
    """Resolve *app* to ``(app_root, initial_url)``.

    *app* may be:

    - a directory containing ``index.html``
    - a path to an ``.html`` / ``.htm`` file (root = parent directory)

    Returns an absolute filesystem root and a ``tkwry://localhost/...`` URL.

    The native ``tkwry://`` handler opens each request under this root,
    then verifies the opened file's identity against the canonical path
    (symlinks, Windows junctions, and reparse points that escape are
    refused). Internal links that stay under the root are allowed.
    """
    path = Path(app).expanduser()
    # Match file-URL policy: do not follow symlinks via resolve().
    # Serving still canonicalizes per request so links cannot escape root.
    path = path.absolute()
    if path.is_file():
        if path.suffix.lower() not in {".html", ".htm"}:
            raise ValueError(f"app file must be HTML (.html/.htm), got: {path.name!r}")
        root = path.parent
        entry = path.name
    elif path.is_dir():
        root = path
        index = path / "index.html"
        if not index.is_file():
            raise ValueError(f"app directory has no index.html: {path}")
        entry = "index.html"
    else:
        raise ValueError(f"app path does not exist: {path}")
    return str(root), app_url(entry)


def normalize_watch_suffixes(suffixes: Collection[str]) -> frozenset[str]:
    """Normalize extension names to a ``.ext`` frozenset."""
    out: set[str] = set()
    for item in suffixes:
        text = str(item).strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = f".{text}"
        out.add(text)
    return frozenset(out)


def scan_app_mtime(
    root: str | Path,
    *,
    suffixes: Collection[str] | None = WATCH_DEFAULT_SUFFIXES,
    ignore_dirs: Collection[str] = WATCH_DEFAULT_IGNORE_DIRS,
    max_files: int = WATCH_DEFAULT_MAX_FILES,
) -> tuple[float, int, bool]:
    """Return ``(latest_mtime, files_scanned, truncated)`` under *root*.

    Does not follow directory or file symlinks. *suffixes* ``None`` means all
    files; a set/list filters by extension (case-insensitive). *ignore_dirs*
    are directory **names** pruned from the walk. Scanning stops after
    *max_files* matching files (*truncated* is then ``True``).
    """
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    ignore = frozenset(ignore_dirs)
    suffix_set = None if suffixes is None else normalize_watch_suffixes(suffixes)
    base = Path(root)
    latest = 0.0
    seen = 0
    truncated = False
    if not base.is_dir():
        return latest, seen, truncated
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in ignore]
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix_set is not None and suffix not in suffix_set:
                continue
            if seen >= max_files:
                truncated = True
                break
            seen += 1
            path = Path(dirpath) / name
            try:
                latest = max(latest, path.lstat().st_mtime)
            except OSError:
                continue
        if truncated:
            break
    return latest, seen, truncated
