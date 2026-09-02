#!/usr/bin/env bash
# Shared Linux integration runner for GitHub Actions and local Docker.
# Expects: DISPLAY set, Xvfb already running, package installed, cwd usable
# from repo root (tests/ at ./tests).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Leftover WebKitNetworkProcess zombies can lock later suites on GHA x64.
cleanup_webkit() {
  pkill -9 -f '[Ww]eb[Kk]it' 2>/dev/null || true
}

# Off-thread sync-hook unit tests (worker + Tk pump) have aborted under GC
# after a long ``tests/unit/`` create/destroy streak on GHA. Run them first
# in a fresh process, then the rest of unit (ignore this file).
pytest tests/unit/test_sync_hooks.py -v --tb=short
cleanup_webkit
# WebKitGTK hangs in a single pytest process after many WebViews; split suites.
pytest tests/unit/ --ignore=tests/unit/test_sync_hooks.py -v --tb=short
cleanup_webkit
for integration_test in tests/integration/test_*.py; do
  pytest "$integration_test" -v --tb=short
  cleanup_webkit
done
