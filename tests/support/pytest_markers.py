"""Pytest marker helpers (importable outside conftest plugins)."""

from __future__ import annotations

import pytest


def item_wants_integration_marker(path: str) -> bool:
    """Return whether *path* (posix) should receive ``pytest.mark.integration``."""
    return "/tests/integration/" in path or "/tests/macos/" in path


def apply_integration_markers(items: list[pytest.Item]) -> None:
    """Tag integration / macOS GUI tests for ``-m integration`` selection."""
    integration = pytest.mark.integration
    for item in items:
        if item_wants_integration_marker(item.path.as_posix()):
            item.add_marker(integration)
