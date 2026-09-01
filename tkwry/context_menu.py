"""Tk context menu API via a JavaScript ``contextmenu`` bridge."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeAlias

ContextMenuCallback: TypeAlias = Callable[[], None]
ContextMenuItem: TypeAlias = tuple[str | None, ContextMenuCallback | None]
ContextMenuHandler: TypeAlias = Callable[["ContextMenuEvent"], None]

CONTEXT_MENU_MARKER = "contextmenu"

# Injected when a host context menu or handler is registered.
CONTEXT_MENU_JS = """\
(function () {
  if (window.__tkwryContextMenuListener) {
    document.removeEventListener(
      "contextmenu",
      window.__tkwryContextMenuListener,
      true
    );
  }
  window.__tkwryContextMenuListener = function (event) {
    event.preventDefault();
    event.stopPropagation();
    var link = null;
    var el = event.target;
    while (el && el !== document.documentElement) {
      if (el.tagName === "A" && el.href) {
        link = String(el.href);
        break;
      }
      el = el.parentElement;
    }
    var sel = "";
    try {
      sel = String(window.getSelection() || "");
    } catch (e) {}
    if (!window.ipc || !window.ipc.postMessage) return;
    window.ipc.postMessage(JSON.stringify({
      __tkwry: "contextmenu",
      x: event.screenX,
      y: event.screenY,
      page_x: event.pageX,
      page_y: event.pageY,
      link_url: link,
      selected_text: sel || null
    }));
  };
  window.__tkwryContextMenu = true;
  document.addEventListener(
    "contextmenu",
    window.__tkwryContextMenuListener,
    true
  );
})();
"""

# Injected when the host clears all context-menu hooks on a live document.
CONTEXT_MENU_DISABLE_JS = """\
(function () {
  var listener = window.__tkwryContextMenuListener;
  if (listener) {
    document.removeEventListener("contextmenu", listener, true);
    window.__tkwryContextMenuListener = null;
  }
  window.__tkwryContextMenu = false;
})();
"""


@dataclass(frozen=True, slots=True)
class ContextMenuEvent:
    """A page context-menu request intercepted for the host Tk menu.

    Parameters
    ----------
    x, y:
        Screen coordinates (``event.screenX`` / ``screenY``).
    page_x, page_y:
        Document coordinates.
    link_url:
        Nearest anchor ``href`` under the click, if any.
    selected_text:
        Current selection text, if any.
    """

    x: int
    y: int
    page_x: int = 0
    page_y: int = 0
    link_url: str | None = None
    selected_text: str | None = None


def parse_context_menu_event(message: str) -> ContextMenuEvent | None:
    """Parse a ``{"__tkwry": "contextmenu", ...}`` IPC payload, or return ``None``."""
    try:
        data = json.loads(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("__tkwry") != CONTEXT_MENU_MARKER:
        return None
    try:
        x = int(data.get("x", 0))
        y = int(data.get("y", 0))
        page_x = int(data.get("page_x", 0))
        page_y = int(data.get("page_y", 0))
    except (TypeError, ValueError):
        return None
    link = data.get("link_url")
    if link is not None and not isinstance(link, str):
        link = None
    selected = data.get("selected_text")
    if selected is not None and not isinstance(selected, str):
        selected = None
    if selected == "":
        selected = None
    return ContextMenuEvent(
        x=x,
        y=y,
        page_x=page_x,
        page_y=page_y,
        link_url=link,
        selected_text=selected,
    )


def normalize_context_menu_items(
    items: Sequence[ContextMenuItem] | None,
) -> tuple[ContextMenuItem, ...] | None:
    """Validate and freeze context-menu item tuples.

    Each item is ``(label, callback)``. ``label is None`` marks a separator
    (callback ignored). Non-separator items require a callable callback.
    """
    if items is None:
        return None
    if isinstance(items, (str, bytes)):
        raise TypeError("context_menu items must be a sequence of (label, callback)")
    normalized: list[ContextMenuItem] = []
    for index, item in enumerate(items):
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(
                f"context_menu item {index} must be a (label, callback) tuple"
            )
        label, callback = item
        if label is None:
            normalized.append((None, None))
            continue
        if not isinstance(label, str) or not label:
            raise ValueError(
                f"context_menu item {index}: label must be a non-empty str "
                "or None for a separator"
            )
        if callback is None or not callable(callback):
            raise TypeError(
                f"context_menu item {index}: callback must be callable "
                f"(got {type(callback).__name__})"
            )
        normalized.append((label, callback))
    if not normalized:
        raise ValueError("context_menu items must not be empty")
    return tuple(normalized)


def merge_context_menu_script(
    script: str | None, *, context_menu_enabled: bool
) -> str | None:
    """Append the context-menu bridge when enabled."""
    if not context_menu_enabled:
        return script
    if script:
        if "__tkwryContextMenu" in script:
            return script
        return f"{script}\n{CONTEXT_MENU_JS}"
    return CONTEXT_MENU_JS
