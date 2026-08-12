"""Shared browser profile (``WebSession``) for one or more WebViews."""

from __future__ import annotations

from pathlib import Path

from tkwry._core import WebSession as NativeWebSession


class WebSession:
    """Shared wry ``WebContext`` — cookies / cache / localStorage where supported.

    Keep the session alive for as long as any :class:`~tkwry.WebView` that
    uses it is alive (required on macOS when ``app=`` / custom protocols are
    involved).

    Parameters
    ----------
    data_directory:
        Persistent profile directory. Created if missing. Mutually exclusive
        with ``ephemeral=True``.
    ephemeral:
        Private / non-persistent browsing (``with_incognito``). Cookie sharing
        across WebViews in an ephemeral session is best-effort by platform.
    """

    def __init__(
        self,
        data_directory: str | Path | None = None,
        *,
        ephemeral: bool = False,
    ) -> None:
        if ephemeral and data_directory is not None:
            raise ValueError(
                "WebSession: pass data_directory= or ephemeral=True, not both"
            )
        path: str | None = None
        if data_directory is not None:
            resolved = Path(data_directory).expanduser().absolute()
            resolved.mkdir(parents=True, exist_ok=True)
            path = str(resolved)
        self._native = NativeWebSession(data_directory=path, ephemeral=ephemeral)

    @property
    def data_directory(self) -> Path | None:
        raw = self._native.data_directory
        return Path(raw) if raw else None

    @property
    def ephemeral(self) -> bool:
        return bool(self._native.ephemeral)

    @property
    def native(self) -> NativeWebSession:
        """Underlying ``tkwry._core.WebSession`` (for WebView create)."""
        return self._native
