#!/usr/bin/env bash
# Shared macOS CI runner for GitHub Actions.
# Off-thread sync-hook unit tests (worker + Tk pump) can Abort under GC after a
# long create/destroy streak when packed into one pytest process with the rest
# of ``tests/`` — same class of flake already isolated on Linux / Windows.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pytest tests/unit/test_sync_hooks.py -v --tb=short
pytest tests/ --ignore=tests/unit/test_sync_hooks.py -v --tb=short
