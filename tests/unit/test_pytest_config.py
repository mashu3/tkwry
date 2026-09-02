"""Pytest configuration helpers (marker wiring)."""

from __future__ import annotations

import subprocess
import sys


def test_integration_marker_selects_integration_tests() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "integration",
            "tests/integration/test_layout.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "test_" in result.stdout


def test_integration_marker_excludes_unit_tests() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "integration",
            "tests/unit/test_api.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 5
    assert "no tests collected" in result.stdout.lower()
