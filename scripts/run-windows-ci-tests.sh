#!/usr/bin/env bash
# Windows CI test runner (GitHub Actions bash shell).
#
# WebView2 (especially arm64) can wedge Tk / hang ``update()`` after many
# create/destroy cycles in one process — same class of issue as WebKitGTK on
# Linux (see run-linux-ci-tests.sh) and tkipw's Windows e2e isolation.
# Run unit first, then each integration module in its own pytest process.
#
# Long-lived ``thread=True`` RPC workers (timeout / destroy / JS cancel) have
# also aborted the process on Windows arm64 with STATUS_BREAKPOINT
# (0x80000003) while ThreadPoolExecutor spins a thread under GC after a long
# ``test_content`` create/destroy streak. Keep those in a fresh process.
#
# Off-thread sync-hook unit tests similarly abort under GC after a long
# ``tests/unit/`` streak (Linux Aborted / Windows 0x80000003). Isolate them.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export TK_SILENCE_DEPRECATION="${TK_SILENCE_DEPRECATION:-1}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

CONTENT_RPC_WORKER_STRESS=(
  tests/integration/test_content.py::test_rpc_worker_timeout_rejects
  tests/integration/test_content.py::test_rpc_destroy_during_worker_call
  tests/integration/test_content.py::test_rpc_js_cancel_rejects_worker
)

pytest tests/unit/test_sync_hooks.py -v --tb=short
pytest tests/unit/ --ignore=tests/unit/test_sync_hooks.py -v --tb=short
pytest tests/integration/test_content.py -v --tb=short \
  --deselect "${CONTENT_RPC_WORKER_STRESS[0]}" \
  --deselect "${CONTENT_RPC_WORKER_STRESS[1]}" \
  --deselect "${CONTENT_RPC_WORKER_STRESS[2]}"
pytest "${CONTENT_RPC_WORKER_STRESS[@]}" -v --tb=short
pytest tests/integration/test_create_options.py -v --tb=short
pytest tests/integration/test_layout.py -v --tb=short
pytest tests/integration/test_lifecycle.py -v --tb=short
pytest tests/integration/test_multi_webview.py -v --tb=short
pytest tests/integration/test_notebook.py -v --tb=short
pytest tests/integration/test_viewport.py -v --tb=short
