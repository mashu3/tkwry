#!/usr/bin/env python3
"""Assert a PyInstaller (or similar) freeze tree contains ``tkwry._core``.

Used by ``.github/workflows/freeze.yml``. Exit 0 on success.

    python scripts/check_freeze_artifact.py dist
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} DIST_ROOT", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    # PyInstaller onedir / .app: native ext as _core*.pyd / .so / .dylib
    candidates = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and (
            p.name.startswith("_core")
            or p.name.startswith("tkwry._core")
            or (p.parent.name == "tkwry" and p.stem.startswith("_core"))
        )
        and p.suffix.lower() in {".pyd", ".so", ".dylib", ""}
    ]
    # Empty suffix: some layouts use extension-less Mach-O; still require
    # "core" in the path under a tkwry package folder.
    if not candidates:
        # Broader fallback: any file path containing tkwry and _core.
        candidates = [
            p
            for p in root.rglob("*")
            if p.is_file() and "tkwry" in p.parts and "_core" in p.name
        ]

    if not candidates:
        print(f"error: no tkwry._core native module under {root}", file=sys.stderr)
        print("tree (depth-limited):", file=sys.stderr)
        for i, p in enumerate(sorted(root.rglob("*"))):
            if i >= 80:
                print("  …", file=sys.stderr)
                break
            print(f"  {p.relative_to(root)}", file=sys.stderr)
        return 1

    print("ok: found native module(s):")
    for p in sorted(candidates)[:20]:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
