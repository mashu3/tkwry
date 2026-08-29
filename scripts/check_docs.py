#!/usr/bin/env python3
"""Docs ↔ public API checks (D15).

Fails when:

1. A ``tkwry.__all__`` export (except ``__version__``) is missing from the
   ``docs/usage.md`` API summary section (backtick form).
2. A ``python`` fence in README / ``docs/*.md`` does not ``ast.parse``.
3. A relative markdown link under README / ``docs/*.md`` points at a missing
   file, missing example path, or missing heading anchor.

README PyPI absolute-link policy stays in ``check_readme_pypi_links.py``.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "docs" / "usage.md"
DOC_FILES = [
    ROOT / "README.md",
    *(sorted((ROOT / "docs").glob("*.md"))),
]
# Maintainers guide is local / gitignored — not part of the published docs set.
DOC_FILES = [p for p in DOC_FILES if p.name != "MAINTAINERS.md"]

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
BACKTICK_NAME_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
ALL_EXEMPT = frozenset({"__version__"})
BLOB_PREFIX = "https://github.com/mashu3/tkwry/blob/main/"


def github_heading_slug(heading: str) -> str:
    """Match GitHub-style heading anchors (spaces → ``-``, drop other punctuation)."""
    text = heading.strip().lstrip("#").strip().lower()
    text = text.replace(" ", "-")
    return re.sub(r"[^a-z0-9-]", "", text)


def headings_in(path: Path) -> set[str]:
    slugs: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            slugs.add(github_heading_slug(line))
    return slugs


def api_summary_section(usage_text: str) -> str:
    marker = "## API summary"
    start = usage_text.find(marker)
    if start < 0:
        raise SystemExit(f"{USAGE.relative_to(ROOT)}: missing `{marker}` heading")
    rest = usage_text[start + len(marker) :]
    # Stop at the next H2 (Related), else end of file.
    next_h2 = re.search(r"\n## ", rest)
    if next_h2 is not None:
        rest = rest[: next_h2.start()]
    return rest


def public_all_names() -> list[str]:
    """Parse ``tkwry/__init__.py`` ``__all__`` without importing the native ext.

    The lint job does not build wheels; reading the list from source keeps D15
    runnable alongside ruff / README link checks.
    """
    init_path = ROOT / "tkwry" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if not isinstance(node.value, ast.List):
                    raise SystemExit("tkwry/__init__.py: __all__ is not a list")
                names: list[str] = []
                for elt in node.value.elts:
                    if not isinstance(elt, ast.Constant) or not isinstance(
                        elt.value, str
                    ):
                        raise SystemExit(
                            "tkwry/__init__.py: __all__ entries must be string literals"
                        )
                    names.append(elt.value)
                return names
    raise SystemExit("tkwry/__init__.py: __all__ not found")


def check_all_vs_api_summary(errors: list[str]) -> None:
    section = api_summary_section(USAGE.read_text(encoding="utf-8"))
    documented = set(BACKTICK_NAME_RE.findall(section))
    for name in public_all_names():
        if name in ALL_EXEMPT:
            continue
        if name not in documented:
            errors.append(
                f"__all__ name `{name}` missing from docs/usage.md "
                f"API summary (expected in backticks)"
            )


def iter_python_fences(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    fences: list[tuple[int, str]] = []
    # Line-oriented scan so we can report 1-based start lines.
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            lang = line[3:].strip().lower()
            start_line = i + 1
            i += 1
            body_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                body_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                fences.append((start_line, "".join(body_lines)))
                break
            # closing fence
            if lang in {"", "python", "py"}:
                fences.append((start_line, "".join(body_lines)))
            i += 1
            continue
        i += 1
    return fences


def check_python_fences(errors: list[str]) -> None:
    for path in DOC_FILES:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        for start_line, body in iter_python_fences(path):
            try:
                ast.parse(body, filename=f"{rel}:{start_line}")
            except SyntaxError as exc:
                errors.append(
                    f"{rel}:{start_line}: python fence SyntaxError: {exc.msg} "
                    f"(line {exc.lineno})"
                )


def resolve_link_target(source: Path, url: str) -> tuple[Path | None, str | None]:
    """Return (file_path, anchor) for a relative or same-doc link."""
    if url.startswith(("http://", "https://", "mailto:")):
        return None, None
    file_part, _, anchor = url.partition("#")
    if not file_part:
        return source, anchor or None
    target = (source.parent / file_part).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        # Escape outside the repo — treat as error via missing file.
        return target, anchor or None
    return target, anchor or None


def check_links(errors: list[str]) -> None:
    for path in DOC_FILES:
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for label, url in LINK_RE.findall(text):
            # README absolute GitHub blob links: existence checked by
            # check_readme_pypi_links.py; still verify local blob targets when
            # they point into this repo.
            if url.startswith(BLOB_PREFIX):
                rel_url = url[len(BLOB_PREFIX) :]
                file_part, _, anchor = rel_url.partition("#")
                target = ROOT / file_part
                if not file_part or not target.is_file():
                    errors.append(f"{rel}: missing blob target for [{label}]({url})")
                    continue
                if anchor and anchor not in headings_in(target):
                    errors.append(
                        f"{rel}: missing anchor #{anchor} for [{label}]({url})"
                    )
                continue
            if url.startswith(("http://", "https://", "mailto:")):
                continue

            target, anchor = resolve_link_target(path, url)
            if target is None:
                continue
            if not target.is_file():
                try:
                    shown = target.relative_to(ROOT.resolve())
                except ValueError:
                    shown = target
                errors.append(f"{rel}: missing file for [{label}]({url}) -> {shown}")
                continue
            if anchor and anchor not in headings_in(target):
                sample = ", ".join(sorted(headings_in(target))[:8])
                errors.append(
                    f"{rel}: missing anchor #{anchor} for [{label}]({url}); "
                    f"have [{sample}]"
                )


def main() -> int:
    errors: list[str] = []
    check_all_vs_api_summary(errors)
    check_python_fences(errors)
    check_links(errors)

    if errors:
        print("docs check failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        f"docs check ok "
        f"({len(DOC_FILES)} files, __all__ ↔ API summary, python fences, links)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
