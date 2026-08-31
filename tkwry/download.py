"""App-facing download objects and handler helpers."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from tkwry._origin import unique_download_path

DownloadDecision: TypeAlias = str | Path | bool | None
DownloadHandler: TypeAlias = (
    Callable[["Download"], DownloadDecision] | Callable[[str, str], DownloadDecision]
)
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
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_absolute():
            raise ValueError("save_as: path must resolve to an absolute path")
        return str(resolved)


def call_download_handler(
    handler: DownloadHandler,
    download: Download,
    *,
    url: str,
    suggested_dest: str,
) -> DownloadDecision:
    """Invoke *handler* using the one-arg ``Download`` or legacy two-arg form."""
    positional = _positional_arity(handler)
    if positional <= 1:
        return handler(download)
    return handler(url, suggested_dest)


def _positional_arity(func: object) -> int:
    sig = inspect.signature(func)
    count = 0
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            return 2
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            if param.default is inspect.Parameter.empty:
                count += 1
            else:
                break
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            break
    return count
