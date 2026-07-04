"""Package version (single source: Cargo.toml via wheel metadata)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    try:
        return version("tkwry")
    except PackageNotFoundError:
        pass

    # Dev-only fallback: read Cargo.toml when running from source tree
    # without `maturin develop` / `pip install -e .`
    import re
    from pathlib import Path

    cargo = Path(__file__).resolve().parents[1] / "Cargo.toml"
    if not cargo.is_file():
        return "0.0.0"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        cargo.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        return "0.0.0"
    return match.group(1)


__version__: str = _resolve_version()
