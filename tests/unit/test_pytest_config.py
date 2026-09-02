"""Pytest configuration helpers (marker wiring)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.pytest_markers import (
    apply_integration_markers,
    item_wants_integration_marker,
)


def test_item_wants_integration_marker_matches_integration_and_macos() -> None:
    assert item_wants_integration_marker(
        "/home/runner/work/tkwry/tkwry/tests/integration/test_layout.py"
    )
    assert item_wants_integration_marker("D:/a/tkwry/tkwry/tests/macos/test_input.py")


def test_item_wants_integration_marker_skips_unit_tests() -> None:
    assert not item_wants_integration_marker(
        "/home/runner/work/tkwry/tkwry/tests/unit/test_api.py"
    )
    assert not item_wants_integration_marker("D:/a/tkwry/tkwry/tests/unit/test_api.py")


def test_apply_integration_markers_tags_integration_tests_only() -> None:
    integration_item = MagicMock()
    integration_item.path = Path("/repo/tests/integration/test_layout.py")
    unit_item = MagicMock()
    unit_item.path = Path("/repo/tests/unit/test_api.py")

    apply_integration_markers([integration_item, unit_item])

    integration_item.add_marker.assert_called_once_with(pytest.mark.integration)
    unit_item.add_marker.assert_not_called()
