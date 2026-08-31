"""Tkinter WebView widget."""

from __future__ import annotations

import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import warnings
import weakref
from collections import deque
from collections.abc import Callable, Collection, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple, TypeAlias, TypeVar, cast, overload
from urllib.parse import urlparse

from tkwry._app import resolve_app, resolve_app_csp, validate_app_isolation
from tkwry._core import (
    Cookie,
    DragDropEvent,
    NewWindowResponse,
    PageLoadEvent,
    PermissionKind,
    PermissionResponse,
)
from tkwry._core import (
    WebView as NativeWebView,
)
from tkwry._host import (
    _claim_frame_host,
    _drain_pending_destroy_webviews,
    _ensure_tk_wakeup_fileevent,
    _frame_webview_refs,
    _open_wakeup_pipe,
    _pump_toplevel_wakeup_pipe,
    _register_sync_hook_webview,
    _release_frame_host,
    _release_tk_wakeup_pipe,
    _run_pending_webview_destroy,
    _toplevel_wakeup_write_fd,
    _track_atexit_destroy_toplevel,
    _unregister_sync_hook_webview,
)
from tkwry._linux import GtkPump
from tkwry._origin import (
    INLINE_ORIGINS,
    BridgeAllowlist,
    app_navigation_allowed,
    is_external_http_url,
    normalize_download_allow,
    normalize_navigation_allow,
    open_in_browser,
    origin_allowed,
    origin_of,
    resolve_bridge_origins,
    untrusted_navigation_allowed,
    warn_star_bridge_origins,
)
from tkwry._parent import (
    check_tk_thread_id,
    require_tk_thread,
    tk_embed_origin,
    tk_embed_parent,
)
from tkwry._rpc_api import WebViewRpcMixin
from tkwry._url import _normalize_load_headers, _normalize_url, _validate_url
from tkwry.context_menu import (
    ContextMenuCallback,
    ContextMenuEvent,
    ContextMenuHandler,
    ContextMenuItem,
    normalize_context_menu_items,
)
from tkwry.download import (
    Download,
    DownloadFailedHandler,
    DownloadHandler,
    DownloadStartedHandler,
    call_download_handler,
)
from tkwry.exceptions import (
    TkwrySecurityWarning,
    WebViewCreationError,
    WebViewDestroyedError,
    WebViewNavigationError,
    WebViewNotReadyError,
    WebViewTimeoutError,
)
from tkwry.navigation import (
    NavigationEvent,
    NavigationHandler,
    NavigationPolicyHandler,
    call_navigation_handler,
)
from tkwry.session import WebSession

if sys.platform == "darwin":
    from tkwry._macos import (
        _ensure_mac_pump,
        _ensure_mac_wakeup_pipe,
        _mac_service_wakeup,
        _register_macos_webview,
        _release_tk_keyboard_focus,
        _set_mac_webviews_input_active,
        _unregister_macos_webview,
    )

IpcHandler: TypeAlias = Callable[[str], None]
BridgeOrigins: TypeAlias = Literal["*"] | Collection[str]
BridgeAllow: TypeAlias = Callable[[str], bool]
PageLoadHandler: TypeAlias = Callable[[PageLoadEvent, str], None]
TitleChangedHandler: TypeAlias = Callable[[str], None]
NewWindowHandler: TypeAlias = Callable[[str], NewWindowResponse]
PermissionHandler: TypeAlias = Callable[[PermissionKind], PermissionResponse]
DragDropHandler: TypeAlias = Callable[[DragDropEvent, list[str], tuple[int, int]], None]
EvalCallback: TypeAlias = Callable[[str], None]
EvalErrorHandler: TypeAlias = Callable[[Exception], None]
CreationFailedHandler: TypeAlias = Callable[[BaseException], None]
CallbackErrorHandler: TypeAlias = Callable[[BaseException, str], None]
DownloadCompleteHandler: TypeAlias = Callable[[str, str | None, bool], None]
_DANGEROUS_DOWNLOAD_SCHEMES = frozenset({"javascript", "vbscript", "mailto"})
_PendingLoad: TypeAlias = (
    tuple[Literal["url"], str, tuple[tuple[str, str], ...] | None]
    | tuple[Literal["html"], str]
)
_PendingEval: TypeAlias = tuple[float, EvalCallback, EvalErrorHandler | None]
_NativeEvalWait: TypeAlias = tuple[int, int, EvalCallback, EvalErrorHandler | None]
_SyncHookItem: TypeAlias = tuple[
    Callable[[], object],
    list[object],
    object,
    threading.Event,
    list[bool],
    list[bool],
    list[float],
]
_EVAL_CALLBACK_TIMEOUT_S = 30.0
_SYNC_HOOK_TIMEOUT_S = 30.0
_SYNC_HOOK_HANDLER_TIMEOUT_S = 30.0
_SYNC_HOOK_MAX_WAIT_S = _SYNC_HOOK_TIMEOUT_S + _SYNC_HOOK_HANDLER_TIMEOUT_S
_SYNC_HOOK_EVENT_POOL_SIZE = 4
_MIN_LAYOUT_DIMENSION = 2
_CREATE_MAX_ATTEMPTS = 30
_FLUSH_LOAD_MAX_ATTEMPTS = 3
_FLUSH_LOAD_RETRY_BASE_MS = 150
_FLUSH_LOAD_RETRY_MAX_MS = 2000
_NATIVE_TEARDOWN_MAX_ATTEMPTS = 100
_QUEUE_DROP_IPC = 0
_QUEUE_DROP_PAGE_LOAD = 1
_QUEUE_DROP_TITLE = 2
_QUEUE_DROP_DRAG_DROP = 3
_QUEUE_DROP_EVAL = 4
_QUEUE_DROP_RPC = 5
_T = TypeVar("_T")


def _validate_color_component(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not (0 <= value <= 255):
        raise ValueError(f"{name} must be 0-255, got {value}")


def _validate_dimension(value: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < _MIN_LAYOUT_DIMENSION:
        raise ValueError(f"{name} must be >= {_MIN_LAYOUT_DIMENSION}, got {value}")
    return value


def _validate_background_color(color: tuple[int, int, int, int]) -> None:
    if not isinstance(color, tuple) or len(color) != 4:
        raise ValueError("background_color must be a (r, g, b, a) tuple of 4 ints")
    for val, name in zip(color, ("r", "g", "b", "a")):
        _validate_color_component(val, name)


def _normalize_proxy(
    proxy: Mapping[str, str] | None,
) -> tuple[dict[str, str], tuple[str, str, str]] | None:
    """Normalize ``proxy=`` to a public dict and a native ``(kind, host, port)``.

    Accepts exactly one of ``{"http": "..."}`` or ``{"socks5": "..."}``.
    Values may be ``host:port``, ``[ipv6]:port``, or ``scheme://host:port``.
    Credentials are rejected without echoing them (wry has host/port only).
    """
    if proxy is None:
        return None
    if not isinstance(proxy, Mapping):
        raise TypeError("proxy= must be a mapping or None")
    keys = {str(k) for k in proxy}
    if keys not in ({"http"}, {"socks5"}):
        raise ValueError(
            "proxy= must be exactly one of {'http': 'host:port'} "
            "or {'socks5': 'host:port'}"
        )
    kind = "http" if "http" in keys else "socks5"
    raw_value = proxy[kind]
    if not isinstance(raw_value, str):
        raise TypeError(f"proxy[{kind!r}] must be a str")
    raw = raw_value.strip()
    if not raw:
        raise ValueError(f"proxy[{kind!r}] must be a non-empty host:port string")

    host: str
    port: str
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "proxy= must not include credentials (wry supports host and port only)"
            )
        scheme = (parsed.scheme or "").lower()
        if scheme != kind:
            raise ValueError(f"proxy[{kind!r}] URL scheme must be {kind!r}")
        if not parsed.hostname or parsed.port is None:
            raise ValueError(
                f"proxy[{kind!r}] URL must include host and port "
                f"(e.g. '{kind}://127.0.0.1:8080')"
            )
        host = parsed.hostname
        port = str(parsed.port)
    elif raw.startswith("["):
        end = raw.find("]")
        if end < 0 or end + 1 >= len(raw) or raw[end + 1] != ":":
            raise ValueError(f"proxy[{kind!r}] IPv6 form must be '[addr]:port'")
        host = raw[1:end]
        port = raw[end + 2 :]
    else:
        if raw.count(":") != 1:
            raise ValueError(
                f"proxy[{kind!r}] must be 'host:port' "
                "(use '[ipv6]:port' for IPv6, or a scheme:// URL)"
            )
        host, port = raw.split(":", 1)

    host = host.strip()
    port = port.strip()
    if not host or not port:
        raise ValueError(f"proxy[{kind!r}] must include both host and port")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise ValueError(f"proxy[{kind!r}] port must be an integer 1-65535")

    public_endpoint = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    public = {kind: public_endpoint}
    return public, (kind, host, port)


def _noop_native_eval_callback(_result: str) -> None:
    """Stub passed to Rust; Python delivers via ``_native_eval_wait``."""


class WebViewPhase(Enum):
    """Derived lifecycle phase for triage (not a write-side state machine).

    Computed from ``_destroyed`` / ``_webview`` / layout / visibility flags.
    Does **not** change the ``ready`` contract: ``ready`` stays layout-based,
    while ``HIDDEN`` means the native view should be ``set_visible(False)``.
    """

    PRE_CREATE = "pre_create"
    CREATE_FAILED = "create_failed"
    NATIVE = "native"
    READY = "ready"
    HIDDEN = "hidden"
    TEARING_DOWN = "tearing_down"
    DESTROYED = "destroyed"


class InFlightDownload(NamedTuple):
    """A download allowed at start but not yet completed (no progress %)."""

    url: str
    dest: str | None


class QueueDropCounts(NamedTuple):
    """Overflow drops since the last :meth:`~WebView.take_queue_drop_stats` call.

    The first six fields match :meth:`~WebView.take_queue_drop_counts`.
    ``download_complete`` and ``rpc_stream`` are only on this named view
    (kept out of the legacy 6-tuple through 0.1.x).
    """

    ipc: int
    page_load: int
    title: int
    drag_drop: int
    eval: int
    rpc: int
    download_complete: int
    rpc_stream: int


class WebView(WebViewRpcMixin):
    """Embed a system WebView (wry) inside an existing Tk ``Frame``.

    The host *frame* must be laid out with a real size (``pack`` / ``grid`` /
    ``place``) before the native webview is created. IPC, page-load,
    title-changed, eval callbacks, and drag-and-drop handlers run on the
    **Tk main thread** via an internal queue. Drag-and-drop is notify-only
    (``-> None``); OS drops are always accepted and cannot be denied from
    Python.

    **Lifecycle state** (maintainers: triage ``ready`` / initial load /
    ``destroy`` regressions against this table and :class:`WebViewPhase`
    before patching):

    Two independent axes (do **not** merge them in a drive-by fix):

    * **Layout → ``ready`` / ``<<WebViewReady>>``** — host frame has a geometry
      manager and a real size. Unmap/remap does **not** clear ``ready`` or
      re-fire the ready event.
    * **Map/visibility → ``set_visible`` / ``phase``** — ``<Map>`` / ``<Unmap>``
      (e.g. Notebook tabs) drive ``_host_is_viewable_for_map`` →
      ``_frame_should_show`` → native ``set_visible``. When laid out but not
      shown, ``phase`` is :attr:`WebViewPhase.HIDDEN` while ``ready`` stays
      ``True``.

    +---------------+--------+-------+-----------+---------------------------+
    | Phase         | native | ready | destroyed | Allowed public API        |
    +===============+========+=======+===========+===========================+
    | PRE_CREATE    | None   | False | False     | ``load_*``,               |
    |               |        |       |           | ``wait_until_ready``,     |
    |               |        |       |           | handler setters,          |
    |               |        |       |           | ``bind`` / ``when_ready``,|
    |               |        |       |           | ``sync_bounds``,          |
    |               |        |       |           | ``destroy``               |
    +---------------+--------+-------+-----------+---------------------------+
    | CREATE_FAILED | None   | False | False     | ``when_failed`` /         |
    |               |        |       |           | ``<<WebViewCreateFailed>>``|
    |               |        |       |           | ``creation_failed``;      |
    |               |        |       |           | ``load_*`` / handlers     |
    |               |        |       |           | raise                     |
    |               |        |       |           | ``WebViewCreationError``  |
    +---------------+--------+-------+-----------+---------------------------+
    | NATIVE        | Some   | False | False     | Same as PRE_CREATE until  |
    |               |        |       |           | layout is ready           |
    +---------------+--------+-------+-----------+---------------------------+
    | HIDDEN        | Some   | True* | False     | Public APIs allowed;      |
    | (unmapped tab)|        |       |           | native is                 |
    |               |        |       |           | ``set_visible(False)``.   |
    |               |        |       |           | ``*`` ``ready`` follows   |
    |               |        |       |           | layout size, not map      |
    |               |        |       |           | state (Notebook tabs).    |
    +---------------+--------+-------+-----------+---------------------------+
    | READY         | Some   | True  | False     | All public methods        |
    +---------------+--------+-------+-----------+---------------------------+
    | TEARING_DOWN  | —      | —     | True      | None; poll drains native  |
    |               |        |       |           | teardown only             |
    +---------------+--------+-------+-----------+---------------------------+
    | DESTROYED     | None   | —     | True      | None; raises              |
    |               |        |       |           | ``WebViewDestroyedError`` |
    +---------------+--------+-------+-----------+---------------------------+

    **Initial load** (constructor ``url`` / ``html`` / ``app`` vs user ``load_*``):

    1. **At create** — pending ``url``/``html``/``app`` is captured once in
       ``_try_create`` as ``_initial_load``.
    2. **Post-ready** — after native creation, ``_run_initial_load`` is
       scheduled once (delayed) when the host frame is viewable with real
       geometry. On **Linux**, that path queues via ``_set_pending_load`` +
       ``_dispatch_pending_load`` so WebKitGTK / place layouts flush through
       the GTK pump (same coalescing path as user ``load_*``). Other
       platforms call the native load directly.
    3. **User ``load_*`` last-wins** — :meth:`load_url` / :meth:`load_html`
       supersede any pending constructor load and coalesce rapid calls (only
       the final URL/HTML is applied). Write paths go through
       ``_queue_user_load`` / ``_clear_initial_load`` /
       ``_set_pending_load`` / pre-create helpers.

    **Local apps** (``app=``): a directory (with ``index.html``) or an HTML
    entry file is served through the ``tkwry://`` custom protocol — relative
    CSS/JS/assets resolve without a localhost HTTP server. Relative navigation
    uses ``tkwry://localhost/...``. The app root is fixed at create time.
    Requests are opened under the app root and the opened file's identity is
    checked against the canonical path (symlinks, Windows junctions, reparse
    points that escape the root are forbidden). ``tkwry://`` responses send a
    default Content-Security-Policy (``csp=False`` to disable, or a custom
    policy string). ``coop=True`` / ``corp=True`` add optional
    Cross-Origin-Opener-Policy / Cross-Origin-Resource-Policy.

    **Sessions** (``session=`` / ``profile=`` / ``user_data_dir=`` /
    ``data_directory=`` / ``ephemeral=`` / ``incognito=``): share a
    wry ``WebContext`` (cookies / cache / localStorage where the platform
    supports it) across WebViews. Prefer one :class:`~tkwry.WebSession` per
    profile. WebViews that share a **non-ephemeral** session must use the
    **same** ``app=`` root (``ValueError`` otherwise). Linux can register
    ``tkwry://`` only once per context; tkwry enforces the same rule everywhere.
    Do **not** share a persistent session between a local ``app=`` WebView and
    an untrusted external site.

    **Trust boundaries:** ``window.ipc`` / ``window.tkwry`` are desktop
    privileges. By default IPC/RPC are accepted only from the initial content
    origin (``html=`` → ``about:blank``; ``app=`` → ``tkwry://``;
    ``url=`` → that origin). Entries may include a path prefix
    (``https://trusted.example/app``). ``bridge_allow`` can further restrict
    by full URL. ``bridge_origins="*"`` warns and requires
    ``expose(..., allow_any_origin=True)``. ``untrusted=True`` is a viewer
    mode: no IPC/RPC, ephemeral storage, http(s) only, new windows denied,
    downloads denied — it cannot be combined with ``bridge_origins`` /
    ``bridge_allow``. ``app=`` also locks in-page navigation to ``tkwry://``
    unless you set ``on_navigation``. ``navigation_allow`` adds extra
    in-webview origins (or path prefixes). ``open_external=True`` opens
    off-list http(s) in the system browser and **never** creates a WebView
    from ``on_new_window``. ``download_allow`` / ``on_download`` gate file
    downloads (``untrusted=True`` denies unless a handler or allowlist
    permits); ``on_download`` may return an **absolute** save path or ``False``
    to cancel (relative dests are denied). Use
    :func:`~tkwry.unique_download_path` to avoid overwriting an existing file.
    ``on_download_complete`` is notify-only. Finished downloads also set
    ``last_download`` and generate ``<<WebViewDownloadStarted>>``,
    ``<<WebViewDownloadComplete>>`` or
    ``<<WebViewDownloadFailed>>`` (same ``(url, dest, success)`` tuple).
    :attr:`in_flight_downloads` tracks policy-hook starts until complete
    (no progress %).

    **Navigation hooks** (``on_navigation``, ``on_new_window``) and
    create-time ``permission_handler`` run on the **Tk main thread**, but
    WebKit **blocks** until they return a value. Keep them fast (heavy work
    → deny/default and defer with ``after``). Custom navigation hooks replace
    the built-in ``navigation_allow`` / ``open_external`` policy for that
    direction. ``permission_handler`` is create-only (wry builder); omit it to
    keep the engine default. Timeout / bad return → ``PermissionResponse.Deny``
    (does **not** change ``untrusted=True`` defaults). ``clipboard=True`` is
    create-only opt-in for the Web Clipboard API on Windows / Linux (macOS
    WebView side is always-on — see platform notes). ``javascript_enabled=False``
    is a create-only break-glass (wry ``with_javascript_disabled``); default
    stays ``True`` and ``untrusted=True`` does **not** flip it.
    ``autoplay=`` (default ``True``, wry) allows media without a user
    gesture; ``False`` restores engine gesture requirements. Distinct from
    :attr:`~tkwry.PermissionKind.Autoplay` on ``permission_handler``.
    ``hotkeys_zoom=True`` enables WebView2 Ctrl+/- / Ctrl+wheel / pinch zoom
    (Windows only; wry default ``False``). Distinct from :meth:`set_zoom`.
    ``back_forward_gestures=True`` enables trackpad / swipe back-forward
    navigation (wry ``with_back_forward_navigation_gestures``; default
    ``False``). Effective on Windows, macOS, and Linux.
    ``default_context_menus=False`` hides the WebView2 page context menu
    (Windows only; wry default ``True``). ``untrusted=True`` does **not**
    flip it. Distinct from Tk window menus and from ``devtools=``.
    ``https_scheme=`` (default ``True``) selects the Windows ``app=`` origin
    ``https://tkwry.localhost`` (secure context) vs wry's
    ``http://tkwry.localhost`` (``False``; mixed content, not SW / SubtleCrypto).
    macOS / Linux stay ``tkwry://``.
    ``proxy=`` is create-only: exactly one of ``{"http": "host:port"}`` or
    ``{"socks5": "host:port"}`` (optional ``scheme://`` form). Maps to wry
    ``with_proxy_config``. Credentials are rejected (never logged). macOS
    needs 14.0+ (wry ``mac-proxy``).

    **Navigation** (``load_url`` / ``load_html``): rapid calls are coalesced
    (**last-wins**) — ``load(A); load(B); load(C)`` navigates to ``C`` only.
    Before the native view exists, the last pending load is applied at creation
    (``load_html`` overrides a pending URL). If more than one of ``url``,
    ``html``, and ``app`` is passed to the constructor, precedence is
    ``html`` > ``app`` > ``url`` (a warning is printed to stderr). When
    ``html=`` wins, ``app=`` is ignored for load, ``tkwry://`` serving,
    navigation lock, CSP/COOP/CORP, and bridge origins.

    **Ready** (``<<WebViewReady>>`` / :meth:`when_ready`): fires once per
    instance when the native view first becomes laid out; unmap/remap does not
    re-fire the event. Layout-change paths funnel through
    ``_sync_bounds_and_stacking`` (plus create) into ``_maybe_fire_ready``.

    **Create failed** (``<<WebViewCreateFailed>>`` / :meth:`when_failed` /
    ``on_creation_failed=``): fires once when native creation is abandoned
    (retries exhausted, WebView2 missing, …). The constructor does **not**
    raise; apps that never call a gated API must handle this signal or check
    ``creation_failed``. Late ``bind`` / ``when_failed`` still run once (idle).

    **Page load** (``on_page_load``): fires ``Started`` and ``Finished`` for
    every navigation **while a handler is registered** (native listening
    follows the handler). Events are queued up to a fixed cap while listening;
    navigations that occurred with no handler are **not** replayed when one is
    attached later.

    **Callback exceptions:** lifecycle / IPC / page-load / title / DnD /
    download-complete / ``when_ready`` / ``when_failed`` handlers that raise
    are logged to stderr and do not stop event delivery. Optional provisional
    ``on_callback_error=(exc, kind) -> None`` (and :meth:`set_on_callback_error`)
    routes those failures to app code instead; ``kind`` names the hook
    (e.g. ``"on_page_load"``, ``"ipc_handler"``). Not in ``__all__`` — may
    change in 0.2.x.

    **JavaScript** (``eval_js`` / ``eval_js_with_callback``): ``eval_js`` is
    fire-and-forget (Tk idle, no return value). ``eval_js_with_callback`` is
    asynchronous; the callback receives the result string on the Tk main thread.

    **Eval callback lifetime** (maintainers: triage double-callback / ghost
    timeout regressions against this table before patching):

    +------------------+-----------------------------------------------------+
    | Token            | Role                                                |
    +==================+=====================================================+
    | ``_eval_epoch``  | Generation counter. Bumped by                       |
    |                  | ``_invalidate_eval_generation`` on destroy /        |
    |                  | emergency teardown. Idle runners and drain paths    |
    |                  | drop work when ``wait_epoch != _eval_epoch``.       |
    +------------------+-----------------------------------------------------+
    | Python token     | Key in ``_pending_eval_tokens``                     |
    |                  | ``(deadline, callback, on_error)``. Registered at   |
    |                  | ``eval_js_with_callback``; released on deliver,     |
    |                  | timeout (~30s), destroy, or failed native start.    |
    +------------------+-----------------------------------------------------+
    | Native token     | Key in ``_native_eval_wait``                        |
    |                  | ``(epoch, py_token, callback, on_error)``. Set when |
    |                  | Rust accepts the eval; drained on the Tk poll.      |
    +------------------+-----------------------------------------------------+

    Stop rules: ``_ensure_event_poll`` arms while handlers, pending evals, or
    ``_native_teardown_pending`` remain; ``_disarm_event_poll`` is the only
    writer that clears ``_event_poll_active`` (``_stop_event_poll_if_idle``
    when idle; poll reschedule / TclError paths also disarm). Pair flags are
    written only via ``_arm_native_teardown`` / ``_clear_native_teardown``
    (clear does not stop the poll). Timeout and drain share one release path
    via ``_release_pending_eval`` so the user callback cannot run twice.

    Call :meth:`destroy` or destroy the host frame to release the native view.
    After :meth:`destroy`, the instance cannot be reused; create a new
    ``WebView`` on the same or another frame instead.

    All public methods must run on the **Tk thread** (the thread that created
    the host frame's Tcl interpreter and runs the event loop). Calls from other
    threads raise ``RuntimeError``.
    """

    def __init__(
        self,
        frame: tk.Frame,
        *,
        width: int | None = None,
        height: int | None = None,
        url: str | None = None,
        html: str | None = None,
        app: str | Path | None = None,
        session: WebSession | None = None,
        profile: str | None = None,
        user_data_dir: str | Path | None = None,
        data_directory: str | Path | None = None,
        ephemeral: bool = False,
        incognito: bool = False,
        untrusted: bool = False,
        bridge_origins: BridgeOrigins | None = None,
        bridge_allow: BridgeAllow | None = None,
        navigation_allow: Collection[str] | None = None,
        open_external: bool = False,
        download_allow: Collection[str] | None = None,
        ipc_handler: IpcHandler | None = None,
        spa_fallback: bool = False,
        app_dev: bool = False,
        csp: bool | str | None = None,
        coop: bool = False,
        corp: bool = False,
        rpc_traceback: bool = False,
        devtools: bool = False,
        clipboard: bool = False,
        javascript_enabled: bool = True,
        autoplay: bool = True,
        hotkeys_zoom: bool = False,
        back_forward_gestures: bool = False,
        default_context_menus: bool = True,
        https_scheme: bool = True,
        proxy: Mapping[str, str] | None = None,
        background_color: tuple[int, int, int, int] | None = None,
        user_agent: str | None = None,
        initialization_script: str | None = None,
        focused: bool = True,
        on_navigation: NavigationHandler | None = None,
        navigation_policy: NavigationPolicyHandler | None = None,
        on_page_load: PageLoadHandler | None = None,
        on_title_changed: TitleChangedHandler | None = None,
        on_new_window: NewWindowHandler | None = None,
        permission_handler: PermissionHandler | None = None,
        drag_drop_handler: DragDropHandler | None = None,
        on_download: DownloadHandler | None = None,
        on_download_started: DownloadStartedHandler | None = None,
        on_download_complete: DownloadCompleteHandler | None = None,
        on_download_failed: DownloadFailedHandler | None = None,
        on_creation_failed: CreationFailedHandler | None = None,
        on_callback_error: CallbackErrorHandler | None = None,
        context_menu: Sequence[ContextMenuItem] | None = None,
        on_context_menu: ContextMenuHandler | None = None,
    ) -> None:
        """Embed a WebView in *frame*.

        See the class docstring for lifecycle, RPC, and platform notes.
        WebViews that share a non-ephemeral ``session`` must use the same
        ``app=`` root (``ValueError`` otherwise).
        """
        require_tk_thread(frame)
        if background_color is not None:
            _validate_background_color(background_color)
        proxy_norm = _normalize_proxy(proxy)
        ephemeral = ephemeral or incognito
        profile_name: str | None = profile
        if user_data_dir is not None and data_directory is not None:
            raise ValueError(
                "WebView: pass user_data_dir= or data_directory=, not both"
            )
        if user_data_dir is not None:
            data_directory = user_data_dir
        if (
            sum(
                (
                    session is not None,
                    profile_name is not None,
                    data_directory is not None,
                    ephemeral,
                )
            )
            > 1
        ):
            raise ValueError(
                "WebView: pass only one of session=, profile=, "
                "user_data_dir=/data_directory=, or ephemeral=/incognito="
            )
        if profile_name is not None:
            if untrusted:
                raise ValueError(
                    "WebView: untrusted=True cannot be combined with profile="
                )
            from tkwry.profile import get_profile_session

            session = get_profile_session(profile_name)
        if untrusted:
            if app is not None:
                raise ValueError("WebView: untrusted=True cannot be combined with app=")
            if ipc_handler is not None:
                raise ValueError(
                    "WebView: untrusted=True cannot be combined with ipc_handler="
                )
            if bridge_origins is not None:
                raise ValueError(
                    "WebView: untrusted=True cannot be combined with bridge_origins="
                )
            if bridge_allow is not None:
                raise ValueError(
                    "WebView: untrusted=True cannot be combined with bridge_allow="
                )
            if data_directory is not None:
                raise ValueError("WebView: untrusted=True cannot use data_directory=")
            if session is not None and not session.ephemeral:
                raise ValueError(
                    "WebView: untrusted=True requires an ephemeral WebSession"
                )
        owned_session: WebSession | None = None
        if untrusted and session is None:
            owned_session = WebSession(ephemeral=True)
            session = owned_session
        elif session is None and (data_directory is not None or ephemeral):
            owned_session = WebSession(
                data_directory=data_directory, ephemeral=ephemeral
            )
            session = owned_session
        self._owned_session = owned_session
        self._profile_name = profile_name
        self._session = session
        self._frame = frame
        self._toplevel: tk.Misc
        try:
            self._toplevel = frame.winfo_toplevel()
        except tk.TclError:
            self._toplevel = frame
        self._tk_thread_id = threading.get_ident()
        self._early_create = width is not None or height is not None
        self._init_width = (
            _validate_dimension(width, "width") if width is not None else None
        )
        self._init_height = (
            _validate_dimension(height, "height") if height is not None else None
        )
        self._destroyed = False
        self._ready_delivered = False
        self._ready_pending = False
        self._ready_callbacks: list[Callable[[], None]] = []
        self._failed_delivered = False
        self._failed_pending = False
        self._failed_callbacks: list[CreationFailedHandler] = []
        self._create_pending = False
        self._create_attempt = 0
        self._creation_error: BaseException | None = None
        if on_creation_failed is not None:
            self._failed_callbacks.append(on_creation_failed)
        self._flush_load_attempt = 0
        self._embed = tk_embed_parent(frame)
        self._webview: NativeWebView | None = None
        self._init_rpc_state(
            ipc_handler=ipc_handler,
            rpc_traceback=rpc_traceback,
        )
        self._untrusted = untrusted
        self._lock_app_navigation = False
        self._navigation_allow: frozenset[str] | None = (
            None
            if navigation_allow is None
            else normalize_navigation_allow(navigation_allow)
        )
        self._open_external = open_external
        self._download_allow: frozenset[str] | None = (
            None if download_allow is None else normalize_download_allow(download_allow)
        )
        self._bridge_origins: BridgeAllowlist = "*"
        self._bridge_allow: BridgeAllow | None = None
        self._spa_fallback = spa_fallback
        self._app_dev = app_dev
        self._on_navigation = on_navigation
        self._navigation_policy = navigation_policy
        self._on_page_load = on_page_load
        self._on_title_changed = on_title_changed
        self._on_new_window = on_new_window
        self._permission_handler = permission_handler
        self._drag_drop_handler = drag_drop_handler
        self._on_download = on_download
        self._on_download_started = on_download_started
        self._on_download_complete = on_download_complete
        self._on_download_failed = on_download_failed
        self._on_callback_error = on_callback_error
        self._context_menu_items = normalize_context_menu_items(context_menu)
        self._context_menu_handler = on_context_menu
        self._context_menu_bridge_injected = False
        self._context_menu_tk: tk.Menu | None = None
        self._devtools = devtools
        self._clipboard = bool(clipboard)
        self._javascript_enabled = bool(javascript_enabled)
        self._autoplay = bool(autoplay)
        self._hotkeys_zoom = bool(hotkeys_zoom)
        self._back_forward_gestures = bool(back_forward_gestures)
        self._default_context_menus = bool(default_context_menus)
        if self._context_menu_active() and sys.platform == "win32":
            if self._default_context_menus:
                print(
                    "tkwry: context_menu=/on_context_menu= requires "
                    "default_context_menus=False on Windows; forcing False",
                    file=sys.stderr,
                )
                self._default_context_menus = False
        self._https_scheme = bool(https_scheme)
        if proxy_norm is None:
            self._proxy: dict[str, str] | None = None
            self._proxy_native: tuple[str, str, str] | None = None
        else:
            self._proxy, self._proxy_native = proxy_norm
        self._background_color = background_color
        self._user_agent = user_agent
        self._initialization_script = initialization_script
        self._init_scripts: list[str] = []
        self._inject_scripts: list[str] = []
        self._focus_when_ready = False
        if focused and sys.platform in ("darwin", "win32"):
            # macOS: child WKWebView + focused=True fights Tk for first responder
            # at create. Windows: WebView2 MoveFocus during create returns
            # E_INVALIDARG (0x80070057) when the host HWND cannot take focus
            # (e.g. Tk ``-alpha`` 0 startup cloak, minimized owner).
            self._focus_when_ready = True
            focused = False
        self._focused = focused
        self._event_poll_active = False
        self._wait_until_ready_active = False
        self._pending_eval_callbacks = 0
        self._eval_token_seq = 0
        self._pending_eval_tokens: dict[int, _PendingEval] = {}
        self._native_eval_wait: dict[int, _NativeEvalWait] = {}
        self._sync_hook_queue: queue.SimpleQueue[_SyncHookItem] = queue.SimpleQueue()
        self._sync_hook_depth = 0
        self._sync_hook_event_pool: deque[threading.Event] = deque(
            threading.Event() for _ in range(_SYNC_HOOK_EVENT_POOL_SIZE)
        )
        self._sync_hook_event_pool_lock = threading.Lock()
        self._tk_wakeup_write_fd: int | None = None
        self._tk_wakeup_pipe_attached = False
        # Bumped on destroy so late WebKit-thread delivers are discarded.
        self._eval_epoch = 0
        content_sources = sum(x is not None for x in (url, html, app))
        if content_sources > 1:
            if html is not None:
                winner = "html"
            elif app is not None:
                winner = "app"
            else:
                winner = "url"
            print(
                f"tkwry: {winner}= takes precedence when multiple of "
                "url=/html=/app= are given",
                file=sys.stderr,
            )
        self._app_root: str | None = None
        if app is not None and html is None:
            app_root, app_entry_url = resolve_app(app)
            self._app_root = app_root
            url = app_entry_url
        if self._session is not None:
            self._session._bind_app_root(self._app_root)
        if url is not None:
            url = _normalize_url(url)
            _validate_url(url)
            if url.startswith("tkwry:") and self._app_root is None:
                raise ValueError(
                    "tkwry:// URLs require app= (custom protocol root) at create"
                )
        self._pending_url = None if html is not None else url
        self._pending_url_headers: tuple[tuple[str, str], ...] | None = None
        self._pending_html = html
        self._lock_app_navigation = self._app_root is not None and not untrusted
        has_app = self._app_root is not None
        validate_app_isolation(coop=coop, corp=corp, has_app=has_app)
        self._csp = resolve_app_csp(csp, has_app=has_app)
        self._coop = coop
        self._corp = corp
        self._bridge_origins = resolve_bridge_origins(
            bridge_origins,
            url=None if html is not None else url,
            html=html,
            app=self._app_root is not None and html is None,
        )
        self._bridge_allow = bridge_allow
        if self._bridge_origins == "*":
            warn_star_bridge_origins(stacklevel=4)
            if devtools:
                warnings.warn(
                    'devtools=True with bridge_origins="*" increases XSS '
                    "impact on IPC/RPC",
                    TkwrySecurityWarning,
                    stacklevel=2,
                )
        self._pending_load: _PendingLoad | None = None
        self._flush_load_scheduled = False
        self._post_nav_drain_scheduled = False
        self._in_poll_events = False
        self._pending_eval_js: tuple[str, EvalErrorHandler | None] | None = None
        self._eval_js_scheduled = False
        self._last_eval_error: BaseException | None = None
        self._last_navigation_error: BaseException | None = None
        self._last_download: tuple[str, str | None, bool] | None = None
        self._last_started_download: Download | None = None
        self._in_flight_downloads: list[InFlightDownload] = []
        self._navigation_error_queue: queue.SimpleQueue[BaseException] = (
            queue.SimpleQueue()
        )
        self._local_queue_drop_counts = [0] * (_QUEUE_DROP_RPC + 1)
        self._bounds_sync_scheduled = False
        self._stacking_sync_scheduled = False
        self._initial_load: _PendingLoad | None = None
        self._initial_load_attempt = 0
        self._initial_load_after_id: str | None = None
        self._deferred_after_ids: list[str] = []
        self._native_teardown_pending: NativeWebView | None = None
        self._native_teardown_attempts = 0
        self._frame_bind_ids: list[tuple[str, str]] = []

        _claim_frame_host(frame, self)
        try:
            if self._session is not None:
                self._session._register_webview(self)

            for sequence, handler in (
                ("<Configure>", self._on_configure),
                ("<Map>", self._on_map),
                ("<Unmap>", self._on_unmap),
                ("<Destroy>", self._on_destroy),
            ):
                funcid = self._frame.bind(sequence, handler, add="+")
                self._frame_bind_ids.append((sequence, funcid))
            if sys.platform == "darwin":
                _register_macos_webview(self)
            if self._needs_event_poll():
                self._ensure_event_poll()
            if self._creation_size() is not None or self._early_create:
                self._schedule_try_create()
        except Exception:
            if self._session is not None:
                try:
                    self._session._unregister_webview(self)
                except Exception:
                    pass
            try:
                self._unbind_frame_events()
            except Exception:
                pass
            self._cancel_deferred_callbacks()
            self._disarm_event_poll()
            if sys.platform == "darwin":
                try:
                    _unregister_macos_webview(self)
                except Exception:
                    pass
            _release_frame_host(frame, self)
            raise

    def pack(self, **kwargs) -> None:
        """``pack`` the host frame, then schedule bounds sync / native create."""
        self._require_not_destroyed("pack")
        self._frame.pack(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    def grid(self, **kwargs) -> None:
        """``grid`` the host frame, then schedule bounds sync / native create."""
        self._require_not_destroyed("grid")
        self._frame.grid(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    def place(self, **kwargs) -> None:
        """``place`` the host frame, then schedule bounds sync / native create."""
        self._require_not_destroyed("place")
        self._frame.place(**kwargs)
        self._schedule_bounds_sync()
        self._schedule_try_create()

    def __repr__(self) -> str:
        self._require_tk_thread()
        phase = self.phase
        if phase is WebViewPhase.DESTROYED or phase is WebViewPhase.TEARING_DOWN:
            url = None
        elif self._webview is None:
            url = self._pending_url
            if url is None and self._pending_html is not None:
                url = "<html>"
        else:
            try:
                url = self._webview.url()
            except Exception:
                url = None
        try:
            frame = str(self._frame)
        except Exception:
            frame = "<unavailable>"
        return f"<WebView phase={phase.value} url={url!r} frame={frame}>"

    @property
    def phase(self) -> WebViewPhase:
        """Derived lifecycle phase (see :class:`WebViewPhase`).

        Read-only snapshot of existing flags — not an independently mutated
        state machine. ``ready`` remains layout-based; ``HIDDEN`` means the
        host is not viewable enough to show the native child.
        """
        self._require_tk_thread()
        if self._destroyed:
            if self._native_teardown_pending is not None:
                return WebViewPhase.TEARING_DOWN
            return WebViewPhase.DESTROYED
        if self._creation_error is not None:
            return WebViewPhase.CREATE_FAILED
        if self._webview is None:
            return WebViewPhase.PRE_CREATE
        if not self._layout_ready():
            return WebViewPhase.NATIVE
        if not self._frame_should_show():
            return WebViewPhase.HIDDEN
        return WebViewPhase.READY

    @property
    def ready(self) -> bool:
        """``True`` once the native webview exists with laid-out host geometry."""
        self._require_tk_thread()
        return (
            self._webview is not None and not self._destroyed and self._layout_ready()
        )

    @property
    def url(self) -> str | None:
        """Current document URL.

        Before native creation: the pending URL, or ``"<html>"`` when only
        inline HTML is pending. After creation: the engine URL (may be
        ``None`` for inline HTML on some platforms — see README).
        """
        self._require_not_destroyed("url")
        if self._webview is None:
            if self._pending_url is not None:
                return self._pending_url
            if self._pending_html is not None:
                return "<html>"
            return None
        return self._webview.url()

    @property
    def creation_failed(self) -> bool:
        """``True`` when native creation was abandoned after all retries.

        Also delivered as ``<<WebViewCreateFailed>>`` / :meth:`when_failed`.
        """
        self._require_tk_thread()
        return self._creation_error is not None

    @property
    def creation_error(self) -> BaseException | None:
        """The exception from the final failed creation attempt, if any."""
        self._require_tk_thread()
        return self._creation_error

    @property
    def last_eval_error(self) -> BaseException | None:
        """Most recent eval failure (timeout, native error, or dropped result)."""
        self._require_tk_thread()
        return self._last_eval_error

    @property
    def last_navigation_error(self) -> BaseException | None:
        """Most recent ``on_navigation`` / ``on_new_window`` hook timeout."""
        self._require_tk_thread()
        return self._last_navigation_error

    @property
    def last_download(self) -> tuple[str, str | None, bool] | None:
        """Most recent download completion ``(url, dest, success)``.

        Also delivered as ``<<WebViewDownloadComplete>>`` or
        ``<<WebViewDownloadFailed>>``. *dest* may be ``None``.
        """
        self._require_tk_thread()
        return self._last_download

    @property
    def last_started_download(self) -> Download | None:
        """Most recent download allowed at start.

        See ``<<WebViewDownloadStarted>>``.
        """
        self._require_tk_thread()
        return self._last_started_download

    @property
    def in_flight_downloads(self) -> tuple[InFlightDownload, ...]:
        """Downloads allowed at start (policy hook) but not yet completed.

        Populated only when :meth:`set_on_download` / ``download_allow`` /
        ``on_download=`` is active — trusted default downloads without a start
        hook do not appear here (see :attr:`last_download` on complete).
        No progress percentage (wry does not expose it — §8.7).
        """
        self._require_tk_thread()
        return tuple(self._in_flight_downloads)

    @property
    def native(self) -> NativeWebView | None:
        """Underlying :class:`tkwry._core.WebView`, or ``None`` if not created."""
        self._require_not_destroyed("native")
        return self._webview

    @property
    def destroyed(self) -> bool:
        """``True`` after :meth:`destroy` or host-frame destruction."""
        self._require_tk_thread()
        return self._destroyed

    @property
    def untrusted(self) -> bool:
        """``True`` when this WebView was created with ``untrusted=True``."""
        self._require_tk_thread()
        return self._untrusted

    @property
    def profile(self) -> str | None:
        """Named profile passed as ``profile=`` at construction, if any."""
        self._require_tk_thread()
        return self._profile_name

    @property
    def clipboard(self) -> bool:
        """Create-time Web Clipboard API opt-in (``clipboard=True``).

        On Windows / Linux this is the wry ``with_clipboard`` flag. On macOS
        the WebView clipboard path is always available; this flag still
        records the constructor value.
        """
        self._require_tk_thread()
        return self._clipboard

    @property
    def javascript_enabled(self) -> bool:
        """Create-time JavaScript flag (default ``True``).

        ``False`` maps to wry ``with_javascript_disabled``. Create-only;
        ``untrusted=True`` does not change this default.
        """
        self._require_tk_thread()
        return self._javascript_enabled

    @property
    def autoplay(self) -> bool:
        """Create-time media autoplay without user gesture (default ``True``).

        Maps to wry ``with_autoplay``. ``False`` keeps the engine's
        user-gesture requirement. Not :attr:`~tkwry.PermissionKind.Autoplay`.
        """
        self._require_tk_thread()
        return self._autoplay

    @property
    def hotkeys_zoom(self) -> bool:
        """Create-time page zoom via Ctrl+/- / Ctrl+wheel / pinch (default ``False``).

        Maps to wry ``with_hotkeys_zoom``. Effective on **Windows** (WebView2).
        macOS / Linux ignore the engine flag; this still records the
        constructor value. Not :meth:`set_zoom` / :meth:`reset_zoom`.
        """
        self._require_tk_thread()
        return self._hotkeys_zoom

    @property
    def back_forward_gestures(self) -> bool:
        """Create-time swipe back/forward navigation (default ``False``).

        Maps to wry ``with_back_forward_navigation_gestures``. Horizontal
        trackpad / touch swipe triggers history navigation on Windows
        (WebView2), macOS (WKWebView), and Linux (WebKitGTK). Not
        :meth:`go_back` / :meth:`go_forward`.
        """
        self._require_tk_thread()
        return self._back_forward_gestures

    @property
    def default_context_menus(self) -> bool:
        """Create-time WebView page context menus (default ``True``).

        Maps to wry ``with_default_context_menus`` (Windows / WebView2).
        ``False`` disables Inspect / Back / Reload on the page menu; Tk
        window chrome menus are unchanged. macOS / Linux ignore the engine
        flag; this still records the constructor value.
        ``untrusted=True`` does not change this default. Not ``devtools=``.
        """
        self._require_tk_thread()
        return self._default_context_menus

    @property
    def https_scheme(self) -> bool:
        """Create-time Windows ``app=`` custom-protocol scheme (default ``True``).

        Maps to wry ``with_https_scheme``. ``True`` uses
        ``https://tkwry.localhost`` (secure context: Service Worker,
        ``crypto.subtle``). ``False`` uses wry's ``http://tkwry.localhost``
        (mixed content easier; **not** a secure context). macOS / Linux
        stay ``tkwry://``; this still records the constructor value.
        """
        self._require_tk_thread()
        return self._https_scheme

    @property
    def proxy(self) -> dict[str, str] | None:
        """Create-time HTTP CONNECT / SOCKSv5 proxy (default ``None``).

        Maps to wry ``with_proxy_config``. Exactly one of
        ``{"http": "host:port"}`` or ``{"socks5": "host:port"}``.
        Credentials are not supported. Returns a shallow copy (or ``None``).
        macOS requires 14.0+ (wry ``mac-proxy`` feature).
        """
        self._require_tk_thread()
        return None if self._proxy is None else dict(self._proxy)

    @property
    def navigation_allow(self) -> frozenset[str] | None:
        """Extra in-webview origins / path prefixes, or ``None`` if unset."""
        self._require_tk_thread()
        return self._navigation_allow

    @property
    def open_external(self) -> bool:
        """``True`` when off-list http(s) is opened in the system browser."""
        self._require_tk_thread()
        return self._open_external

    @property
    def download_allow(self) -> frozenset[str] | None:
        """Download URL origins / path prefixes, or ``None`` if unset."""
        self._require_tk_thread()
        return self._download_allow

    @property
    def csp(self) -> str | None:
        """Content-Security-Policy for ``tkwry://``, or ``None`` if omitted."""
        self._require_tk_thread()
        return self._csp

    @property
    def coop(self) -> bool:
        """``True`` when ``Cross-Origin-Opener-Policy: same-origin`` is sent."""
        self._require_tk_thread()
        return self._coop

    @property
    def corp(self) -> bool:
        """``True`` when ``Cross-Origin-Resource-Policy: same-origin`` is sent."""
        self._require_tk_thread()
        return self._corp

    @property
    def bridge_origins(self) -> BridgeAllowlist:
        """Origins (or origin+path prefixes) allowed to use IPC/RPC.

        ``"*"`` means every page (warns; ``expose`` needs
        ``allow_any_origin=True``).
        """
        self._require_tk_thread()
        return self._bridge_origins

    def set_bridge_origins(self, origins: BridgeOrigins) -> None:
        """Replace the IPC/RPC origin allowlist (``"*"`` or concrete entries).

        Entries may be origins (``https://trusted.example``) or origin+path
        prefixes (``https://trusted.example/app``). ``"*"`` is refused if any
        ``expose()`` registration lacks ``allow_any_origin=True``.
        """
        self._require_not_destroyed("set_bridge_origins")
        if self._untrusted:
            raise ValueError("WebView: untrusted=True cannot change bridge_origins")
        resolved = resolve_bridge_origins(
            origins,
            url=None,
            html=None,
            app=False,
        )
        if resolved == "*":
            missing = [
                name
                for name, reg in self._rpc_methods.items()
                if not reg.allow_any_origin
            ]
            if missing:
                raise ValueError(
                    "set_bridge_origins('*') requires allow_any_origin=True "
                    f"on expose() for: {', '.join(sorted(missing))}"
                )
            warn_star_bridge_origins(stacklevel=4)
        self._bridge_origins = resolved

    @property
    def bridge_allow(self) -> BridgeAllow | None:
        """Optional extra IPC/RPC predicate on the page URL (after the allowlist)."""
        self._require_tk_thread()
        return self._bridge_allow

    def set_bridge_allow(self, predicate: BridgeAllow | None) -> None:
        """Set or clear a callback that further restricts IPC/RPC by page URL.

        Called with the source URL after :attr:`bridge_origins` matches.
        Must return ``bool``; exceptions and non-bool values deny the call.
        """
        self._require_not_destroyed("set_bridge_allow")
        if self._untrusted:
            raise ValueError("WebView: untrusted=True cannot set bridge_allow")
        self._bridge_allow = predicate

    def bind(
        self,
        sequence: str,
        func: Callable[..., object],
        add: Literal["", "+"] | None = None,
    ) -> str:
        """Bind a Tk event on the host frame (e.g. ``\"<<WebViewReady>>\"``)."""
        self._require_not_destroyed("bind")
        result = self._frame.bind(sequence, func, add=add)
        if sequence == "<<WebViewReady>>" and self._ready_delivered:
            self._deliver_late_virtual_event("<<WebViewReady>>", func)
        elif sequence == "<<WebViewCreateFailed>>" and self._failed_delivered:
            self._deliver_late_virtual_event("<<WebViewCreateFailed>>", func)
        return result

    def _deliver_late_virtual_event(
        self, sequence: str, func: Callable[..., object]
    ) -> None:
        def _deliver(
            _func: Callable = func, _frame: tk.Misc = self._frame, _seq: str = sequence
        ) -> None:
            if self._destroyed:
                return
            captured: list[tk.Event] = []
            probe = f"{_seq[:-2]}-Synthetic>>"

            def _capture(evt: tk.Event) -> None:
                captured.append(evt)

            bind_id = _frame.bind(probe, _capture)
            try:
                _frame.event_generate(probe)
            finally:
                _frame.unbind(probe, bind_id)
            if captured:
                evt = captured[0]
            else:
                evt = tk.Event()
                evt.widget = _frame
            self._invoke_callback(_func, evt, kind="bind")

        self._frame.after_idle(_deliver)

    def when_ready(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* once the native view exists and the host is laid out."""
        self._require_not_destroyed("when_ready")
        if self._ready_delivered:

            def _deliver() -> None:
                if self._destroyed:
                    return
                self._invoke_callback(callback, kind="when_ready")

            self._frame.after_idle(_deliver)
        else:
            self._ready_callbacks.append(callback)

    def when_failed(self, callback: CreationFailedHandler) -> None:
        """Schedule *callback* once native creation is permanently abandoned.

        *callback* receives the exception stored in :attr:`creation_error`.
        Prefer this or ``bind(\"<<WebViewCreateFailed>>\")`` over only checking
        after a gated API raises :exc:`~tkwry.WebViewCreationError`.
        """
        self._require_not_destroyed("when_failed")
        err = self._creation_error
        if err is not None and self._failed_delivered:

            def _deliver() -> None:
                if self._destroyed:
                    return
                self._invoke_callback(callback, err, kind="on_creation_failed")

            self._frame.after_idle(_deliver)
            return
        self._failed_callbacks.append(callback)
        if err is not None and not self._failed_pending:
            self._fire_create_failed()

    def wait_until_ready(self, timeout: float = 30.0) -> bool:
        """Pump a nested Tk event loop until the webview is laid out or *timeout*.

        This pumps the Tk event loop via ``update_idletasks`` and ``update``.
        Nested :meth:`wait_until_ready` on the same instance raises
        :exc:`RuntimeError`. Prefer
        :meth:`when_ready` or ``bind(\"<<WebViewReady>>\")`` when you can avoid
        nesting the event loop (especially from handlers that touch Tk state).

        *timeout* must be a finite number of seconds ``> 0`` so unmapped or
        never-laid-out hosts cannot spin forever. Returns ``True`` if ready,
        ``False`` on timeout or :attr:`creation_failed`. Calling after
        :meth:`destroy` raises :exc:`~tkwry.WebViewDestroyedError`. If the
        view is destroyed while waiting, returns ``False``.

        Raises:
            ValueError: if *timeout* is missing, non-positive, or non-finite.
            WebViewDestroyedError: if :meth:`destroy` was already called.
            RuntimeError: if called while another ``wait_until_ready`` is nested
                on this instance.
        """
        self._require_tk_thread()
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite number of seconds > 0")
        timeout_s = float(timeout)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout must be a finite number of seconds > 0")

        if self._destroyed:
            raise WebViewDestroyedError(
                "WebView.destroy() was called; cannot call wait_until_ready()"
            )
        if self.ready:
            return True
        if self._creation_error is not None:
            return False
        if self._wait_until_ready_active:
            raise RuntimeError(
                "wait_until_ready() is already running on this WebView; "
                "nested calls are not supported (this pumps the Tk event loop)"
            )

        root = self._frame.winfo_toplevel()
        deadline = time.monotonic() + timeout_s
        self._wait_until_ready_active = True
        try:
            while not self.ready and not self._destroyed:
                if self._creation_error is not None:
                    return False
                if time.monotonic() >= deadline:
                    return False
                self._pump_wait_until_ready(root)
                time.sleep(0.01)
            return self.ready
        finally:
            self._wait_until_ready_active = False

    def __del__(self) -> None:
        try:
            if not hasattr(self, "_destroyed") or self._destroyed:
                return
            if threading.get_ident() == self._tk_thread_id:
                self._cancel_deferred_callbacks()
                self.destroy()
            else:
                self._schedule_destroy_on_tk_thread()
        except Exception:
            # Still GC-safe (never raise from ``__del__``), but do not hide errors.
            traceback.print_exc()
            if hasattr(self, "_destroyed") and not self._destroyed:
                try:
                    self._teardown_native_if_alive()
                except Exception:
                    traceback.print_exc()

    def _schedule_destroy_on_tk_thread(self) -> None:
        """Best-effort ``destroy()`` when ``__del__`` runs off the Tk thread."""
        tk_thread_id = self._tk_thread_id

        def _run() -> None:
            if threading.get_ident() != tk_thread_id:
                return
            _run_pending_webview_destroy(self)

        if threading.get_ident() == tk_thread_id:
            try:
                self._frame.after(0, _run)
            except (AttributeError, tk.TclError, RuntimeError):
                self._teardown_native_if_alive()
            return

        toplevel = getattr(self, "_toplevel", None)
        if toplevel is None:
            self._teardown_native_if_alive()
            return

        try:
            pending: list[weakref.ReferenceType[WebView]] | None = getattr(
                toplevel, "_tkwry_pending_destroy_webviews", None
            )
            if pending is None:
                pending = []
                setattr(toplevel, "_tkwry_pending_destroy_webviews", pending)
            pending.append(weakref.ref(self))
            _track_atexit_destroy_toplevel(toplevel)
            write_fd = self._tk_wakeup_write_fd
            if write_fd is None:
                write_fd = _toplevel_wakeup_write_fd(toplevel)
            if write_fd is None:
                self._teardown_native_if_alive()
                return
            os.write(write_fd, b"\x01")
        except Exception:
            self._teardown_native_if_alive()

    def _invalidate_eval_generation(self, *, count_pending_drops: bool = False) -> None:
        """Bump the eval epoch and drop Python/native pending callbacks.

        Shared by :meth:`destroy` and emergency off-thread teardown so stale
        WebKit delivers cannot invoke user callbacks after a generation ends.
        """
        self._eval_epoch += 1
        if count_pending_drops and self._pending_eval_tokens:
            self._bump_queue_drop(_QUEUE_DROP_EVAL, len(self._pending_eval_tokens))
        self._pending_eval_callbacks = 0
        self._pending_eval_tokens.clear()
        self._native_eval_wait.clear()

    def _begin_terminal_state(
        self,
        *,
        count_eval_drops: bool,
        clear_ready: bool,
    ) -> None:
        """Shared bookkeeping once ``_destroyed`` has been set to True.

        Keeps :meth:`destroy` and :meth:`_teardown_native_if_alive` aligned so
        eval / fire-and-forget JS / sync hooks cannot diverge per exit path.
        """
        self._invalidate_eval_generation(count_pending_drops=count_eval_drops)
        self._pending_eval_js = None
        self._eval_js_scheduled = False
        self._abort_sync_hooks()
        self._discard_navigation_error_queue()
        self._in_flight_downloads.clear()
        if clear_ready:
            self._ready_delivered = False
            self._ready_pending = False
            self._ready_callbacks.clear()

    def _detach_from_host(self, *, unbind_events: bool) -> None:
        """Drop frame-host claim and sync-hook registration (Tcl-safe-ish)."""
        try:
            _release_frame_host(self._frame, self)
        except Exception:
            pass
        if unbind_events:
            self._unbind_frame_events()
        _unregister_sync_hook_webview(self)

    def _unregister_platform_webview(self) -> None:
        if sys.platform == "darwin":
            try:
                _unregister_macos_webview(self)
            except Exception:
                pass

    def _teardown_native_if_alive(self) -> None:
        """Release the native WebView when Tk teardown is impossible."""
        if self._destroyed:
            return
        self._destroyed = True
        if self._session is not None:
            self._session._unregister_webview(self)
        self._abort_inflight_rpc()
        self._stop_app_watch()
        # Emergency path: skip Tcl ``after`` cancel / frame unbind; still clear
        # eval generation and ready callbacks so GC cannot revive work.
        self._begin_terminal_state(count_eval_drops=False, clear_ready=True)
        self._rpc_methods.clear()
        self._shutdown_rpc_executor()
        self._detach_from_host(unbind_events=False)
        self._release_native_view(hide=False)
        self._unregister_platform_webview()
        # Deferred native teardown still needs the poll (arm via release path).
        self._stop_event_poll_if_idle()

    def destroy(self) -> None:
        """Hide and release the native webview without destroying the host frame.

        The instance cannot be reused after this call; create a new ``WebView``
        if you need another embedded view. Further APIs raise
        :exc:`~tkwry.WebViewDestroyedError` except snapshot properties
        (``destroyed``, ``phase``, ``last_*``, …),
        :meth:`take_queue_drop_counts` / :meth:`take_queue_drop_stats`, and a
        second ``destroy()``
        (idempotent). :meth:`wait_until_ready` after destroy raises; if
        destroy happens during a wait, that wait returns ``False``.

        In-flight RPC is cancelled cooperatively (``rpc_cancelled()``). Worker
        pool threads are joined for at most ~2 seconds; Python cannot preempt
        uncooperative handlers, so they may briefly outlive the WebView.
        """
        self._require_tk_thread()
        if self._destroyed:
            return
        self._destroyed = True
        self._dispose_context_menu_tk()
        self._context_menu_items = None
        self._context_menu_handler = None
        self._inject_scripts.clear()
        if self._session is not None:
            self._session._unregister_webview(self)
        self._abort_inflight_rpc()
        self._stop_app_watch()
        self._cancel_deferred_callbacks()
        self._begin_terminal_state(count_eval_drops=True, clear_ready=True)
        self._rpc_methods.clear()
        self._shutdown_rpc_executor()
        self._detach_from_host(unbind_events=True)
        had_native = self._webview is not None
        self._release_native_view(hide=True)
        self._release_tk_wakeup_pipe_registration()
        self._unregister_platform_webview()
        if sys.platform == "linux":
            GtkPump.detach(self._frame)
            if had_native or self._native_teardown_pending is not None:
                from tkwry._linux import pump_gtk_unless_active

                # After detach: last view still needs a sync flush; siblings keep
                # GtkPump so unless_active skips nested bursts.
                for _ in range(_NATIVE_TEARDOWN_MAX_ATTEMPTS):
                    if self._native_teardown_pending is not None:
                        self._finish_native_teardown()
                    pump_gtk_unless_active(self._frame)
                    if self._native_teardown_pending is None:
                        break
                    try:
                        self._toplevel.update_idletasks()
                        self._toplevel.update()
                    except tk.TclError:
                        break
                if self._native_teardown_pending is not None:
                    pending = self._native_teardown_pending
                    try:
                        pending.force_destroy()
                    except Exception:
                        traceback.print_exc()
                    self._clear_native_teardown()
        if self._native_teardown_pending is not None:
            self._ensure_event_poll()
        else:
            self._stop_event_poll_if_idle()

    def _unbind_frame_events(self) -> None:
        """Drop host-frame binds so ``destroy()`` does not pin this instance."""
        for sequence, funcid in self._frame_bind_ids:
            try:
                self._frame.unbind(sequence, funcid)
            except tk.TclError:
                pass
        self._frame_bind_ids.clear()

    def _ensure_gtk_pump_attached(self) -> None:
        if sys.platform != "linux" or self._destroyed or self._webview is None:
            return
        GtkPump.ensure_attached(self._frame)

    def _attach_gtk_pump_for_native(self) -> None:
        if sys.platform != "linux" or self._destroyed:
            return
        GtkPump.ensure_attached(self._frame)

    def _native_is_alive(self, native: NativeWebView) -> bool:
        try:
            return native.is_alive()
        except Exception:
            return False

    def _hide_native_view(self, native: NativeWebView) -> None:
        try:
            native.set_visible(False)
        except Exception:
            pass

    def _show_native_view(self, native: NativeWebView) -> bool:
        """Map-axis show counterpart to ``_hide_native_view``."""
        try:
            native.set_visible(True)
            return True
        except Exception:
            return False

    def _arm_native_teardown(self, native: NativeWebView) -> None:
        """Remember a native that did not die on ``destroy()`` yet."""
        self._native_teardown_pending = native
        self._native_teardown_attempts = 0

    def _clear_native_teardown(self) -> None:
        """Drop deferred-teardown state only (does not stop the event poll)."""
        self._native_teardown_pending = None
        self._native_teardown_attempts = 0

    def _release_native_view(self, *, hide: bool) -> None:
        native = self._webview
        if native is None:
            return
        if hide:
            self._hide_native_view(native)
        try:
            native.destroy()
        except Exception:
            traceback.print_exc()
        if self._native_is_alive(native):
            self._arm_native_teardown(native)
        self._webview = None
        if self._native_teardown_pending is not None:
            self._ensure_event_poll()

    def _force_native_teardown(self) -> None:
        """Best-effort native release when Tk-thread destroy is unavailable."""
        native = self._webview
        if native is None and self._native_teardown_pending is not None:
            native = self._native_teardown_pending
        if native is None:
            return
        try:
            native.force_destroy()
        except Exception:
            traceback.print_exc()
        self._clear_native_teardown()
        self._webview = None

    def _finish_native_teardown(self) -> None:
        native = self._native_teardown_pending
        if native is None:
            return
        try:
            if self._native_is_alive(native):
                self._hide_native_view(native)
                native.destroy()
            if not self._native_is_alive(native):
                self._clear_native_teardown()
                self._stop_event_poll_if_idle()
                return
            self._native_teardown_attempts += 1
            if self._native_teardown_attempts >= _NATIVE_TEARDOWN_MAX_ATTEMPTS:
                print(
                    "tkwry: native teardown timed out after "
                    f"{_NATIVE_TEARDOWN_MAX_ATTEMPTS} poll attempts; "
                    "forcing release",
                    file=sys.stderr,
                )
                try:
                    native.force_destroy()
                except Exception:
                    traceback.print_exc()
                self._clear_native_teardown()
                self._stop_event_poll_if_idle()
        except Exception:
            traceback.print_exc()

    def _clear_initial_load(self) -> None:
        """Drop constructor deferred load so user nav/reload cannot overwrite it."""
        self._cancel_initial_load_timer()
        self._initial_load = None

    def _arm_initial_load(self, load: _PendingLoad) -> None:
        """Remember constructor ``url``/``html`` until ``_run_initial_load``."""
        self._initial_load = load

    def _set_pending_load(
        self,
        kind: Literal["url", "html"],
        payload: str,
        *,
        headers: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        """Write coalesced post-create load without touching ``_initial_load``."""
        if kind == "url":
            self._pending_load = ("url", payload, headers)
        else:
            self._pending_load = ("html", payload)

    def _queue_user_load(
        self,
        kind: Literal["url", "html"],
        payload: str,
        *,
        headers: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        """User ``load_*`` last-wins: supersede constructor load and set pending."""
        self._clear_initial_load()
        self._set_pending_load(
            kind, payload, headers=headers if kind == "url" else None
        )

    def _clear_pending_load(self) -> None:
        self._pending_load = None

    def _set_precreate_url(
        self,
        url: str,
        *,
        headers: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        """Store URL applied at native create (clears pending HTML)."""
        self._pending_url = url
        self._pending_url_headers = headers
        self._pending_html = None

    def _set_precreate_html(self, html: str) -> None:
        """Store HTML applied at native create (clears pending URL)."""
        self._pending_html = html
        self._pending_url = None
        self._pending_url_headers = None

    def _clear_precreate_pending(self) -> None:
        """Drop pre-create URL/HTML after they are consumed into ``_initial_load``."""
        self._pending_url = None
        self._pending_url_headers = None
        self._pending_html = None

    def _apply_load_to_native(self, load: _PendingLoad) -> None:
        """Push one coalesced load to the native WebView (must exist)."""
        native = self._webview
        if native is None:
            return
        if load[0] == "url":
            _, payload, headers = load
            if headers:
                native.load_url_with_headers(payload, list(headers))
            else:
                native.load_url(payload)
        else:
            _, payload = load
            native.load_html(payload)

    def load_url(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Navigate to *url* (``http``/``https``/``file``/``tkwry``; scheme optional).

        Local filesystem paths (``/path/to/page.html``, ``C:\\page.html``) are
        normalized to ``file://`` URLs so relative assets resolve correctly.
        ``tkwry://localhost/...`` URLs require constructor ``app=`` (custom
        protocol root fixed at create time).

        Optional *headers* are sent on **this navigation request only** (not
        injected into in-page ``fetch`` / subresources). Requires an
        ``http``/``https`` URL. Header values are never written to tkwry logs
        or exception messages. A ``User-Agent`` header here does **not** rewrite
        ``navigator.userAgent`` (create-time ``user_agent=`` is the identity
        path). Redirect following is engine-defined.

        Multiple rapid calls are coalesced (**last-wins**): only the final URL
        (and its headers) is loaded. Before the native view exists, the URL is
        stored and applied at creation (unless superseded by :meth:`load_html`).
        """
        self._require_not_destroyed("load_url")
        normalized = _normalize_url(url)
        _validate_url(normalized)
        header_items = _normalize_load_headers(headers)
        if header_items is not None:
            if not (
                normalized.startswith("http://") or normalized.startswith("https://")
            ):
                raise ValueError("load_url headers= requires an http(s) URL")
        if normalized.startswith("tkwry:") and self._app_root is None:
            raise ValueError(
                "tkwry:// URLs require app= (custom protocol root) at create"
            )
        if self._webview is None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call load_url()"
            ) from self._creation_error
        if self._webview is None:
            self._set_precreate_url(normalized, headers=header_items)
            return
        if self._sync_hook_depth > 0:
            self._queue_user_load("url", normalized, headers=header_items)
            self._track_after(self._frame.after_idle(self._flush_load))
            return
        self._queue_user_load("url", normalized, headers=header_items)
        self._dispatch_pending_load()

    def load_html(self, html: str) -> None:
        """Load inline HTML.

        Like :meth:`load_url`, rapid calls are coalesced (**last-wins**).
        ``load_html`` supersedes any pending :meth:`load_url` call.
        """
        self._require_not_destroyed("load_html")
        if self._webview is None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call load_html()"
            ) from self._creation_error
        if self._webview is None:
            self._set_precreate_html(html)
            return
        if self._sync_hook_depth > 0:
            self._queue_user_load("html", html)
            self._track_after(self._frame.after_idle(self._flush_load))
            return
        self._queue_user_load("html", html)
        self._dispatch_pending_load()

    def _dispatch_pending_load(self) -> None:
        """Run coalesced load on Linux immediately when not inside event poll."""
        if (
            sys.platform == "linux"
            and self._webview is not None
            and self._sync_hook_depth == 0
            and not self._in_poll_events
        ):
            if self._flush_load_scheduled:
                return
            self._flush_load_scheduled = True
            self._flush_load()
            return
        self._schedule_flush_load()

    def reload(self) -> None:
        """Reload the current document.

        Clears pending constructor / coalesced ``load_*`` so they cannot
        overwrite this reload.
        """
        native = self._require_ready("reload")
        # Supersede constructor deferred load so it cannot overwrite this reload.
        self._clear_initial_load()
        # Drop any idle-coalesced load_url/load_html so it cannot overwrite reload.
        self._clear_pending_load()
        self._flush_load_attempt = 0
        if self._sync_hook_depth > 0:
            self._track_after(self._frame.after_idle(self._run_deferred_reload))
            return
        native.reload()
        if self._page_load_listening_wanted():
            self._ensure_event_poll()
        self._finish_navigation()

    def go_back(self) -> None:
        """Go to the previous page in history."""
        native = self._require_ready("go_back")
        native.go_back()
        if self._page_load_listening_wanted():
            self._ensure_event_poll()
        self._finish_navigation()

    def go_forward(self) -> None:
        """Go to the next page in history."""
        native = self._require_ready("go_forward")
        native.go_forward()
        if self._page_load_listening_wanted():
            self._ensure_event_poll()
        self._finish_navigation()

    def can_go_back(self) -> bool:
        """Return whether history can go back."""
        return self._require_ready("can_go_back").can_go_back()

    def can_go_forward(self) -> bool:
        """Return whether history can go forward."""
        return self._require_ready("can_go_forward").can_go_forward()

    def print(self) -> None:
        """Open the platform print dialog for the current page.

        Fire-and-forget: wry shows the system dialog and does not report
        success, cancel, or a PDF. There is no return value and no
        ``print_to_pdf``. Window title / icon / geometry belong on the host
        :class:`tkinter.Toplevel`, not this method.

        For macOS margins, see :meth:`print_with_options`.
        """
        self._require_ready("print").print()

    def print_with_options(
        self,
        *,
        top: float = 0.0,
        right: float = 0.0,
        bottom: float = 0.0,
        left: float = 0.0,
    ) -> None:
        """Open the print dialog with page margins (**macOS only**).

        Wraps wry ``WebViewExtMacOS::print_with_options`` (margin points).
        Still fire-and-forget — no PDF / success / cancel result. On Windows
        and Linux raises :class:`OSError`; use :meth:`print` there.
        """
        self._require_ready("print_with_options").print_with_options(
            top=top, right=right, bottom=bottom, left=left
        )

    def set_zoom(self, scale: float) -> None:
        """Set the page zoom factor (``1.0`` = 100%).

        Wraps wry ``WebView::zoom``. Tk-thread and ready required. Engine
        range is platform-defined (WebView2 typically accepts about
        ``0.25``–``5.0``; WKWebView ``pageZoom`` on macOS 11+; WebKitGTK
        ``zoom-level``). tkwry does not clamp — out-of-range values raise
        from the engine when it rejects them.

        This is **page** zoom, not Tk window zoom/iconify (those belong on
        the host :class:`~tkinter.Toplevel`).
        """
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("scale must be a finite float")
        scale_f = float(scale)
        if not math.isfinite(scale_f):
            raise ValueError("scale must be a finite float")
        self._require_ready("set_zoom").set_zoom(scale_f)

    def reset_zoom(self) -> None:
        """Reset page zoom to ``1.0`` (same as ``set_zoom(1.0)``)."""
        self._require_ready("reset_zoom").set_zoom(1.0)

    def cookies(self) -> list[Cookie]:
        """Return all cookies known to this WebView's engine store.

        Tk-thread only. Shared across views that use the same
        :class:`~tkwry.WebSession`. Do not log returned ``Cookie.value``.
        """
        return list(self._require_ready("cookies").cookies())

    def cookies_for_url(self, url: str) -> list[Cookie]:
        """Return cookies the engine would send for *url*.

        Tk-thread only. Do not log returned ``Cookie.value``.
        """
        return list(self._require_ready("cookies_for_url").cookies_for_url(url))

    def set_cookie(self, cookie: Cookie) -> None:
        """Store *cookie* in this WebView's engine cookie jar.

        Tk-thread only. Engine support varies; values are never written to
        tkwry logs.
        """
        self._require_ready("set_cookie").set_cookie(cookie)

    @overload
    def delete_cookie(self, cookie: Cookie, url: None = None) -> None: ...

    @overload
    def delete_cookie(self, cookie: str, url: str) -> None: ...

    def delete_cookie(self, cookie: Cookie | str, url: str | None = None) -> None:
        """Delete a matching cookie from this WebView's engine jar.

        Pass a :class:`~tkwry.Cookie`, or a cookie *name* plus the page *url*
        it belongs to. The name+url form builds ``Cookie(name, "", domain,
        path)`` from the URL hostname and path (empty path → ``/``). Matching
        is engine-defined (typically name + domain + path). Tk-thread only.
        """
        native = self._require_ready("delete_cookie")
        if url is not None:
            if not isinstance(cookie, str):
                raise TypeError(
                    "delete_cookie(name, url) requires name as str; "
                    "pass a Cookie as the only argument"
                )
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                raise ValueError("delete_cookie url must include a hostname")
            path = parsed.path if parsed.path else "/"
            native.delete_cookie(Cookie(cookie, "", domain=host, path=path))
            return
        if isinstance(cookie, str):
            raise TypeError("delete_cookie(name, url) requires a url")
        native.delete_cookie(cookie)

    def clear_all_browsing_data(self) -> None:
        """Clear browsing data for this WebView (cookies, cache, etc.).

        Wraps wry ``clear_all_browsing_data`` on the **WebView**, not a
        session-wide CDP wipe. Sibling views that share a
        :class:`~tkwry.WebSession` may still see overlapping storage until
        their stores are cleared too — engine-dependent. Tk-thread only.
        """
        self._require_ready("clear_all_browsing_data").clear_all_browsing_data()

    def _run_deferred_reload(self) -> None:
        if self._destroyed or self._webview is None:
            return
        try:
            self._webview.reload()
        except Exception:
            traceback.print_exc()
            return
        if self._page_load_listening_wanted():
            self._ensure_event_poll()
        self._finish_navigation()

    def eval_js(self, script: str, *, on_error: EvalErrorHandler | None = None) -> None:
        """Evaluate JavaScript without waiting for a result.

        The script is scheduled on the Tk idle loop (not synchronous). There is
        no return value; use :meth:`eval_js_with_callback` when you need the
        result. Failures call *on_error* (if set) and generate
        ``<<WebViewEvalFailed>>`` (``last_eval_error``); without *on_error*
        the traceback is also printed to stderr.
        """
        self._require_ready("eval_js")
        self._pending_eval_js = (script, on_error)
        self._schedule_eval_js()

    def eval_js_with_callback(
        self,
        script: str,
        callback: EvalCallback,
        *,
        on_error: EvalErrorHandler | None = None,
    ) -> None:
        """Evaluate JavaScript and invoke *callback* with the result string.

        Asynchronous: *callback* runs on the **Tk main thread** after the script
        completes. The result is always a ``str`` (including JSON literals).
        Failures and the 30s timeout call *on_error* (if set) with
        :class:`~tkwry.WebViewTimeoutError` on timeout, generate
        ``<<WebViewEvalFailed>>``, and set ``last_eval_error``. Without
        *on_error* the error is also printed to stderr; *callback* is not
        invoked on timeout.
        """
        self._require_ready("eval_js_with_callback")
        epoch = self._eval_epoch
        token = self._register_pending_eval(callback, on_error)
        self._ensure_event_poll()

        def _run() -> None:
            if self._destroyed or self._webview is None or epoch != self._eval_epoch:
                self._release_pending_eval(token)
                return

            try:
                native_token = self._webview.eval_js_with_callback(
                    script, _noop_native_eval_callback
                )
            except Exception as exc:
                self._release_pending_eval(token)
                self._signal_eval_error(exc, on_error=on_error)
                return
            self._native_eval_wait[native_token] = (
                epoch,
                token,
                callback,
                on_error,
            )

        self._frame.after_idle(_run)

    def focus(self) -> None:
        """Move keyboard focus to the WebView (``makeFirstResponder`` on macOS)."""
        native = self._require_ready("focus")
        native.focus()
        if sys.platform == "darwin":
            toplevel = self._frame.winfo_toplevel()
            _set_mac_webviews_input_active(toplevel, self)
            _release_tk_keyboard_focus(toplevel)

    def focus_parent(self) -> None:
        """Return keyboard focus to the native parent view (macOS Tk coexistence)."""
        native = self._require_ready("focus_parent")
        native.focus_parent()
        if sys.platform == "darwin":
            _set_mac_webviews_input_active(self._frame.winfo_toplevel(), None)

    def set_background_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        """Set the native WebView background color (RGBA 0–255)."""
        native = self._require_ready("set_background_color")
        for val, name in ((r, "r"), (g, "g"), (b, "b"), (a, "a")):
            _validate_color_component(val, name)
        self._background_color = (r, g, b, a)
        native.set_background_color(r, g, b, a)

    def set_user_agent(self, user_agent: str | None) -> None:
        """Set the user agent applied when the native view is first created."""
        self._require_not_destroyed("set_user_agent")
        if self._webview is not None:
            raise ValueError(
                "user_agent cannot be changed after the native WebView is created"
            )
        self._user_agent = user_agent

    def set_initialization_script(self, script: str | None) -> None:
        """Set the primary initialization script for native create.

        Create-only. Prefer :meth:`add_init_script` to append additional
        pre-load scripts, or :meth:`inject_script` / :meth:`execute_script`
        for the named injection tiers.
        """
        self._require_not_destroyed("set_initialization_script")
        if self._webview is not None:
            raise ValueError(
                "initialization_script cannot be changed after the native "
                "WebView is created"
            )
        self._initialization_script = script

    def add_init_script(self, script: str) -> None:
        """Append a pre-load script that runs on every navigation (create-time).

        Combined with ``initialization_script=`` / :meth:`set_initialization_script`
        when the native WebView is built (wry ``with_initialization_script``).
        Raises if the native view already exists — wry cannot add init scripts
        after create; use :meth:`inject_script` for post-create persistence.
        """
        self._require_not_destroyed("add_init_script")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call add_init_script()"
            ) from self._creation_error
        if not isinstance(script, str) or not script.strip():
            raise ValueError("add_init_script: script must be a non-empty str")
        if self._webview is not None:
            raise ValueError(
                "add_init_script cannot be used after the native WebView is "
                "created; use inject_script() for post-create injection"
            )
        self._init_scripts.append(script)

    def execute_script(
        self, script: str, *, on_error: EvalErrorHandler | None = None
    ) -> None:
        """Run JavaScript once in the current document (alias of :meth:`eval_js`).

        Does not re-run on later navigations. For pre-load scripts use
        :meth:`add_init_script`; for best-effort re-injection after create use
        :meth:`inject_script`.
        """
        self._require_not_destroyed("execute_script")
        self.eval_js(script, on_error=on_error)

    def inject_script(self, script: str) -> None:
        """Inject a script intended to persist across navigations.

        - Before native create: same as :meth:`add_init_script` (engine
          initialization script).
        - After ready: runs immediately via :meth:`eval_js`, then re-runs on
          each ``PageLoadEvent.Started`` (best-effort — not identical to wry
          initialization scripts, which run before ``window.onload``).
        """
        self._require_not_destroyed("inject_script")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call inject_script()"
            ) from self._creation_error
        if not isinstance(script, str) or not script.strip():
            raise ValueError("inject_script: script must be a non-empty str")
        if self._webview is None:
            self.add_init_script(script)
            return
        self._require_ready("inject_script")
        self._inject_scripts.append(script)
        self._sync_page_load_listening()
        self.eval_js(script)

    def _user_initialization_script(self) -> str | None:
        parts: list[str] = []
        if self._initialization_script:
            parts.append(self._initialization_script)
        parts.extend(self._init_scripts)
        if not parts:
            return None
        return "\n".join(parts)

    def _page_load_listening_wanted(self) -> bool:
        return self._on_page_load is not None or bool(self._inject_scripts)

    def _sync_page_load_listening(self) -> None:
        if self._webview is None:
            return
        wanted = self._page_load_listening_wanted()
        self._webview.set_page_load_listening(wanted)
        if wanted:
            self._ensure_event_poll()

    def open_devtools(self) -> None:
        """Open the platform DevTools / inspector (private APIs on macOS)."""
        self._require_ready("open_devtools").open_devtools()

    def close_devtools(self) -> None:
        """Close DevTools if open."""
        self._require_ready("close_devtools").close_devtools()

    def is_devtools_open(self) -> bool:
        """Return whether DevTools is currently open."""
        return self._require_ready("is_devtools_open").is_devtools_open()

    def set_on_navigation(self, handler: NavigationHandler | None) -> None:
        """Register a navigation allow/deny hook (Tk main thread; WebKit waits).

        The handler receives a :class:`~tkwry.NavigationEvent` or, for legacy
        code, the URL ``str``. Return ``True`` to allow or ``False`` to block.
        When set, the handler replaces built-in ``navigation_allow`` /
        ``open_external`` policy for navigations.
        """
        self._require_not_destroyed("set_on_navigation")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_navigation()"
            ) from self._creation_error
        self._on_navigation = handler
        self._sync_navigation_hook()

    def set_navigation_policy(self, handler: NavigationPolicyHandler | None) -> None:
        """Register a URL policy hook using :class:`~tkwry.NavigationEvent`.

        Example: ``set_navigation_policy(lambda e: e.url.startswith("https://"))``.
        Ignored when :meth:`set_on_navigation` is set — use one hook or combine
        logic in ``on_navigation``.
        """
        self._require_not_destroyed("set_navigation_policy")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_navigation_policy()"
            ) from self._creation_error
        self._navigation_policy = handler
        self._sync_navigation_hook()

    def _sync_navigation_hook(self) -> None:
        if self._webview is not None:
            if self._on_navigation is not None or self._navigation_policy_active():
                self._webview.set_on_navigation(self._native_navigation)
            else:
                self._webview.clear_on_navigation()
        if self._on_navigation is not None or self._navigation_policy_active():
            self._ensure_tk_wakeup_pipe()
            self._ensure_event_poll()

    def set_on_page_load(self, handler: PageLoadHandler | None) -> None:
        """Register a page-load handler (Tk main thread; listening follows handler).

        Navigations that occurred with no handler are not replayed. Clearing
        with ``None`` stops native page-load collection.
        """
        self._require_not_destroyed("set_on_page_load")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_page_load()"
            ) from self._creation_error
        self._on_page_load = handler
        self._sync_page_load_listening()
        if self._page_load_listening_wanted():
            self._deliver_page_load_events()

    def sync_bounds(self) -> None:
        """Push the host frame's size and position to the native WebView.

        Called automatically on ``<Configure>``, ``<Map>``, and ``<Unmap>``.
        Call this manually after layout changes that do not emit Configure
        (e.g. custom geometry) so the WebView reflows — useful for centered
        images and responsive content.

        Size source of truth is the host's mapped ``winfo_width`` /
        ``winfo_height`` (when ``> 1``). Constructor or ``place`` dimensions
        are only used before Tk reports a real size.
        """
        self._require_not_destroyed("sync_bounds")
        if self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call sync_bounds()"
            ) from self._creation_error
        self._sync_bounds_and_stacking()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Native view bounds in ``set_bounds`` space: ``(x, y, width, height)``."""
        return self._require_ready("bounds").bounds()

    def take_queue_drop_counts(self) -> tuple[int, int, int, int, int, int]:
        """Return overflow drop counts since the last call.

        Returns ``(ipc, page_load, title, drag_drop, eval, rpc)``. Each internal
        queue caps at 2048 pending items; additional events are compacted or
        discarded and counted here so applications can detect handler backlogs.
        RPC uses a dedicated queue so IPC overflow cannot drop ``tkwry.call``.
        Readable after :meth:`destroy` so local drops counted during teardown
        (for example pending evals) are not lost.

        Does **not** include ``download_complete`` or ``rpc_stream`` overflows —
        use :meth:`take_queue_drop_stats` for the full named snapshot. Calling
        either method resets the shared six counters.
        """
        self._require_tk_thread()
        local = self._take_local_queue_drop_counts()
        if self._destroyed or self._webview is None:
            return local
        native = self._webview.take_queue_drop_counts()
        return (
            local[0] + native[0],
            local[1] + native[1],
            local[2] + native[2],
            local[3] + native[3],
            local[4] + native[4],
            local[5] + native[5],
        )

    def take_queue_drop_stats(self) -> QueueDropCounts:
        """Return named queue overflow counts since the last stats (or counts) take.

        Includes the legacy six fields plus ``download_complete`` (native
        download-complete queue) and ``rpc_stream`` (worker→Tk stream chunk
        queue). Resets those counters (and the shared six). Readable after
        :meth:`destroy`. Prefer this over :meth:`take_queue_drop_counts` for
        new code.
        """
        self._require_tk_thread()
        local = self._take_local_queue_drop_counts()
        rpc_stream = self._take_rpc_stream_dropped()
        if self._destroyed or self._webview is None:
            return QueueDropCounts(*local, download_complete=0, rpc_stream=rpc_stream)
        native = self._webview.take_queue_drop_stats()
        return QueueDropCounts(
            local[0] + native[0],
            local[1] + native[1],
            local[2] + native[2],
            local[3] + native[3],
            local[4] + native[4],
            local[5] + native[5],
            native[6],
            rpc_stream,
        )

    def set_on_title_changed(self, handler: TitleChangedHandler | None) -> None:
        """Register a document-title handler (Tk main thread)."""
        self._require_not_destroyed("set_on_title_changed")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_title_changed()"
            ) from self._creation_error
        self._on_title_changed = handler
        if self._webview is not None:
            self._webview.set_title_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_new_window(self, handler: NewWindowHandler | None) -> None:
        """Register a new-window hook (Tk main thread; WebKit waits for a response)."""
        self._require_not_destroyed("set_on_new_window")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_new_window()"
            ) from self._creation_error
        self._on_new_window = handler
        if self._webview is not None:
            if handler is not None or self._new_window_policy_active():
                self._webview.set_on_new_window(self._native_new_window)
            else:
                self._webview.clear_on_new_window()
        if handler is not None or self._new_window_policy_active():
            self._ensure_tk_wakeup_pipe()
            self._ensure_event_poll()

    def set_drag_drop_handler(self, handler: DragDropHandler | None) -> None:
        """Register a notify-only drop handler (runs on the Tk main thread).

        Events are queued from the WebKit thread; the handler cannot accept or
        deny the OS drop. Clearing with ``None`` stops native collection.
        """
        self._require_not_destroyed("set_drag_drop_handler")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_drag_drop_handler()"
            ) from self._creation_error
        self._drag_drop_handler = handler
        if self._webview is not None:
            self._webview.set_drag_drop_listening(handler is not None)
        if handler is not None:
            self._ensure_event_poll()

    def set_on_download(self, handler: DownloadHandler | None) -> None:
        """Register a download start hook (Tk main thread; WebKit waits).

        *handler* receives a :class:`~tkwry.Download` when it accepts one
        argument, or legacy ``(url, suggested_dest)``. It may return ``True``
        (allow suggested path), ``False`` / ``None`` (cancel), an **absolute**
        ``str`` / ``Path`` save location, or ``download.save(directory)``.
        Relative dests are denied. Use :func:`~tkwry.unique_download_path`
        when the suggested name already exists; tkwry does not pick an
        overwrite policy.
        """
        self._require_not_destroyed("set_on_download")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_download()"
            ) from self._creation_error
        self._on_download = handler
        if self._webview is not None:
            if self._download_policy_active():
                self._webview.set_on_download_started(self._native_download_started)
            else:
                self._webview.clear_on_download_started()
        if self._download_policy_active():
            self._ensure_tk_wakeup_pipe()
            self._ensure_event_poll()

    def set_on_download_started(self, handler: DownloadStartedHandler | None) -> None:
        """Register a notify-only handler when a download is allowed to start.

        Also generates ``<<WebViewDownloadStarted>>``. Inspect
        :attr:`last_started_download` or the ``Download`` argument.
        """
        self._require_not_destroyed("set_on_download_started")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_download_started()"
            ) from self._creation_error
        self._on_download_started = handler

    def set_on_download_complete(self, handler: DownloadCompleteHandler | None) -> None:
        """Register a download-finished handler (Tk main thread; notify-only).

        Completions also set :attr:`last_download` and generate
        ``<<WebViewDownloadComplete>>`` / ``<<WebViewDownloadFailed>>``
        whether or not a handler is registered.
        """
        self._require_not_destroyed("set_on_download_complete")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_download_complete()"
            ) from self._creation_error
        self._on_download_complete = handler
        if self._webview is not None:
            self._webview.set_download_complete_listening(True)
        self._ensure_event_poll()

    def set_on_download_failed(self, handler: DownloadFailedHandler | None) -> None:
        """Register a notify-only handler when a download finishes unsuccessfully.

        Also generates ``<<WebViewDownloadFailed>>``. :attr:`last_download`
        still receives ``(url, dest, success=False)``.
        """
        self._require_not_destroyed("set_on_download_failed")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_on_download_failed()"
            ) from self._creation_error
        self._on_download_failed = handler

    def set_context_menu(self, items: Sequence[ContextMenuItem] | None) -> None:
        """Register a Tk context menu shown on page right-click.

        *items* is a sequence of ``(label, callback)``; ``label is None``
        inserts a separator. Uses a JavaScript ``contextmenu`` bridge
        (``preventDefault`` + IPC). On Windows, requires
        ``default_context_menus=False`` (auto-forced before native create).

        Ignored when :meth:`set_context_menu_handler` is set — the handler
        takes priority.
        """
        self._require_not_destroyed("set_context_menu")
        if items is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_context_menu()"
            ) from self._creation_error
        self._context_menu_items = normalize_context_menu_items(items)
        self._dispose_context_menu_tk()
        if self._context_menu_active():
            self._require_windows_context_menu_ready("set_context_menu")
            self._enable_context_menu_bridge()
        elif self._webview is not None:
            self._webview.set_ipc_listening(self._ipc_listening_wanted())

    def set_context_menu_handler(self, handler: ContextMenuHandler | None) -> None:
        """Register a host handler for page context-menu events.

        *handler* receives a :class:`~tkwry.ContextMenuEvent`. When set, it
        takes priority over :meth:`set_context_menu`. Pass ``None`` to clear.
        """
        self._require_not_destroyed("set_context_menu_handler")
        if handler is not None and self._creation_error is not None:
            raise WebViewCreationError(
                "WebView native creation failed; cannot call set_context_menu_handler()"
            ) from self._creation_error
        self._context_menu_handler = handler
        self._dispose_context_menu_tk()
        if self._context_menu_active():
            self._require_windows_context_menu_ready("set_context_menu_handler")
            self._enable_context_menu_bridge()
        elif self._webview is not None:
            self._webview.set_ipc_listening(self._ipc_listening_wanted())

    def _require_windows_context_menu_ready(self, method: str) -> None:
        if sys.platform != "win32":
            return
        if not self._default_context_menus:
            return
        if self._webview is None:
            print(
                f"tkwry: {method} requires default_context_menus=False on "
                "Windows; forcing False before native create",
                file=sys.stderr,
            )
            self._default_context_menus = False
            return
        raise ValueError(
            f"{method}: on Windows pass default_context_menus=False at "
            "construction (cannot disable after the native WebView exists)"
        )

    def _dispose_context_menu_tk(self) -> None:
        menu = self._context_menu_tk
        self._context_menu_tk = None
        if menu is None:
            return
        try:
            menu.destroy()
        except tk.TclError:
            pass

    def _deliver_context_menu_event(self, event: ContextMenuEvent) -> None:
        if self._destroyed:
            return
        handler = self._context_menu_handler
        if handler is not None:
            self._invoke_callback(handler, event, kind="on_context_menu")
            return
        items = self._context_menu_items
        if items is None:
            return
        self._popup_context_menu(event, items)

    def _popup_context_menu(
        self, event: ContextMenuEvent, items: tuple[ContextMenuItem, ...]
    ) -> None:
        self._dispose_context_menu_tk()
        try:
            menu = tk.Menu(self._frame, tearoff=0)
        except tk.TclError:
            return
        self._context_menu_tk = menu
        for label, callback in items:
            if label is None:
                menu.add_separator()
                continue
            assert callback is not None

            def _run(cb: ContextMenuCallback = callback) -> None:
                self._invoke_callback(cb, kind="context_menu_item")

            menu.add_command(label=label, command=_run)
        try:
            menu.tk_popup(event.x, event.y)
        except tk.TclError:
            self._dispose_context_menu_tk()
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def set_on_callback_error(self, handler: CallbackErrorHandler | None) -> None:
        """Register a provisional hook for exceptions in user callbacks.

        *handler* receives ``(exc, kind)`` where ``kind`` names the failing
        hook (e.g. ``"on_page_load"``, ``"ipc_handler"``). When unset,
        failures are logged to stderr (default). Not part of the stable
        public contract — may change in 0.2.x.
        """
        self._require_not_destroyed("set_on_callback_error")
        self._on_callback_error = handler

    def _schedule_try_create(self, *, delay_ms: int | None = None) -> None:
        if (
            self._destroyed
            or self._webview is not None
            or self._create_pending
            or self._creation_error is not None
        ):
            return
        self._create_pending = True
        if delay_ms is None:
            self._track_after(self._frame.after_idle(self._run_try_create))
        else:
            self._track_after(self._frame.after(delay_ms, self._run_try_create))

    def _track_after(self, after_id: str | None) -> str | None:
        if after_id:
            self._deferred_after_ids.append(after_id)
        return after_id

    def _cancel_deferred_callbacks(self) -> None:
        self._cancel_initial_load_timer()
        self._create_pending = False
        self._flush_load_scheduled = False
        self._eval_js_scheduled = False
        self._bounds_sync_scheduled = False
        self._stacking_sync_scheduled = False
        self._pending_eval_js = None
        after_ids = self._deferred_after_ids
        self._deferred_after_ids = []
        for after_id in after_ids:
            if not after_id:
                continue
            try:
                self._frame.after_cancel(after_id)
            except (tk.TclError, RuntimeError, ValueError):
                pass

    def _run_try_create(self) -> None:
        self._create_pending = False
        self._try_create()

    def _require_tk_thread(self) -> None:
        # Compare a plain int only — never touch Tk/Tcl from a foreign thread.
        check_tk_thread_id(self._tk_thread_id)

    def _require_not_destroyed(self, method: str) -> None:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )

    def _require_ready(self, method: str) -> NativeWebView:
        self._require_tk_thread()
        if self._destroyed:
            raise WebViewDestroyedError(
                f"WebView.destroy() was called; cannot call {method}()"
            )
        if self._creation_error is not None:
            raise WebViewCreationError(
                f"WebView native creation failed; cannot call {method}()"
            ) from self._creation_error
        if not self.ready:
            raise WebViewNotReadyError(
                f"WebView is not ready; call wait_until_ready() or bind to "
                f"<<WebViewReady>> before calling {method}()"
            )
        assert self._webview is not None
        return self._webview

    def _bump_queue_drop(self, kind: int, count: int = 1) -> None:
        if count <= 0:
            return
        self._local_queue_drop_counts[kind] += count

    def _take_local_queue_drop_counts(self) -> tuple[int, int, int, int, int, int]:
        counts = self._local_queue_drop_counts
        self._local_queue_drop_counts = [0] * (_QUEUE_DROP_RPC + 1)
        return (
            counts[_QUEUE_DROP_IPC],
            counts[_QUEUE_DROP_PAGE_LOAD],
            counts[_QUEUE_DROP_TITLE],
            counts[_QUEUE_DROP_DRAG_DROP],
            counts[_QUEUE_DROP_EVAL],
            counts[_QUEUE_DROP_RPC],
        )

    def _pump_wait_until_ready(self, root: tk.Misc) -> None:
        """Advance this WebView; ``update_idletasks`` runs before ``update``."""
        root.update_idletasks()
        if (
            not self._destroyed
            and self._webview is None
            and self._creation_error is None
        ):
            if not self._create_pending and self._creation_size() is not None:
                self._try_create()
        if self._webview is not None and not self._destroyed:
            if self._bounds_sync_scheduled:
                self._deferred_sync_bounds()
            elif not self._layout_ready():
                self._sync_bounds()
            if self._should_keep_polling() or self._event_poll_active:
                self._poll_events()
        if sys.platform == "linux":
            from tkwry._linux import pump_gtk_unless_active

            pump_gtk_unless_active(self._frame)
        try:
            root.update()
        except tk.TclError:
            pass

    def _schedule_eval_js(self) -> None:
        if self._eval_js_scheduled:
            return
        self._eval_js_scheduled = True
        self._track_after(self._frame.after_idle(self._flush_eval_js))

    def _flush_eval_js(self) -> None:
        self._eval_js_scheduled = False
        pending = self._pending_eval_js
        self._pending_eval_js = None
        if pending is None or self._destroyed or self._webview is None:
            return
        script, on_error = pending
        self._run_eval_js(script, on_error)

    def _place_info_size(self) -> tuple[int | None, int | None]:
        """Explicit ``place(..., width=, height=)`` on the host, if any.

        Used only as a pre-layout fallback when ``winfo_*`` is still ``<= 1``.
        Mapped ``winfo_width`` / ``winfo_height`` remain authoritative.
        """
        try:
            if self._frame.winfo_manager() != "place":
                return None, None
            info = self._frame.place_info()
        except tk.TclError:
            return None, None

        def _axis(name: str) -> int | None:
            raw = info.get(name)
            if not isinstance(raw, str) or raw == "":
                return None
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                return None
            return value if value > 1 else None

        return _axis("width"), _axis("height")

    def _size_with_fallbacks(
        self, frame_w: int, frame_h: int
    ) -> tuple[int, int] | None:
        """Resolve host size for create/sync.

        Contract: when either axis reports ``winfo_* > 1``, that value wins.
        Before layout, fall back to constructor ``width``/``height``, then to
        explicit ``place`` width/height on the host.
        """
        place_w, place_h = self._place_info_size()
        width: int | None
        height: int | None
        if frame_w > 1:
            width = frame_w
        elif self._init_width is not None:
            width = self._init_width
        else:
            width = place_w
        if frame_h > 1:
            height = frame_h
        elif self._init_height is not None:
            height = self._init_height
        else:
            height = place_h
        if width is None or height is None or width <= 1 or height <= 1:
            return None
        return width, height

    def _creation_size(self) -> tuple[int, int] | None:
        self._frame.update_idletasks()
        frame_w = self._frame.winfo_width()
        frame_h = self._frame.winfo_height()
        if frame_w > 1 and frame_h > 1:
            return frame_w, frame_h
        return self._size_with_fallbacks(frame_w, frame_h)

    def _layout_ready(self) -> bool:
        """Whether the host frame has real geometry for callbacks and API use."""
        if self._webview is None or self._destroyed:
            return False
        return self._frame_is_laid_out()

    def _frame_is_laid_out(self) -> bool:
        """Whether the host is managed by pack/grid/place with usable size."""
        try:
            if not self._frame.winfo_exists():
                return False
            if not self._frame.winfo_manager():
                return False
        except tk.TclError:
            return False
        return self._bounds_size() is not None

    def _maybe_fire_ready(self) -> None:
        if self._destroyed or self._webview is None:
            return
        if not self._layout_ready():
            return
        if self._ready_delivered or self._ready_pending:
            return
        self._ready_pending = True
        self._fire_ready()

    def _fire_ready(self) -> None:
        def _deliver_ready() -> None:
            if self._destroyed:
                self._ready_pending = False
                return
            # Defer bind handlers until create/bounds/poll paths return. event_generate
            # from idle is synchronous for bindings but no longer re-enters _try_create.
            self._frame.event_generate("<<WebViewReady>>")
            self._ready_delivered = True
            self._ready_pending = False
            callbacks = self._ready_callbacks
            self._ready_callbacks = []
            for callback in callbacks:
                self._invoke_callback(callback, kind="when_ready")
            if self._focus_when_ready:
                self._focus_when_ready = False
                if self._webview is not None:
                    try:
                        self.focus()
                    except Exception:
                        traceback.print_exc()

        self._track_after(self._frame.after_idle(_deliver_ready))

    def _mark_creation_failed(self, exc: BaseException) -> None:
        if self._creation_error is None:
            self._creation_error = exc
        self._fire_create_failed()

    def _fire_create_failed(self) -> None:
        if self._failed_delivered or self._failed_pending:
            return
        if self._creation_error is None:
            return
        self._failed_pending = True

        def _deliver_failed() -> None:
            if self._destroyed:
                self._failed_pending = False
                return
            if self._failed_delivered:
                return
            self._frame.event_generate("<<WebViewCreateFailed>>")
            self._failed_delivered = True
            self._failed_pending = False
            err = self._creation_error
            callbacks = self._failed_callbacks
            self._failed_callbacks = []
            if err is None:
                return
            for callback in callbacks:
                self._invoke_callback(callback, err, kind="on_creation_failed")

        self._track_after(self._frame.after_idle(_deliver_failed))

    def _needs_event_poll(self) -> bool:
        return any(
            (
                self._ipc_listening_wanted(),
                self._on_navigation is not None,
                self._on_new_window is not None,
                self._navigation_policy_active(),
                self._new_window_policy_active(),
                self._on_page_load is not None,
                bool(self._inject_scripts),
                self._on_title_changed is not None,
                self._drag_drop_handler is not None,
                self._download_policy_active(),
                self._on_download_complete is not None,
            )
        )

    def _navigation_policy_active(self) -> bool:
        return (
            self._untrusted
            or self._lock_app_navigation
            or self._navigation_allow is not None
            or self._navigation_policy is not None
        )

    def _new_window_policy_active(self) -> bool:
        return (
            self._untrusted
            or self._lock_app_navigation
            or self._navigation_allow is not None
            or self._open_external
        )

    def _download_policy_active(self) -> bool:
        return (
            self._untrusted
            or self._download_allow is not None
            or self._on_download is not None
        )

    def _default_navigation_allowed(self, url: str) -> bool:
        if self._lock_app_navigation and app_navigation_allowed(url):
            return True
        if self._navigation_allow is not None:
            if origin_of(url) in INLINE_ORIGINS:
                return True
            return origin_allowed(url, self._navigation_allow)
        if self._lock_app_navigation:
            return False
        return True

    def _maybe_open_external(self, url: str) -> None:
        if not self._open_external or not is_external_http_url(url):
            return
        target = url
        try:
            self._track_after(self._frame.after(0, lambda: open_in_browser(target)))
        except tk.TclError:
            open_in_browser(target)

    def _bridge_origin_allowed(self, source_url: str | None) -> bool:
        if not origin_allowed(source_url, self._bridge_origins):
            return False
        predicate = self._bridge_allow
        if predicate is None:
            return True
        try:
            result = predicate(source_url or "")
        except Exception:
            traceback.print_exc()
            return False
        if type(result) is not bool:
            print(
                f"tkwry: bridge_allow must return bool, got {type(result).__name__}",
                file=sys.stderr,
            )
            return False
        return result

    def _native_drag_drop(
        self, event: DragDropEvent, paths: list[str], position: tuple[int, int]
    ) -> None:
        """Inject a drag-drop event into the same queue OS drops use (tests)."""
        native = self._webview
        if native is None or self._drag_drop_handler is None:
            return
        # Python handlers are authoritative for async queues.
        native.set_drag_drop_listening(True)
        native._enqueue_drag_drop_event(event, paths, position)
        self._ensure_event_poll()

    def _invoke_navigation_handler(self, url: str) -> bool:
        if self._untrusted and not untrusted_navigation_allowed(url):
            return False
        event = NavigationEvent(url=url)
        handler = self._on_navigation
        if handler is not None:
            try:
                result = call_navigation_handler(handler, event, url=url)
            except Exception:
                traceback.print_exc()
                return False
            if type(result) is not bool:
                print(
                    "tkwry: on_navigation must return bool, got "
                    f"{type(result).__name__}",
                    file=sys.stderr,
                )
                return False
            return result
        policy = self._navigation_policy
        if policy is not None:
            try:
                result = policy(event)
            except Exception:
                traceback.print_exc()
                return False
            if type(result) is not bool:
                print(
                    "tkwry: navigation_policy must return bool, got "
                    f"{type(result).__name__}",
                    file=sys.stderr,
                )
                return False
            return result
        allowed = self._default_navigation_allowed(url)
        if not allowed:
            self._maybe_open_external(url)
        return allowed

    def _native_navigation(self, url: str) -> bool:
        if self._on_navigation is None and not self._navigation_policy_active():
            return True
        return self._dispatch_sync_hook(
            lambda: self._invoke_navigation_handler(url),
            default=False,
            kind="on_navigation",
            detail=url,
        )

    def _native_title_changed(self, title: str) -> None:
        native = self._webview
        if native is None or self._on_title_changed is None:
            return
        native.set_title_listening(True)
        native._enqueue_title_event(title)
        self._ensure_event_poll()

    def _invoke_new_window_handler(self, url: str) -> NewWindowResponse:
        if self._untrusted:
            self._maybe_open_external(url)
            return NewWindowResponse.Deny
        handler = self._on_new_window
        if handler is None:
            if (
                self._lock_app_navigation
                or self._navigation_allow is not None
                or self._open_external
            ):
                self._maybe_open_external(url)
                return NewWindowResponse.Deny
            return NewWindowResponse.Allow
        try:
            result = handler(url)
        except Exception:
            traceback.print_exc()
            return NewWindowResponse.Deny
        if not isinstance(result, NewWindowResponse):
            print(
                "tkwry: on_new_window must return NewWindowResponse, "
                f"got {type(result).__name__}",
                file=sys.stderr,
            )
            return NewWindowResponse.Deny
        return result

    def _invoke_permission_handler(self, kind: PermissionKind) -> PermissionResponse:
        handler = self._permission_handler
        if handler is None:
            return PermissionResponse.Default
        try:
            result = handler(kind)
        except Exception:
            traceback.print_exc()
            return PermissionResponse.Deny
        if not isinstance(result, PermissionResponse):
            print(
                "tkwry: permission_handler must return PermissionResponse, "
                f"got {type(result).__name__}",
                file=sys.stderr,
            )
            return PermissionResponse.Deny
        return result

    def _native_permission(self, kind: PermissionKind) -> PermissionResponse:
        return self._dispatch_sync_hook(
            lambda: self._invoke_permission_handler(kind),
            default=PermissionResponse.Deny,
            kind="permission_handler",
            detail=repr(kind),
        )

    def _download_scheme(self, url: str) -> str:
        from urllib.parse import urlparse

        return (urlparse(url.strip()).scheme or "").lower()

    def _default_download_allowed(self, url: str) -> bool:
        if self._download_scheme(url) in _DANGEROUS_DOWNLOAD_SCHEMES:
            return False
        if self._download_allow is not None:
            return origin_allowed(url, self._download_allow)
        if self._untrusted:
            return False
        return True

    def _coerce_download_decision(
        self, result: object, _suggested: str
    ) -> tuple[bool, str | None]:
        if result is None or result is False:
            return False, None
        if result is True:
            return True, None
        if isinstance(result, (str, Path)):
            path = os.fspath(result)
            if not os.path.isabs(path):
                print(
                    "tkwry: on_download dest must be an absolute path",
                    file=sys.stderr,
                )
                return False, None
            return True, path
        print(
            "tkwry: on_download must return bool, None, str, or Path, "
            f"got {type(result).__name__}",
            file=sys.stderr,
        )
        return False, None

    def _invoke_download_handler(self, url: str, dest: str) -> tuple[bool, str | None]:
        if self._download_scheme(url) in _DANGEROUS_DOWNLOAD_SCHEMES:
            return False, None
        if self._download_allow is not None and not origin_allowed(
            url, self._download_allow
        ):
            return False, None
        handler = self._on_download
        if handler is None:
            return self._default_download_allowed(url), None
        download = Download(url=url, suggested_dest=dest)
        try:
            result = call_download_handler(
                handler, download, url=url, suggested_dest=dest
            )
        except Exception:
            traceback.print_exc()
            return False, None
        return self._coerce_download_decision(result, dest)

    def _deliver_download_started(self, download: Download) -> None:
        if self._destroyed:
            return
        self._last_started_download = download
        try:
            self._frame.event_generate("<<WebViewDownloadStarted>>")
        except (tk.TclError, RuntimeError):
            pass
        handler = self._on_download_started
        if handler is not None:
            self._invoke_callback(handler, download, kind="on_download_started")

    def _add_in_flight_download(
        self, url: str, suggested: str, dest_override: str | None
    ) -> None:
        if self._destroyed:
            return
        raw = dest_override if dest_override is not None else suggested
        resolved: str | None = raw or None
        self._in_flight_downloads.append(InFlightDownload(url=url, dest=resolved))
        self._deliver_download_started(
            Download(url=url, suggested_dest=suggested, dest=resolved)
        )

    def _remove_in_flight_download(self, url: str, dest: str | None) -> None:
        for index, item in enumerate(self._in_flight_downloads):
            if item.url != url:
                continue
            if dest is None or item.dest is None or item.dest == dest:
                del self._in_flight_downloads[index]
                return

    def _native_download_started(self, url: str, dest: str) -> tuple[bool, str | None]:
        if not self._download_policy_active():
            return True, None
        deny: tuple[bool, str | None] = (False, None)
        allowed, path = self._dispatch_sync_hook(
            lambda: self._invoke_download_handler(url, dest),
            default=deny,
            kind="on_download",
            detail=url,
        )
        if allowed:
            self._add_in_flight_download(url, dest, path)
        return allowed, path

    def _deliver_download_complete_events(self) -> None:
        native = self._webview
        if native is None:
            return
        handler = self._on_download_complete
        failed_handler = self._on_download_failed
        for url, dest, success in native.drain_download_complete_events():
            self._remove_in_flight_download(url, dest)
            self._last_download = (url, dest, success)
            sequence = (
                "<<WebViewDownloadComplete>>"
                if success
                else "<<WebViewDownloadFailed>>"
            )
            if not self._destroyed:
                try:
                    self._frame.event_generate(sequence)
                except (tk.TclError, RuntimeError):
                    pass
            if not success and failed_handler is not None:
                self._invoke_callback(
                    failed_handler, url, dest, kind="on_download_failed"
                )
            if handler is not None:
                self._invoke_callback(
                    handler, url, dest, success, kind="on_download_complete"
                )

    def _wake_async_events(self) -> None:
        """Drain wakeup-backed async queues without an idle native poll.

        ``download_complete_listening`` is always on so ``last_download`` and
        ``<<WebViewDownloadComplete>>`` / ``Failed`` work without a handler.
        Native complete pushes wake the Tk pipe; that path used to drain sync
        hooks only, so handler-less completes sat in the queue forever after
        ``76b50aa`` stopped polling on ``_webview is not None``. Deliver here
        (and keep a continuous poll only while ``on_download_complete`` is set).
        """
        if self._destroyed:
            return
        self._deliver_download_complete_events()
        if self._should_keep_polling():
            self._ensure_event_poll()

    def _native_download_complete(
        self, url: str, dest: str | None, success: bool
    ) -> None:
        """Inject a download-complete event (tests)."""
        native = self._webview
        if native is None:
            return
        native.set_download_complete_listening(True)
        native._enqueue_download_complete_event(url, dest, success)
        self._wake_async_events()

    def _native_new_window(self, url: str) -> NewWindowResponse:
        if self._on_new_window is None and not self._new_window_policy_active():
            return NewWindowResponse.Allow
        return self._dispatch_sync_hook(
            lambda: self._invoke_new_window_handler(url),
            default=NewWindowResponse.Deny,
            kind="on_new_window",
            detail=url,
        )

    def _enqueue_ipc(self, message: str, source_url: str | None = None) -> None:
        native = self._webview
        if native is None or not self._ipc_listening_wanted():
            return
        native.set_ipc_listening(True)
        native._enqueue_ipc_message(message, source_url)
        self._ensure_event_poll()

    def _sync_async_listening(self) -> None:
        native = self._webview
        if native is None:
            return
        native.set_ipc_listening(self._ipc_listening_wanted())
        native.set_page_load_listening(self._page_load_listening_wanted())
        native.set_title_listening(self._on_title_changed is not None)
        native.set_drag_drop_listening(self._drag_drop_handler is not None)
        native.set_download_complete_listening(True)

    def _invoke_callback(
        self,
        callback: Callable[..., object],
        *args: object,
        kind: str = "callback",
    ) -> None:
        try:
            callback(*args)
        except Exception as exc:
            error_handler = self._on_callback_error
            if error_handler is None or callback is error_handler:
                traceback.print_exception(type(exc), exc, exc.__traceback__)
                return
            try:
                error_handler(exc, kind)
            except Exception:
                traceback.print_exc()

    def _ensure_tk_wakeup_pipe(self) -> None:
        toplevel = self._frame.winfo_toplevel()
        write_fd = _toplevel_wakeup_write_fd(toplevel)
        if sys.platform != "darwin":
            if self._tk_wakeup_pipe_attached:
                if write_fd is not None:
                    self._tk_wakeup_write_fd = write_fd
            else:
                if write_fd is None:
                    read_fd, write_fd = _open_wakeup_pipe()
                    setattr(toplevel, "_tkwry_wake_read_fd", read_fd)
                    setattr(toplevel, "_tkwry_wake_write_fd", write_fd)
                    setattr(toplevel, "_tkwry_wake_pipe_users", 0)
                setattr(
                    toplevel,
                    "_tkwry_wake_pipe_users",
                    getattr(toplevel, "_tkwry_wake_pipe_users", 0) + 1,
                )
                _ensure_tk_wakeup_fileevent(toplevel)
                _register_sync_hook_webview(toplevel, self)
                self._tk_wakeup_pipe_attached = True
                self._tk_wakeup_write_fd = write_fd
        else:
            self._tk_wakeup_write_fd = write_fd
        native = self._webview
        if native is not None and self._tk_wakeup_write_fd is not None:
            native.set_mac_wakeup_write_fd(self._tk_wakeup_write_fd)

    def _release_tk_wakeup_pipe_registration(self) -> None:
        """Drop this WebView's share of the Win/Linux shared wakeup pipe (D24)."""
        if sys.platform == "darwin" or not self._tk_wakeup_pipe_attached:
            return
        self._tk_wakeup_pipe_attached = False
        self._tk_wakeup_write_fd = None
        try:
            _release_tk_wakeup_pipe(self._frame.winfo_toplevel())
        except tk.TclError:
            pass

    def _wake_tk_for_sync_hook(self) -> None:
        write_fd = self._tk_wakeup_write_fd
        if write_fd is None:
            return
        try:
            if os.write(write_fd, b"\x01") != 1:
                return
        except OSError:
            pass

    def _borrow_sync_hook_event(self) -> threading.Event:
        """Reuse preallocated events — avoid ``threading.Event()`` during worker GC."""
        with self._sync_hook_event_pool_lock:
            event = (
                self._sync_hook_event_pool.popleft()
                if self._sync_hook_event_pool
                else threading.Event()
            )
        event.clear()
        return event

    def _return_sync_hook_event(self, event: threading.Event) -> None:
        with self._sync_hook_event_pool_lock:
            if len(self._sync_hook_event_pool) < _SYNC_HOOK_EVENT_POOL_SIZE * 2:
                self._sync_hook_event_pool.append(event)

    def _schedule_sync_hook_drain(self) -> None:
        """Ask the Tk thread to drain Python-side sync hooks."""
        self._wake_tk_for_sync_hook()
        if threading.get_ident() != self._tk_thread_id:
            return
        try:
            self._frame.after(0, self._drain_sync_hooks)
        except (tk.TclError, RuntimeError):
            pass

    def _dispatch_sync_hook(
        self,
        invoke: Callable[[], _T],
        default: _T,
        *,
        kind: str = "sync hook",
        detail: str | None = None,
    ) -> _T:
        if threading.get_ident() == self._tk_thread_id:
            return self._run_sync_hook_invoke(invoke, default)

        done = self._borrow_sync_hook_event()
        try:
            result: list[object] = [default]
            cancelled = [False]
            started = [False]
            handler_started_at = [0.0]
            self._sync_hook_queue.put(
                (
                    invoke,
                    result,
                    default,
                    done,
                    cancelled,
                    started,
                    handler_started_at,
                )
            )
            self._ensure_event_poll()
            self._schedule_sync_hook_drain()
            enqueued_at = time.monotonic()
            deadline = enqueued_at + _SYNC_HOOK_TIMEOUT_S
            absolute_deadline = enqueued_at + _SYNC_HOOK_MAX_WAIT_S
            suffix = f" ({detail})" if detail else ""

            def _timeout_msg(prefix: str) -> None:
                print(f"tkwry: {prefix}{suffix}", file=sys.stderr)

            def _on_timeout(prefix: str) -> None:
                _timeout_msg(prefix)
                if kind in ("on_navigation", "on_new_window"):
                    self._queue_navigation_error(
                        WebViewNavigationError(f"{prefix}{suffix}")
                    )

            while not done.is_set():
                if not started[0]:
                    remaining = min(deadline, absolute_deadline) - time.monotonic()
                    if remaining <= 0:
                        cancelled[0] = True
                        _on_timeout(f"{kind} timed out after {_SYNC_HOOK_TIMEOUT_S:g}s")
                        return default
                    done.wait(timeout=min(0.05, remaining))
                else:
                    handler_deadline = (
                        handler_started_at[0] + _SYNC_HOOK_HANDLER_TIMEOUT_S
                    )
                    remaining = (
                        min(handler_deadline, absolute_deadline) - time.monotonic()
                    )
                    if remaining <= 0:
                        cancelled[0] = True
                        if time.monotonic() >= absolute_deadline:
                            _on_timeout(
                                f"{kind} timed out after "
                                f"{_SYNC_HOOK_MAX_WAIT_S:g}s total"
                            )
                        else:
                            _on_timeout(
                                f"{kind} handler timed out after "
                                f"{_SYNC_HOOK_HANDLER_TIMEOUT_S:g}s"
                            )
                        return default
                    done.wait(timeout=min(0.05, remaining))
                # Wake the Tk thread; native/Python drains run on the owner thread only.
                self._schedule_sync_hook_drain()
            return cast(_T, result[0])
        finally:
            self._return_sync_hook_event(done)

    def _discard_navigation_error_queue(self) -> None:
        q = self._navigation_error_queue
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _queue_navigation_error(self, exc: WebViewNavigationError) -> None:
        try:
            self._navigation_error_queue.put_nowait(exc)
        except Exception:
            return
        self._wake_tk_for_sync_hook()

    def _deliver_navigation_errors(self) -> None:
        """Deliver queued nav/new-window timeouts on the Tk thread."""
        if self._destroyed:
            self._discard_navigation_error_queue()
            return
        while True:
            try:
                exc = self._navigation_error_queue.get_nowait()
            except queue.Empty:
                return
            self._last_navigation_error = exc
            try:
                self._frame.event_generate("<<WebViewNavigationFailed>>")
            except (tk.TclError, RuntimeError):
                pass

    def _signal_eval_error(
        self,
        exc: BaseException,
        *,
        on_error: EvalErrorHandler | None,
    ) -> None:
        self._last_eval_error = exc
        if not self._destroyed:
            try:
                self._frame.event_generate("<<WebViewEvalFailed>>")
            except (tk.TclError, RuntimeError):
                pass
        if on_error is not None:
            self._invoke_callback(on_error, exc, kind="eval_on_error")
            return
        if isinstance(exc, WebViewTimeoutError) or exc.__traceback__ is None:
            print(f"tkwry: {exc}", file=sys.stderr)
        else:
            traceback.print_exception(type(exc), exc, exc.__traceback__)

    def _run_sync_hook_invoke(self, invoke: Callable[[], _T], default: _T) -> _T:
        self._sync_hook_depth += 1
        try:
            return invoke()
        except Exception:
            traceback.print_exc()
            return default
        finally:
            self._sync_hook_depth -= 1

    def _drain_sync_hooks(self) -> None:
        while True:
            try:
                (
                    invoke,
                    result,
                    default,
                    done,
                    cancelled,
                    started,
                    handler_started_at,
                ) = self._sync_hook_queue.get_nowait()
            except queue.Empty:
                break
            if cancelled[0] or self._destroyed:
                result[0] = default
            else:
                started[0] = True
                handler_started_at[0] = time.monotonic()
                result[0] = self._run_sync_hook_invoke(invoke, default)
            done.set()

    def _abort_sync_hooks(self) -> None:
        while True:
            try:
                (
                    _invoke,
                    result,
                    default,
                    done,
                    cancelled,
                    _started,
                    _handler_started_at,
                ) = self._sync_hook_queue.get_nowait()
            except queue.Empty:
                break
            cancelled[0] = True
            result[0] = default
            done.set()

    def _deliver_title_events(self) -> None:
        handler = self._on_title_changed
        native = self._webview
        if handler is None or native is None:
            return
        for title in native.drain_title_events():
            self._invoke_callback(handler, title, kind="on_title_changed")

    def _deliver_drag_drop_events(self) -> None:
        handler = self._drag_drop_handler
        native = self._webview
        if handler is None or native is None:
            return
        for event, paths, position in native.drain_drag_drop_events():
            self._invoke_callback(
                handler, event, paths, position, kind="drag_drop_handler"
            )

    def _deliver_page_load_events(self) -> None:
        page_load = self._on_page_load
        native = self._webview
        if native is None:
            return
        if page_load is None and not self._inject_scripts:
            return
        pending = native.drain_page_load_events()
        for event, page_url in pending:
            if event == PageLoadEvent.Started and self._inject_scripts:
                for script in list(self._inject_scripts):
                    try:
                        self.eval_js(script)
                    except Exception:
                        traceback.print_exc()
            if page_load is not None:
                self._invoke_callback(page_load, event, page_url, kind="on_page_load")

    def _deliver_async_event_queues(self) -> None:
        """Drain native async queues into Python callbacks."""
        native = self._webview
        if native is not None:
            native.drain_sync_hooks()
        self._drain_sync_hooks()
        self._deliver_navigation_errors()
        if self._ipc_listening_wanted():
            self._deliver_ipc_messages()
        self._drain_rpc_futures()
        self._deliver_page_load_events()
        if self._on_title_changed is not None:
            self._deliver_title_events()
        if self._drag_drop_handler is not None:
            self._deliver_drag_drop_events()
        self._deliver_download_complete_events()

    def _service_linux_events(self, *, gtk_rounds: int = 1, passes: int = 1) -> None:
        """Deliver async queues; pump GTK only when GtkPump is not already draining.

        When ``GtkPump`` is active for this frame's toplevel, run a **single**
        queue flush and return (no nested ``pump_events``). When inactive, pump
        up to *gtk_rounds* bursts per pass for *passes* rounds, then deliver.
        """
        if sys.platform != "linux" or self._destroyed:
            return
        from tkwry._linux import GtkPump, pump_gtk_unless_active

        if GtkPump.is_active_for(self._frame):
            self._deliver_async_event_queues()
            return

        for _ in range(max(1, passes)):
            pump_gtk_unless_active(self._frame, bursts=gtk_rounds)
            self._deliver_async_event_queues()

    def _schedule_post_navigation_drain(self) -> None:
        """Queue GTK/WebKit drain on Tk idle (avoids nested pump deadlocks)."""
        if self._destroyed or sys.platform != "linux":
            return
        if self._post_nav_drain_scheduled:
            return
        self._post_nav_drain_scheduled = True

        def _drain() -> None:
            self._post_nav_drain_scheduled = False
            if self._destroyed:
                return
            self._drain_after_navigation()

        self._track_after(self._frame.after_idle(_drain))

    def _finish_navigation(self) -> None:
        """Pump GTK and deliver async queues after a navigation."""
        if self._destroyed:
            return
        if sys.platform == "linux":
            if self._in_poll_events:
                self._schedule_post_navigation_drain()
            else:
                self._drain_after_navigation()
        else:
            self._service_linux_events()

    def _drain_after_navigation(self) -> None:
        """Post-nav flush via :meth:`_service_linux_events` (queue-only if active)."""
        # Inactive pump: a few light passes; active GtkPump: one queue deliver.
        self._service_linux_events(gtk_rounds=1, passes=4)

    def _register_pending_eval(
        self,
        callback: EvalCallback,
        on_error: EvalErrorHandler | None,
    ) -> int:
        token = self._eval_token_seq
        self._eval_token_seq += 1
        self._pending_eval_tokens[token] = (
            time.monotonic() + _EVAL_CALLBACK_TIMEOUT_S,
            callback,
            on_error,
        )
        self._pending_eval_callbacks += 1
        return token

    def _release_pending_eval(self, token: int) -> None:
        if token not in self._pending_eval_tokens:
            return
        del self._pending_eval_tokens[token]
        self._pending_eval_callbacks = max(0, self._pending_eval_callbacks - 1)

    def _drop_native_eval_wait_for_py_token(self, py_token: int) -> None:
        for native_token, wait in list(self._native_eval_wait.items()):
            if wait[1] == py_token:
                del self._native_eval_wait[native_token]

    def _expire_pending_evals(self) -> None:
        if not self._pending_eval_tokens:
            return
        now = time.monotonic()
        for token, (deadline, callback, on_error) in list(
            self._pending_eval_tokens.items()
        ):
            if now >= deadline:
                self._release_pending_eval(token)
                self._drop_native_eval_wait_for_py_token(token)
                self._bump_queue_drop(_QUEUE_DROP_EVAL)
                self._signal_eval_error(
                    WebViewTimeoutError(
                        f"eval_js_with_callback timed out after "
                        f"{_EVAL_CALLBACK_TIMEOUT_S:g}s"
                    ),
                    on_error=on_error,
                )

    def _disarm_event_poll(self) -> None:
        """Unconditionally clear the poll latch (ignores remaining work)."""
        self._event_poll_active = False

    def _ensure_event_poll(self) -> None:
        """Arm the Tk poll when async work remains (including native teardown).

        Destroyed instances still poll while ``_native_teardown_pending`` is set
        so deferred ``is_alive`` cleanup cannot stall forever.
        """
        if self._event_poll_active:
            return
        if self._destroyed and self._native_teardown_pending is None:
            return
        try:
            if not self._frame.winfo_exists():
                return
        except (tk.TclError, RuntimeError):
            return
        self._event_poll_active = True
        try:
            self._track_after(self._frame.after(1, self._poll_events))
        except (tk.TclError, RuntimeError):
            self._disarm_event_poll()

    def _stop_event_poll_if_idle(self) -> None:
        """Clear ``_event_poll_active`` when no poll work remains."""
        if self._should_keep_polling():
            return
        self._disarm_event_poll()

    def _drain_native_eval_callbacks(self) -> None:
        native = self._webview
        if native is None:
            return
        for native_token, _callback, result in native.drain_eval_callbacks():
            wait = self._native_eval_wait.pop(native_token, None)
            if wait is None:
                continue
            wait_epoch, py_token, expected_cb, on_error = wait
            if py_token not in self._pending_eval_tokens:
                continue
            self._release_pending_eval(py_token)
            if wait_epoch != self._eval_epoch:
                continue
            if result is None:
                self._bump_queue_drop(_QUEUE_DROP_EVAL)
                self._signal_eval_error(
                    RuntimeError("eval result dropped (pending queue full)"),
                    on_error=on_error,
                )
                continue
            self._invoke_callback(expected_cb, result, kind="eval_js_with_callback")

    def _poll_events(self) -> None:
        self._in_poll_events = True
        try:
            self._poll_events_impl()
        finally:
            self._in_poll_events = False

    def _poll_events_impl(self) -> None:
        try:
            _drain_pending_destroy_webviews(self._toplevel)
        except tk.TclError:
            return
        self._finish_native_teardown()
        if self._destroyed:
            self._discard_navigation_error_queue()
            if self._native_teardown_pending is not None:
                try:
                    if self._frame.winfo_exists():
                        self._track_after(self._frame.after(1, self._poll_events))
                    else:
                        self._disarm_event_poll()
                except (tk.TclError, RuntimeError):
                    self._disarm_event_poll()
            else:
                self._stop_event_poll_if_idle()
            return
        if sys.platform == "linux":
            _pump_toplevel_wakeup_pipe(self._toplevel)
            # GtkPump already drains the shared GTK context for this toplevel.
            # Nested full bursts from every 1ms WebView poll starve Tk when
            # multiple views keep events_pending under Xvfb.
            from tkwry._linux import pump_gtk_unless_active

            pump_gtk_unless_active(self._frame)
        elif sys.platform == "darwin":
            _mac_service_wakeup(self._toplevel)
        else:
            _pump_toplevel_wakeup_pipe(self._toplevel)

        native = self._webview
        if native is not None:
            native.drain_sync_hooks()
        self._drain_sync_hooks()
        self._deliver_navigation_errors()

        if self._ipc_listening_wanted():
            self._deliver_ipc_messages()
        self._drain_rpc_futures()

        self._deliver_page_load_events()

        if self._on_title_changed is not None:
            self._deliver_title_events()

        if self._drag_drop_handler is not None:
            self._deliver_drag_drop_events()

        self._deliver_download_complete_events()

        self._expire_pending_evals()
        self._drain_native_eval_callbacks()

        if self._should_keep_polling():
            delay = 1 if sys.platform == "linux" else 10
            try:
                self._track_after(self._frame.after(delay, self._poll_events))
            except tk.TclError:
                self._disarm_event_poll()
        else:
            # Clear before re-check so a concurrent ensure_event_poll can re-arm.
            self._disarm_event_poll()
            if self._should_keep_polling():
                self._ensure_event_poll()

    def _should_keep_polling(self) -> bool:
        if self._native_teardown_pending is not None:
            return True
        if self._needs_event_poll():
            return True
        if self._rpc_inflight:
            return True
        try:
            if not self._navigation_error_queue.empty():
                return True
        except Exception:
            pass
        return self._pending_eval_callbacks > 0 or bool(self._native_eval_wait)

    def _try_create(self) -> None:
        if (
            self._destroyed
            or self._webview is not None
            or self._creation_error is not None
        ):
            return

        size = self._creation_size()
        # ``update_idletasks`` inside ``_creation_size`` can re-enter create.
        if (
            size is None
            or self._destroyed
            or self._webview is not None
            or self._creation_error is not None
        ):
            return
        width, height = size

        url = self._pending_url
        html = self._pending_html
        initial_load: _PendingLoad | None = None
        if html is not None:
            initial_load = ("html", html)
        elif url is not None:
            initial_load = ("url", url, self._pending_url_headers)

        kwargs: dict = {
            "width": width,
            "height": height,
            # Map axis only (Notebook / unmapped): not the layout ``ready`` contract.
            "visible": self._frame_should_show(),
            "devtools": self._devtools,
            "clipboard": self._clipboard,
            "javascript_enabled": self._javascript_enabled,
            "autoplay": self._autoplay,
            "hotkeys_zoom": self._hotkeys_zoom,
            "back_forward_gestures": self._back_forward_gestures,
            "default_context_menus": self._default_context_menus,
            "https_scheme": self._https_scheme,
            "focused": self._focused,
        }
        if self._proxy_native is not None:
            kwargs["proxy"] = self._proxy_native
        if self._background_color is not None:
            kwargs["background_color"] = self._background_color
        if self._user_agent is not None:
            kwargs["user_agent"] = self._user_agent
        init_script = self._effective_initialization_script()
        if init_script is not None:
            kwargs["initialization_script"] = init_script
            if self._rpc_methods or self._rpc_bridge_wanted:
                self._rpc_bootstrap_injected = True
        if self._app_root is not None:
            kwargs["app_root"] = self._app_root
            kwargs["spa_fallback"] = self._spa_fallback
            if self._app_dev:
                kwargs["app_cache_control"] = "no-store"
            if self._csp is not None:
                kwargs["app_csp"] = self._csp
            if self._coop:
                kwargs["app_coop"] = True
            if self._corp:
                kwargs["app_corp"] = True
        if self._session is not None:
            kwargs["session"] = self._session.native
        if self._on_navigation is not None or self._navigation_policy_active():
            kwargs["on_navigation"] = self._native_navigation
        if self._on_new_window is not None or self._new_window_policy_active():
            kwargs["on_new_window"] = self._native_new_window
        if self._permission_handler is not None:
            kwargs["on_permission"] = self._native_permission
        kwargs["page_load_listening"] = self._page_load_listening_wanted()
        kwargs["ipc_listening"] = self._ipc_listening_wanted()
        kwargs["title_listening"] = self._on_title_changed is not None
        kwargs["drag_drop_listening"] = self._drag_drop_handler is not None
        if self._download_policy_active():
            kwargs["on_download_started"] = self._native_download_started
        kwargs["download_complete_listening"] = True
        if self._untrusted:
            kwargs["with_ipc"] = False

        if sys.platform == "linux":
            from tkwry._linux import pump_gtk_unless_active

            # Bootstrap before attach: first root pumps; a live sibling GtkPump
            # is not doubled (unless_active no-ops).
            pump_gtk_unless_active(self._frame, bursts=20)
            self._attach_gtk_pump_for_native()

        if sys.platform == "win32":
            from tkwry._win32 import (
                WEBVIEW2_MISSING_MESSAGE,
                is_webview2_runtime_available,
                looks_like_webview2_missing,
                webview2_missing_error,
            )

            if not is_webview2_runtime_available():
                self._mark_creation_failed(webview2_missing_error())
                print(f"tkwry: {WEBVIEW2_MISSING_MESSAGE}", file=sys.stderr)
                return

        try:
            self._webview = NativeWebView(
                self._embed.handle,
                owner_thread=self._tk_thread_id,
                **kwargs,
            )
        except Exception as exc:
            traceback.print_exc()
            if sys.platform == "win32":
                from tkwry._win32 import (
                    WEBVIEW2_MISSING_MESSAGE,
                    is_webview2_runtime_available,
                    looks_like_webview2_missing,
                    webview2_missing_error,
                )

                missing = looks_like_webview2_missing(exc) or (
                    not is_webview2_runtime_available()
                )
                if missing:
                    self._mark_creation_failed(webview2_missing_error(exc))
                    print(f"tkwry: {WEBVIEW2_MISSING_MESSAGE}", file=sys.stderr)
                    return
            self._create_attempt += 1
            if self._create_attempt >= _CREATE_MAX_ATTEMPTS:
                self._mark_creation_failed(exc)
                print(
                    f"tkwry: failed to create native WebView after "
                    f"{_CREATE_MAX_ATTEMPTS} attempts; giving up",
                    file=sys.stderr,
                )
                return
            delay = min(5000, 50 * (2 ** min(self._create_attempt - 1, 6)))
            self._schedule_try_create(delay_ms=delay)
            return
        self._create_attempt = 0
        if sys.platform != "darwin":
            self._ensure_tk_wakeup_pipe()
        self._sync_async_listening()
        self._ensure_gtk_pump_attached()
        self._clear_precreate_pending()
        self._sync_bounds()
        self._schedule_bounds_sync()
        if initial_load is not None:
            self._arm_initial_load(initial_load)
            # Defer load until after create returns: sync load_html during
            # _try_create/pack races WebKitGTK (constructor html never finishes).
            self._schedule_initial_load()
        if sys.platform == "darwin" and self._webview is not None:
            toplevel = self._frame.winfo_toplevel()
            _ensure_mac_wakeup_pipe(toplevel, self._webview)
            _ensure_mac_pump(toplevel)
        if self._needs_event_poll():
            self._ensure_event_poll()
            if sys.platform == "linux":
                # GtkPump usually active after attach → queue-only; else one kick.
                self._service_linux_events(gtk_rounds=32, passes=1)
        self._maybe_fire_ready()

    def _run_eval_js(
        self, script: str, on_error: EvalErrorHandler | None = None
    ) -> None:
        if self._destroyed or self._webview is None:
            return
        try:
            self._webview.eval_js(script)
        except Exception as exc:
            self._signal_eval_error(exc, on_error=on_error)

    def _frame_ready_for_initial_load(self) -> bool:
        """Whether the host frame is laid out enough to load content.

        Default: real mapped size **and** viewable (Notebook tabs stay unloaded
        until shown). Constructor ``width``/``height`` also unlock Navigate while
        the host is still hidden or 1×1 — used for off-screen warmup (e.g. a
        paned pane with ``hide=True``).
        """
        try:
            if not self._frame.winfo_exists() or self._webview is None:
                return False
            fw = int(self._frame.winfo_width())
            fh = int(self._frame.winfo_height())
            has_init_size = (
                self._init_width is not None
                and self._init_height is not None
                and self._init_width > 1
                and self._init_height > 1
            )
            if fw > 1 and fh > 1:
                if self._host_is_viewable_for_map():
                    return True
                # Sized but not viewable: only with explicit early-create size.
                return has_init_size
            # Unmapped / 1×1: constructor size enables hidden warmup Navigate.
            return has_init_size
        except (tk.TclError, TypeError, ValueError):
            return False

    def _bump_initial_load_attempt(self) -> None:
        self._initial_load_attempt += 1
        max_attempts = self._initial_load_attempts()
        if self._initial_load_attempt >= max_attempts:
            print(
                "tkwry: initial load failed after "
                f"{max_attempts} attempt(s); will retry",
                file=sys.stderr,
            )
            self._initial_load_attempt = 0

    def _schedule_flush_load(self, *, delay_ms: int | None = None) -> None:
        if self._flush_load_scheduled:
            return
        self._flush_load_scheduled = True
        if delay_ms is None:
            self._track_after(self._frame.after_idle(self._flush_load))
        else:
            self._track_after(self._frame.after(delay_ms, self._flush_load))

    def _initial_load_attempts(self) -> int:
        """Headless Linux and macOS may need a second navigation after compositing."""
        if sys.platform in ("darwin", "linux"):
            return 2
        return 1

    def _cancel_initial_load_timer(self) -> None:
        after_id = self._initial_load_after_id
        if after_id is None:
            return
        self._initial_load_after_id = None
        try:
            self._frame.winfo_toplevel().after_cancel(after_id)
        except tk.TclError:
            pass

    def _schedule_initial_load(self) -> None:
        if self._initial_load is None:
            return
        self._cancel_initial_load_timer()
        try:
            toplevel = self._frame.winfo_toplevel()
            if sys.platform == "darwin":
                delay = 200
            else:
                delay = 150 if sys.platform == "linux" else 100
            self._initial_load_after_id = toplevel.after(delay, self._run_initial_load)
        except tk.TclError:
            self._initial_load_after_id = None

    def _maybe_reschedule_initial_load(self) -> None:
        if self._initial_load is not None and not self._destroyed:
            self._schedule_initial_load()

    def _run_initial_load(self) -> None:
        self._initial_load_after_id = None
        load = self._initial_load
        if load is None or self._destroyed or self._webview is None:
            return
        if self._pending_load is not None:
            # A later load_url/load_html already won; drop constructor content.
            self._clear_initial_load()
            return
        if not self._frame_ready_for_initial_load():
            if (
                os.environ.get("TKWRY_LOAD_PROFILE")
                or os.environ.get("TKLAB_STARTUP_PROFILE")
                or os.environ.get("TKIPW_STARTUP_PROFILE")
            ):
                if not getattr(self, "_initial_load_defer_logged", False):
                    self._initial_load_defer_logged = True
                    try:
                        print(
                            "tkwry: initial load deferred "
                            f"viewable={self._frame.winfo_viewable()} "
                            f"size={self._frame.winfo_width()}x"
                            f"{self._frame.winfo_height()} "
                            f"init={self._init_width}x{self._init_height}",
                            file=sys.stderr,
                            flush=True,
                        )
                    except tk.TclError:
                        pass
            self._maybe_reschedule_initial_load()
            return
        if (
            os.environ.get("TKWRY_LOAD_PROFILE")
            or os.environ.get("TKLAB_STARTUP_PROFILE")
            or os.environ.get("TKIPW_STARTUP_PROFILE")
        ):
            try:
                print(
                    "tkwry: initial load firing "
                    f"viewable={self._frame.winfo_viewable()} "
                    f"size={self._frame.winfo_width()}x"
                    f"{self._frame.winfo_height()} "
                    f"init={self._init_width}x{self._init_height}",
                    file=sys.stderr,
                    flush=True,
                )
            except tk.TclError:
                pass
        self._sync_bounds()
        # Re-check after sync: load_* may have cleared or replaced this.
        if self._initial_load is not load or self._pending_load is not None:
            self._clear_initial_load()
            return
        if sys.platform == "linux":
            if load[0] == "url":
                _, payload, headers = load
                self._set_pending_load("url", payload, headers=headers)
            else:
                _, payload = load
                self._set_pending_load("html", payload)
            self._dispatch_pending_load()
            if self._pending_load is None:
                self._clear_initial_load()
            return
        try:
            self._apply_load_to_native(load)
        except Exception:
            traceback.print_exc()
            self._bump_initial_load_attempt()
            self._maybe_reschedule_initial_load()
            return
        self._sync_bounds()
        self._finish_navigation()
        if self._page_load_listening_wanted():
            self._ensure_event_poll()
        self._clear_initial_load()

    def _flush_load(self) -> None:
        self._flush_load_scheduled = False
        if self._destroyed or self._webview is None or self._pending_load is None:
            return
        load = self._pending_load
        if sys.platform == "linux":
            self._sync_bounds()
        try:
            self._apply_load_to_native(load)
        except Exception:
            traceback.print_exc()
            self._flush_load_attempt += 1
            if self._destroyed or self._pending_load is None:
                return
            delay_ms = min(
                _FLUSH_LOAD_RETRY_MAX_MS,
                _FLUSH_LOAD_RETRY_BASE_MS * (2 ** min(self._flush_load_attempt - 1, 4)),
            )
            if self._flush_load_attempt >= _FLUSH_LOAD_MAX_ATTEMPTS:
                print(
                    "tkwry: load still failing after "
                    f"{self._flush_load_attempt} attempt(s); continuing to retry "
                    f"in {delay_ms}ms",
                    file=sys.stderr,
                )
                self._flush_load_attempt = 0
            self._schedule_flush_load(delay_ms=delay_ms)
            return
        self._clear_pending_load()
        self._flush_load_attempt = 0
        self._clear_initial_load()
        self._sync_bounds()
        self._finish_navigation()
        if self._page_load_listening_wanted():
            self._ensure_event_poll()

    def _bounds_size(self) -> tuple[int, int] | None:
        """Return the width/height to push, or None when geometry is not meaningful.

        Mapped ``winfo_*`` is authoritative; see ``_size_with_fallbacks``.
        """
        try:
            if not self._frame.winfo_exists():
                return None
            return self._size_with_fallbacks(
                self._frame.winfo_width(),
                self._frame.winfo_height(),
            )
        except tk.TclError:
            return None

    def _host_is_viewable_for_map(self) -> bool:
        """Map/visibility axis only (HIDDEN vs READY) — never used by ``ready``.

        Unmapped hosts (e.g. inactive Notebook tabs) must hide via
        ``set_visible(False)``. On Linux/Xvfb a *mapped* host can still report
        ``winfo_viewable()==0`` while geometry is valid, so mapped Linux hosts
        count as showable; ``winfo_ismapped()`` still detects real unmaps.
        """
        try:
            if not self._frame.winfo_ismapped():
                return False
        except tk.TclError:
            return False
        if sys.platform == "linux":
            return True
        return bool(self._frame.winfo_viewable())

    def _frame_should_show(self) -> bool:
        try:
            if not self._frame.winfo_exists():
                return False
            if self._bounds_size() is None:
                return False
            return self._host_is_viewable_for_map()
        except tk.TclError:
            return False

    def _schedule_bounds_sync(self) -> None:
        if self._destroyed or self._bounds_sync_scheduled:
            return
        self._bounds_sync_scheduled = True
        try:
            self._frame.update_idletasks()
            self._track_after(self._frame.after_idle(self._deferred_sync_bounds))
        except tk.TclError:
            self._bounds_sync_scheduled = False

    def _deferred_sync_bounds(self) -> None:
        self._bounds_sync_scheduled = False
        self._sync_bounds_and_stacking()

    def _sync_bounds_and_stacking(self) -> bool:
        """Push bounds/visibility, then try ready (layout axis; ignore map hide)."""
        synced = self._sync_bounds()
        if synced:
            self._schedule_stacking_sync()
        # Funnel: even when HIDDEN (sync False), layout-ready still fires once.
        self._maybe_fire_ready()
        return synced

    def _schedule_stacking_sync(self) -> None:
        if sys.platform != "win32" or self._webview is None or self._destroyed:
            return
        if self._stacking_sync_scheduled:
            return
        self._stacking_sync_scheduled = True
        try:
            self._track_after(self._frame.after_idle(self._deferred_sync_stacking))
        except tk.TclError:
            self._stacking_sync_scheduled = False

    def _deferred_sync_stacking(self) -> None:
        self._stacking_sync_scheduled = False
        self._sync_tk_stacking_order()

    def _sync_tk_stacking_order(self) -> None:
        if sys.platform != "win32" or self._webview is None or self._destroyed:
            return
        try:
            from tkwry._win32 import raise_frame_webview

            parent = self._frame.master
            for child in parent.winfo_children():
                key = id(child)
                ref = _frame_webview_refs.get(key)
                if ref is None:
                    continue
                web = ref()
                if web is None or web._destroyed or web._webview is None:
                    continue
                raise_frame_webview(child.winfo_id())
        except tk.TclError:
            pass
        except Exception:
            traceback.print_exc()

    def _sync_bounds(self) -> bool:
        if self._webview is None:
            return False
        if not self._frame_should_show():
            self._hide_native_view(self._webview)
            return False
        size = self._bounds_size()
        if size is None:
            self._hide_native_view(self._webview)
            return False
        width, height = size
        try:
            self._frame.update_idletasks()
            x, y = tk_embed_origin(self._frame, root_relative=self._embed.root_relative)
        except tk.TclError:
            return False
        try:
            self._webview.set_bounds(x, y, width, height)
        except Exception:
            return False
        return self._show_native_view(self._webview)

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        if self._webview is None:
            self._schedule_try_create()
        elif self._bounds_size() is not None:
            self._sync_bounds_and_stacking()
        else:
            self._schedule_bounds_sync()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._ensure_gtk_pump_attached()
        self._schedule_bounds_sync()
        self._track_after(self._frame.after_idle(self._run_initial_load))

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self._frame or self._destroyed:
            return
        self._schedule_bounds_sync()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self._frame:
            return
        self.destroy()
