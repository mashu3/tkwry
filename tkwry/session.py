"""Shared browser profile (``WebSession``) for one or more WebViews."""

from __future__ import annotations

import sys
import traceback
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tkwry._core import WebSession as NativeWebSession

if TYPE_CHECKING:
    from tkwry.webview import WebView


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

    **Broadcast:** :meth:`emit_all` sends a Python→JS event to every live
    WebView that shares this session and is eligible for
    :meth:`~tkwry.WebView.emit` (ready, not ``untrusted``, current page
    allowed by that view's ``bridge_origins``).

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
        # Linux: wry ``WebContext`` touches WebKitGTK (ApplicationInfo) and
        # panics if GTK was never initialized. Owned sessions are created in
        # ``WebView.__init__`` before native WebView / GtkPump paths call
        # ``gtk::init`` — so init here for any early ``WebSession(...)``.
        if sys.platform.startswith("linux"):
            from tkwry._core import ensure_gtk_init

            ensure_gtk_init()
        self._native: NativeWebSession | None = NativeWebSession(
            data_directory=path, ephemeral=ephemeral
        )
        self._app_root: str | None = None
        self._webviews: weakref.WeakSet[WebView] = weakref.WeakSet()
        self._closed = False
        self._ephemeral = ephemeral
        self._data_directory_resolved: Path | None = (
            Path(path) if path is not None else None
        )

    def _require_open(self, action: str) -> None:
        if self._closed:
            raise ValueError(f"WebSession is closed; cannot {action}")

    def close(self) -> None:
        """Destroy live WebViews on this session and release the native profile.

        Idempotent. Invokes :meth:`~tkwry.WebView.destroy` on each registered
        view that is not already destroyed — run on the **Tk main thread** (same
        requirement as those ``destroy`` calls). After close, new
        :class:`~tkwry.WebView` instances cannot attach to this session and
        :meth:`emit_all` / :attr:`native` raise :class:`ValueError`.
        """
        if self._closed:
            return
        for web in list(self._webviews):
            if not web._destroyed:
                web.destroy()
        self._webviews = weakref.WeakSet()
        self._native = None
        self._closed = True

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has been called."""
        return self._closed

    def _register_webview(self, web: WebView) -> None:
        self._require_open("register a WebView")
        self._webviews.add(web)

    def _unregister_webview(self, web: WebView) -> None:
        self._webviews.discard(web)

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

    def emit_all(self, event: str, data: Any = None) -> int:
        """Broadcast :meth:`~tkwry.WebView.emit` to siblings sharing this session.

        Must run on the Tk main thread. Skips views that are destroyed, not
        ready, ``untrusted``, or whose current page is outside that view's
        ``bridge_origins``. A sibling that raises during ``emit`` is skipped
        (traceback to stderr); other views still receive the event. Returns
        how many views successfully received it.
        """
        self._require_open("emit_all")
        if not event:
            raise ValueError("emit_all: event name must be non-empty")
        # Fail fast on non-JSON payloads before touching any WebView.
        from tkwry.ipc import emit_script

        emit_script(event, data)
        sent = 0
        for web in list(self._webviews):
            if not web._emit_eligible():
                continue
            try:
                web.emit(event, data)
            except Exception:
                traceback.print_exc()
                continue
            sent += 1
        return sent

    @property
    def app_root(self) -> Path | None:
        """``app=`` filesystem root bound to this session, if any."""
        return Path(self._app_root) if self._app_root else None

    @property
    def data_directory(self) -> Path | None:
        return self._data_directory_resolved

    @property
    def ephemeral(self) -> bool:
        return self._ephemeral

    @property
    def native(self) -> NativeWebSession:
        """Underlying ``tkwry._core.WebSession`` (for WebView create)."""
        self._require_open("access native")
        assert self._native is not None
        return self._native
