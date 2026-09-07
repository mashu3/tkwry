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
DownloadFailedHandler: TypeAlias = Callable[[str, str | None], None]


@dataclass(frozen=True, slots=True)
class Download:
    """An in-flight or starting download from the engine.

    Parameters
    ----------
    url:
        Source URL.
    suggested_dest:
        Engine-suggested absolute save path.
    dest:
        Resolved save path after ``on_download`` (override or suggested).
    """

    url: str
    suggested_dest: str
    dest: str | None = None

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
