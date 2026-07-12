from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _linux_integration_isolation(tk_root) -> None:
    if sys.platform != "linux":
        yield
        return
    from tkwry._linux import GtkPump, drain_gtk_with_tk

    GtkPump.reset_all()
    yield
    if GtkPump._by_root_key:
        drain_gtk_with_tk(tk_root)
    GtkPump.reset_all()
