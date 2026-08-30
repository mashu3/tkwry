#!/usr/bin/env bash
# Windows CI test runner (GitHub Actions bash shell).
#
# WebView2 (especially arm64) can wedge Tk / hang ``update()`` after many
# create/destroy cycles in one process — same class of issue as WebKitGTK on
# Linux (see run-linux-ci-tests.sh) and tkipw's Windows e2e isolation.
# Run unit first, then each integration module in its own pytest process.
#
# Long-lived ``thread=True`` RPC workers abort the process on Windows
# (0x80000003 / STATUS_BREAKPOINT) while ThreadPoolExecutor + GC run after a
# long ``test_content`` create/destroy streak. Isolate every ``thread=True``
# case in that file — not only timeout / destroy / JS cancel.
#
# Off-thread sync-hook unit tests similarly abort under GC after a long
# ``tests/unit/`` streak (Linux Aborted / Windows 0x80000003). Isolate them.
#
# Leftover ``msedgewebview2.exe`` survives pytest process exit and can wedge
# the next suite's Tk ``update()`` on windows-11-arm (Linux reaps WebKit
# between suites for the same reason). Kill helpers after each pytest.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export TK_SILENCE_DEPRECATION="${TK_SILENCE_DEPRECATION:-1}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

cleanup_webview2() {
  powershell.exe -NoProfile -NonInteractive -Command \
    'Get-Process -Name msedgewebview2 -ErrorAction SilentlyContinue | Stop-Process -Force' \
    >/dev/null 2>&1 || true
}

run_pytest() {
  pytest "$@" -v --tb=short
  cleanup_webview2
}

CONTENT_RPC_WORKER_STRESS=(
  tests/integration/test_content.py::test_rpc_worker_thread_does_not_block_handler_thread_flag
  tests/integration/test_content.py::test_rpc_worker_timeout_rejects
  tests/integration/test_content.py::test_rpc_destroy_during_worker_call
  tests/integration/test_content.py::test_rpc_js_cancel_rejects_worker
)

run_pytest tests/unit/test_sync_hooks.py
run_pytest tests/unit/ --ignore=tests/unit/test_sync_hooks.py
deselect_args=()
for node in "${CONTENT_RPC_WORKER_STRESS[@]}"; do
  deselect_args+=(--deselect "$node")
done
run_pytest tests/integration/test_content.py "${deselect_args[@]}"
run_pytest "${CONTENT_RPC_WORKER_STRESS[@]}"
run_pytest tests/integration/test_create_options.py
run_pytest tests/integration/test_layout.py
run_pytest tests/integration/test_lifecycle.py
run_pytest tests/integration/test_multi_webview.py
run_pytest tests/integration/test_notebook.py
run_pytest tests/integration/test_viewport.py
run_pytest tests/integration/test_browser_essentials.py
