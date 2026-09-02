"""Pytest configuration helpers (marker wiring)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import _item_wants_integration_marker, pytest_collection_modifyitems


def test_item_wants_integration_marker_matches_integration_and_macos() -> None:
    assert _item_wants_integration_marker(
        "/home/runner/work/tkwry/tkwry/tests/integration/test_layout.py"
    )
    assert _item_wants_integration_marker(
        "D:/a/tkwry/tkwry/tests/macos/test_input.py"
    )


def test_item_wants_integration_marker_skips_unit_tests() -> None:
    assert not _item_wants_integration_marker(
        "/home/runner/work/tkwry/tkwry/tests/unit/test_api.py"
    )
    assert not _item_wants_integration_marker(
        "D:/a/tkwry/tkwry/tests/unit/test_api.py"
    )


def test_collection_modifyitems_adds_integration_marker() -> None:
    integration_item = MagicMock()
    integration_item.path = Path(
        "/repo/tests/integration/test_layout.py"
    )
    unit_item = MagicMock()
    unit_item.path = Path("/repo/tests/unit/test_api.py")

    pytest_collection_modifyitems(MagicMock(), [integration_item, unit_item])

    integration_item.add_marker.assert_called_once_with(pytest.mark.integration)
    unit_item.add_marker.assert_not_called()
