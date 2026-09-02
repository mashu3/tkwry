"""Navigation policy helpers and event objects."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

NavigationPolicyHandler: TypeAlias = Callable[["NavigationEvent"], bool]
NavigationHandler: TypeAlias = (
    Callable[["NavigationEvent"], bool] | Callable[[str], bool]
)


class NavigationType(str, Enum):
    """How a navigation was initiated.

    Engines currently expose only the target URL to tkwry, so most events
    report :attr:`Unknown` until richer metadata is available from wry.
    """

    Unknown = "unknown"
    Link = "link"
    Form = "form"
    Reload = "reload"
    BackForward = "back_forward"
    Other = "other"


@dataclass(frozen=True, slots=True)
class NavigationEvent:
    """A main-frame navigation request from the engine.

    Parameters
    ----------
    url:
        Target URL.
    navigation_type:
        Initiator hint when the engine provides one; otherwise
        :attr:`NavigationType.Unknown`.
    is_redirect:
        ``True`` when the engine reports a redirect (default ``False``).
    is_main_frame:
        ``True`` for top-level navigations (default ``True``).
    """

    url: str
    navigation_type: NavigationType = NavigationType.Unknown
    is_redirect: bool = False
    is_main_frame: bool = True


def call_navigation_handler(
    handler: NavigationHandler,
    event: NavigationEvent,
    *,
    url: str,
) -> bool:
    """Invoke *handler* using ``NavigationEvent`` or legacy ``url`` form."""
    if _navigation_handler_uses_url(handler):
        return handler(url)
    return handler(event)


def coerce_navigation_result(result: object) -> bool:
    """Return *result* when it is a ``bool``; otherwise ``False``."""
    if type(result) is bool:
        return result
    return False


def _navigation_handler_uses_url(handler: NavigationHandler) -> bool:
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return True
    params = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(params) != 1:
        return True
    param = params[0]
    if param.name == "event":
        return False
    annotation = param.annotation
    if annotation is inspect.Parameter.empty:
        return True
    if annotation is str:
        return True
    if isinstance(annotation, str) and annotation in ("str", "builtins.str"):
        return True
    return False
