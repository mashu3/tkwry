"""Shared browser profile (``WebSession``) for one or more WebViews."""

from __future__ import annotations

from pathlib import Path

from tkwry._core import WebSession as NativeWebSession


class WebSession:
    """Shared wry ``WebContext`` — cookies / cache / localStorage where supported.

    Keep the session alive for as long as any :class:`~tkwry.WebView` that
    uses it is alive (required on macOS when ``app=`` / custom protocols are
    involved).

    **``app=`` sharing:** WebViews that share a **non-ephemeral** session must
    use the **same** ``app=`` root. Linux can register the ``tkwry://`` custom
    protocol only once per ``WebContext``; tkwry raises ``ValueError`` if a
    second root is used (all platforms). Use a separate ``WebSession`` for
    unrelated local apps. Ephemeral sessions are not bound to one root.
    Do not share a persistent session between a trusted ``app=`` WebView and
    an untrusted external site — use ``untrusted=True`` (ephemeral) or a
    separate :class:`WebSession`.

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
        self._app_root: str | None = None

    def _bind_app_root(self, root: str | None) -> None:
        """Record ``app=`` root for this session; reject a conflicting root."""
        if root is None or self.ephemeral:
            return
        existing = self._app_root
        if existing is None:
            self._app_root = root
            return
        if existing != root:
            raise ValueError(
                f"WebSession already has app root {existing}; cannot use {root}. "
                "WebViews that share a session must use the same app= root "
                "(Linux registers tkwry:// once per WebContext)."
            )

    @property
    def app_root(self) -> Path | None:
        """``app=`` filesystem root bound to this session, if any."""
        return Path(self._app_root) if self._app_root else None

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
