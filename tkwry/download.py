"""App-facing download objects and handler helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from tkwry._origin import unique_download_path

DownloadDecision: TypeAlias = str | Path | bool | None
DownloadHandler: TypeAlias = Callable[["Download"], DownloadDecision]
DownloadStartedHandler: TypeAlias = Callable[["Download"], None]
DownloadCompleteHandler: TypeAlias = Callable[["Download", bool], None]
DownloadFailedHandler: TypeAlias = Callable[["Download"], None]


@dataclass(frozen=True, slots=True)
class Download:
    """A download from the engine (start or completion snapshot).

    Parameters
    ----------
    url:
        Source URL.
    suggested_dest:
        Engine-suggested absolute save path at start. On completion-only
        snapshots (no start hook), mirrors ``dest`` when the engine reported
        one, otherwise ``""``.
    dest:
        Resolved save path after ``on_download`` (override or suggested).
        May be ``None`` when the engine omits a path (common on failure).
    success:
        ``True`` / ``False`` after a completion event; ``None`` for
        start-only snapshots (``last_started_download`` / ``on_download``).
    """

    url: str
    suggested_dest: str
    dest: str | None = None
    success: bool | None = None

    @property
    def suggested_filename(self) -> str:
        return Path(self.suggested_dest).name

    def save(self, directory: str | Path) -> str:
        """Return an absolute path under *directory* using the suggested name.

        Creates *directory* when missing. Uses
        :func:`~tkwry.unique_download_path` so an existing file is not
        overwritten.
        """
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / self.suggested_filename
        return str(unique_download_path(candidate))

    def save_as(self, path: str | Path) -> str:
        """Return an absolute save path from *path*."""
        expanded = Path(path).expanduser()
        if not expanded.is_absolute():
            raise ValueError("save_as: path must be an absolute path")
        return str(expanded.resolve())


def call_download_handler(
    handler: DownloadHandler,
    download: Download,
) -> DownloadDecision:
    """Invoke *handler* with *download*."""
    return handler(download)


def download_from_complete(url: str, dest: str | None, *, success: bool) -> Download:
    """Build a completion ``Download`` from engine ``(url, dest, success)``."""
    return Download(
        url=url,
        suggested_dest=dest or "",
        dest=dest,
        success=success,
    )
