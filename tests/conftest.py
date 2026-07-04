"""Shared pytest fixtures."""

from __future__ import annotations

import glob
import os
import sys
import time

import pytest


def pytest_configure(config: pytest.Config) -> None:
    _ensure_windows_tcl_env()


def _ensure_windows_tcl_env() -> None:
    """Point Tcl/Tk at the bundled runtime (GHA can fail to auto-detect)."""
    if os.name != "nt":
        return
    tcl_root = os.path.join(sys.prefix, "tcl")
    if not os.path.isdir(tcl_root):
        return
    if "TCL_LIBRARY" not in os.environ:
        hits = glob.glob(os.path.join(tcl_root, "tcl*", "init.tcl"))
        if hits:
            os.environ["TCL_LIBRARY"] = os.path.dirname(hits[0])
    if "TK_LIBRARY" not in os.environ:
        hits = glob.glob(os.path.join(tcl_root, "tk*", "tk.tcl"))
        if hits:
            os.environ["TK_LIBRARY"] = os.path.dirname(hits[0])


def _create_tk_root():
    import tkinter as tk

    _ensure_windows_tcl_env()
    last_err: tk.TclError | None = None
    for attempt in range(5):
        try:
            root = tk.Tk()
            root.geometry("480x320")
            return root
        except tk.TclError as exc:
            last_err = exc
            if attempt + 1 < 5:
                time.sleep(0.25 * (attempt + 1))
    assert last_err is not None
    raise last_err


@pytest.fixture
def tk_root():
    import tkinter as tk

    if os.name == "nt":
        pytest.importorskip("tkinter")

    root = _create_tk_root()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
