"""Coverage for :mod:`tkwry._version` resolution paths."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import tkwry._version as version_mod


def test_cargo_toml_version_reads_workspace_file() -> None:
    assert version_mod._cargo_toml_version() == version_mod.__version__


def test_cargo_toml_version_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_file = tmp_path / "pkg" / "_version.py"
    fake_file.parent.mkdir()
    fake_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(version_mod, "__file__", str(fake_file))
    assert version_mod._cargo_toml_version() is None


def test_cargo_toml_version_unreadable_version_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[package]\nname = "x"\n', encoding="utf-8")
    fake_file = tmp_path / "tkwry" / "_version.py"
    fake_file.parent.mkdir()
    fake_file.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(version_mod, "__file__", str(fake_file))
    assert version_mod._cargo_toml_version() is None


def test_resolve_version_falls_back_to_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_mod, "_cargo_toml_version", lambda: None)
    monkeypatch.setattr(version_mod, "version", lambda _name: "9.9.9-dist")
    assert version_mod._resolve_version() == "9.9.9-dist"


def test_resolve_version_unknown_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_mod, "_cargo_toml_version", lambda: None)

    def boom(_name: str) -> str:
        raise PackageNotFoundError("tkwry")

    monkeypatch.setattr(version_mod, "version", boom)
    assert version_mod._resolve_version() == "0.0.0"


def test_module_reload_keeps_resolvable_version() -> None:
    reloaded = importlib.reload(version_mod)
    assert isinstance(reloaded.__version__, str)
    assert reloaded.__version__
