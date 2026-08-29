"""Host Toplevel chrome helpers (Tk, not the WebView).

Window title / icon / geometry / min·max size / fullscreen / ``-topmost``
belong on the host ``Tk`` / ``Toplevel``. The WebView only follows its
``Frame`` via ``sync_bounds`` — these helpers deliberately do **not** touch
native WebView bounds.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def configure_window(
    window: tk.Wm,
    *,
    title: str | None = None,
    geometry: str | None = None,
    minsize: tuple[int, int] | None = None,
    maxsize: tuple[int, int] | None = None,
    fullscreen: bool | None = None,
    topmost: bool | None = None,
    icon: str | Path | None = None,
) -> None:
    """Apply host-window chrome; omitted kwargs are left unchanged.

    Parameters
    ----------
    window:
        A ``tk.Tk`` or ``tk.Toplevel`` (anything implementing ``tk.Wm``).
    title, geometry:
        Passed to ``title`` / ``geometry``.
    minsize, maxsize:
        ``(width, height)`` in pixels for ``minsize`` / ``maxsize``.
    fullscreen, topmost:
        Mapped to ``attributes("-fullscreen", …)`` /
        ``attributes("-topmost", …)``.
    icon:
        Path to an image. ``.ico`` uses ``iconbitmap`` on Windows; otherwise
        ``iconphoto`` with ``PhotoImage`` (PNG / GIF / PPM). The image is
        kept alive on ``window`` so Tk does not drop it.

    This is **not** a WebView size / title / icon API — use the host Frame
    and ``sync_bounds`` for native bounds.
    """
    if title is not None:
        window.title(title)
    if geometry is not None:
        window.geometry(geometry)
    if minsize is not None:
        width, height = _size_pair(minsize, name="minsize")
        window.minsize(width, height)
    if maxsize is not None:
        width, height = _size_pair(maxsize, name="maxsize")
        window.maxsize(width, height)
    if fullscreen is not None:
        window.attributes("-fullscreen", bool(fullscreen))
    if topmost is not None:
        window.attributes("-topmost", bool(topmost))
    if icon is not None:
        _apply_icon(window, Path(icon))


def _size_pair(value: tuple[int, int], *, name: str) -> tuple[int, int]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(f"{name} must be a tuple[int, int], got {value!r}")
    width, height = value
    if isinstance(width, bool) or isinstance(height, bool):
        raise TypeError(f"{name} must be a tuple[int, int], got {value!r}")
    if not isinstance(width, int) or not isinstance(height, int):
        raise TypeError(f"{name} must be a tuple[int, int], got {value!r}")
    if width < 0 or height < 0:
        raise ValueError(f"{name} values must be >= 0, got {value!r}")
    return width, height


def _apply_icon(window: tk.Wm, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"window icon not found: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".ico":
        if sys.platform != "win32":
            raise ValueError(
                "configure_window(icon=): .ico is Windows-only; "
                "use PNG, GIF, or PPM on this platform"
            )
        window.iconbitmap(str(path))
        return
    # PhotoImage must outlive the call; stash on the window.
    widget = window  # Tk / Toplevel are widgets implementing Wm
    image = tk.PhotoImage(file=str(path), master=widget)  # type: ignore[arg-type]
    window.iconphoto(True, image)
    setattr(window, "_tkwry_window_icon", image)
