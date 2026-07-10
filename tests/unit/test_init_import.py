"""Tests for package import error handling."""

from __future__ import annotations

import importlib

import pytest

tkwry_init = importlib.import_module("tkwry.__init__")


def test_missing_core_module_is_detected() -> None:
    exc = ModuleNotFoundError("No module named 'tkwry._core'", name="tkwry._core")
    assert tkwry_init._is_missing_core_extension(exc) is True


def test_unrelated_import_error_is_not_missing_core() -> None:
    exc = ImportError("cannot import name 'foo' from tkwry._core.utils")
    assert tkwry_init._is_missing_core_extension(exc) is False


def test_import_error_mentioning_core_in_message_is_not_missing_core() -> None:
    exc = ImportError("failed while loading tkwry._core helper dependency")
    assert tkwry_init._is_missing_core_extension(exc) is False


def test_import_error_with_core_name_is_detected() -> None:
    exc = ImportError("extension load failed", name="tkwry._core")
    assert tkwry_init._is_missing_core_extension(exc) is True


def test_import_error_with_core_cause_is_detected() -> None:
    cause = ModuleNotFoundError("No module named 'tkwry._core'", name="tkwry._core")
    exc = ImportError("initialization failed")
    exc.__cause__ = cause
    assert tkwry_init._is_missing_core_extension(exc) is True


def test_linux_core_build_hint_reraise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tkwry_init.sys, "platform", "linux")
    exc = ModuleNotFoundError("No module named 'tkwry._core'", name="tkwry._core")

    with pytest.raises(ImportError, match="pre-built wheels") as raised:
        tkwry_init._reraise_linux_core_build_hint(exc)

    assert raised.value.__cause__ is exc


def test_non_linux_core_error_is_not_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tkwry_init.sys, "platform", "darwin")
    exc = ModuleNotFoundError("No module named 'tkwry._core'", name="tkwry._core")

    with pytest.raises(ModuleNotFoundError, match="tkwry._core"):
        tkwry_init._reraise_linux_core_build_hint(exc)
