"""Host Toplevel chrome helper (``configure_window``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry import configure_window


@pytest.fixture
def root():
    import tkinter as tk

    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def test_configure_window_title_geometry_minsize(root) -> None:
    configure_window(
        root,
        title="tkwry-chrome",
        geometry="640x480",
        minsize=(400, 300),
    )
    assert root.title() == "tkwry-chrome"
    assert root.minsize() == (400, 300)


def test_configure_window_omitted_kwargs_leave_state(root) -> None:
    configure_window(root, title="keep-me", minsize=(320, 240))
    configure_window(root, geometry="800x600")
    assert root.title() == "keep-me"
    assert root.minsize() == (320, 240)


def test_configure_window_topmost_and_fullscreen_forward(root) -> None:
    # ``-topmost`` / ``-fullscreen`` are WM-dependent. Under Xvfb (and with
    # ``withdraw()``) the attribute often does not stick — only require the
    # helper to forward without raising.
    configure_window(root, topmost=True)
    configure_window(root, topmost=False)
    configure_window(root, fullscreen=True)
    configure_window(root, fullscreen=False)


def test_configure_window_rejects_bad_minsize(root) -> None:
    with pytest.raises(TypeError, match="minsize"):
        configure_window(root, minsize=(1, 2, 3))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="minsize"):
        configure_window(root, minsize=(1.5, 2))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="minsize"):
        configure_window(root, minsize=(-1, 10))


def test_configure_window_icon_png(root, tmp_path: Path) -> None:
    # 1×1 PNG
    png = tmp_path / "icon.png"
    png.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )
    configure_window(root, icon=png)
    assert getattr(root, "_tkwry_window_icon") is not None


def test_configure_window_icon_missing(root, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="window icon not found"):
        configure_window(root, icon=tmp_path / "missing.png")
