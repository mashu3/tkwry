#!/usr/bin/env bash
# Shared macOS CI runner for GitHub Actions.
#
# Off-thread sync-hook unit tests (worker + Tk pump) can Abort under GC after a
# long create/destroy streak when packed into one pytest process with the rest
# of ``tests/`` — same class of flake already isolated on Linux / Windows.
# Do not spawn extra OS threads from remaining unit tests in this process
# (see ``test_install_tabbing_disable_off_main_defers_to_tk_init``).
#
# A single ``pytest tests/`` process (~850 cases) also wedges Tk / WKWebView on
# GHA after many create/destroy cycles — log stuck at ``collected N items``
# until the job times out; re-runs often pass. Mirror Linux/Windows: unit, then
# each integration module, then ``tests/macos``, in separate pytest processes.
#
# On GHA, reap leftover WebKit helper processes between suites (same role as
# Linux ``pkill`` / Windows ``msedgewebview2`` cleanup). Skipped locally so a
# maintainer run does not kill Safari tabs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck source=ci-coverage.sh
source "$(dirname "$0")/ci-coverage.sh"

export TK_SILENCE_DEPRECATION="${TK_SILENCE_DEPRECATION:-1}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

cleanup_webkit() {
  if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
    return 0
  fi
  # WebContent / Networking / GPU helpers left by embed tests.
  pkill -9 -f 'com\.apple\.WebKit\.' 2>/dev/null || true
}

run_pytest() {
  # Prefer array expansion so coverage flags stay intact under ``set -u``.
  local -a cov_args=()
  if [[ "${TKWRY_COVERAGE:-}" == "1" ]]; then
    cov_args=(--cov=tkwry --cov-append --cov-report=)
  fi
  pytest "$@" -v --tb=short "${cov_args[@]}"
  cleanup_webkit
}

ci_coverage_prepare
run_pytest tests/unit/test_sync_hooks.py
run_pytest tests/unit/ --ignore=tests/unit/test_sync_hooks.py
for integration_test in tests/integration/test_*.py; do
  run_pytest "$integration_test"
done
run_pytest tests/macos/
ci_coverage_finalize
