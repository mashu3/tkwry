"""Package version (single source: Cargo.toml).

A source checkout prefers ``Cargo.toml`` so a version bump is visible before
the next ``maturin develop`` / ``pip install``. Installed wheels have no
``Cargo.toml`` beside the package and use distribution metadata instead.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _cargo_toml_version() -> str | None:
    cargo = Path(__file__).resolve().parents[1] / "Cargo.toml"
    if not cargo.is_file():
        return None
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        cargo.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group(1) if match else None


def _resolve_version() -> str:
    cargo_version = _cargo_toml_version()
    if cargo_version is not None:
        return cargo_version
    try:
        return version("tkwry")
    except PackageNotFoundError:
        return "0.0.0"


__version__: str = _resolve_version()
