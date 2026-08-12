"""WebSession construction and validation (no native WebView required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tkwry import WebSession


def test_session_creates_data_directory(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    assert not profile.exists()
    session = WebSession(data_directory=profile)
    assert profile.is_dir()
    assert session.data_directory == profile.resolve()
    assert session.ephemeral is False


def test_session_ephemeral_has_no_directory() -> None:
    session = WebSession(ephemeral=True)
    assert session.data_directory is None
    assert session.ephemeral is True


def test_session_rejects_ephemeral_with_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        WebSession(data_directory=tmp_path, ephemeral=True)


def test_native_session_exposed() -> None:
    session = WebSession(ephemeral=True)
    assert session.native is not None
    assert session.native.ephemeral is True
