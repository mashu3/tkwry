"""Package version (single source: Cargo.toml)."""

from __future__ import annotations

import re
from pathlib import Path


def _read_cargo_version() -> str:
    cargo = Path(__file__).resolve().parents[1] / "Cargo.toml"
    match = re.search(
        r'^version = "([^"]+)"',
        cargo.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError("version not found in Cargo.toml")
    return match.group(1)


def resolve_version() -> str:
    try:
        from importlib.metadata import version

        return version("tkwry")
    except Exception:
        return _read_cargo_version()


__version__ = resolve_version()
