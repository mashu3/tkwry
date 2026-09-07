"""Navigation policy helpers and event objects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

NavigationPolicyHandler: TypeAlias = Callable[["NavigationEvent"], bool]
NavigationHandler: TypeAlias = Callable[["NavigationEvent"], bool]


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
    """A navigation or new-window request from the engine.

    Used by ``on_navigation`` / ``navigation_policy`` and ``on_new_window``.
    Engines currently expose only the target URL, so optional fields default
    honestly until wry provides richer metadata.

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
) -> bool:
    """Invoke *handler* with *event*."""
    return handler(event)


def coerce_navigation_result(result: object) -> bool:
    """Return *result* when it is a ``bool``; otherwise ``False``."""
    if type(result) is bool:
        return result
    return False
