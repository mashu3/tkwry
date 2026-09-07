import sys
import traceback

import pytest


@pytest.fixture(autouse=True)
def _linux_integration_teardown(tk_root) -> None:
    yield
    if sys.platform == "linux":
        from tkwry._linux import GtkPump, drain_gtk_with_tk

        try:
            drain_gtk_with_tk(tk_root, rounds=16)
        except Exception:
            traceback.print_exc()
        GtkPump.reset_all()
