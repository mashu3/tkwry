"""Local app path resolution for ``WebView(app=...)``.

Maps a directory or HTML entry file onto a ``tkwry://`` URL served by the
native custom protocol (no localhost HTTP server).
"""

from __future__ import annotations

from pathlib import Path

APP_SCHEME = "tkwry"
APP_HOST = "localhost"


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

    The native ``tkwry://`` handler canonicalizes each request (following
    symlinks, Windows junctions, and reparse points) and refuses anything
    outside this root. Internal links that stay under the root are allowed.
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
