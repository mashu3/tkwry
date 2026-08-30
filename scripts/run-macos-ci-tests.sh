#!/usr/bin/env bash
# Shared macOS CI runner for GitHub Actions.
# Off-thread sync-hook unit tests (worker + Tk pump) can Abort under GC after a
# long create/destroy streak when packed into one pytest process with the rest
# of ``tests/`` — same class of flake already isolated on Linux / Windows.
# Do not spawn extra OS threads from remaining unit tests in this process
# (see ``test_install_tabbing_disable_off_main_defers_to_tk_init``).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pytest tests/unit/test_sync_hooks.py -v --tb=short
pytest tests/ --ignore=tests/unit/test_sync_hooks.py -v --tb=short
