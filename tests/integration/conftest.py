import sys

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _linux_integration_teardown(tk_root) -> None:
    yield
    if sys.platform == "linux":
        from tkwry._linux import GtkPump, drain_gtk_with_tk

        try:
            drain_gtk_with_tk(tk_root, rounds=16)
        except Exception:
            pass
        GtkPump.reset_all()
