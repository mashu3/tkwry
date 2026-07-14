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

# WebKitGTK hangs in a single pytest process after many WebViews; split suites.
pytest tests/unit/ -v --tb=short
cleanup_webkit
pytest tests/integration/test_content.py -v --tb=short
cleanup_webkit
pytest tests/integration/test_layout.py -v --tb=short
cleanup_webkit
pytest tests/integration/test_viewport.py -v --tb=short
cleanup_webkit
pytest tests/integration/test_multi_webview.py -v --tb=short
cleanup_webkit
pytest tests/integration/test_lifecycle.py -v --tb=short
