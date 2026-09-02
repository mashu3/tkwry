#!/usr/bin/env python3
"""Fail if README.md is not PyPI-safe (absolute GitHub links + renderable).

PyPI long-description has no repo tree, so relative ``docs/`` / ``examples/``
links break. D11: keep README links as ``https://github.com/mashu3/tkwry/blob/main/...``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BLOB_PREFIX = "https://github.com/mashu3/tkwry/blob/main/"
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def github_heading_slug(heading: str) -> str:
    """Match GitHub-style heading anchors (spaces → ``-``, drop other punctuation)."""
    text = heading.strip().lstrip("#").strip().lower()
    text = text.replace(" ", "-")
    # Keep hyphens from spaces; drop emoji / punctuation (may leave a leading ``-``).
    return re.sub(r"[^a-z0-9-]", "", text)


def headings_in(path: Path) -> set[str]:
    slugs: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            slugs.add(github_heading_slug(line))
    return slugs


def main() -> int:
    text = README.read_text(encoding="utf-8")
    errors: list[str] = []

    for label, url in LINK_RE.findall(text):
        if url.startswith(("http://", "https://", "mailto:")):
            if url.startswith(BLOB_PREFIX):
                rel = url[len(BLOB_PREFIX) :]
                file_part, _, anchor = rel.partition("#")
                target = ROOT / file_part
                if not file_part:
                    errors.append(f"empty path after blob prefix: [{label}]({url})")
                elif not target.is_file():
                    errors.append(f"missing file for [{label}]({url}) -> {file_part}")
                elif anchor:
                    slugs = headings_in(target)
                    if anchor not in slugs:
                        sample = ", ".join(sorted(slugs)[:12])
                        errors.append(
                            f"missing anchor #{anchor} in {file_part} "
                            f"(link [{label}]({url})); have [{sample}]"
                        )
            continue
        if url.startswith("#"):
            slugs = headings_in(README)
            anchor = url[1:]
            if anchor and anchor not in slugs:
                errors.append(f"missing README anchor [{label}]({url})")
            continue
        errors.append(
            f"relative link not PyPI-safe: [{label}]({url}) (use {BLOB_PREFIX}…)"
        )

    try:
        from readme_renderer.markdown import render as render_md
    except ImportError:
        errors.append(
            'readme-renderer not installed (pip install "readme-renderer[md]")'
        )
    else:
        html = render_md(text)
        if not html:
            errors.append(
                "readme-renderer returned empty HTML for README.md "
                '(install Markdown extras: pip install "readme-renderer[md]")'
            )

    if errors:
        print("check_readme_pypi_links: FAILED", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("check_readme_pypi_links: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
