#!/usr/bin/env bash
# Run the Linux test suite locally (macOS/Windows host → Docker).
# Closer to GitHub Actions when using --ci (and --amd64 on Apple Silicon).
#
#   docker/run-linux-tests.sh --build --ci
#   docker/run-linux-tests.sh --build --ci --amd64
#   docker/run-linux-tests.sh -- /app/tests/integration/test_content.py -v
#   docker/run-linux-tests.sh --live -- /app/tests/integration/test_multi_webview.py -v
#   docker/run-linux-tests.sh --exec -- python3 -c 'print("ok")'
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${TKWRY_LINUX_IMAGE:-tkwry-linux-test}"
GITHUB_ACTIONS="${GITHUB_ACTIONS:-false}"
PLATFORM="${TKWRY_LINUX_PLATFORM:-}"
mode="pytest" # pytest | ci | exec
build=false
live=false
pytest_args=("/app/tests/" "-v" "--tb=short")
exec_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build=true; shift ;;
    --ci)
      GITHUB_ACTIONS=true
      mode=ci
      shift
      ;;
    --amd64)
      PLATFORM=linux/amd64
      shift
      ;;
    --live)
      # Mount host tests/ + webview.py for fast edit/run without rebuild.
      live=true
      shift
      ;;
    --exec)
      mode=exec
      shift
      ;;
    --)
      shift
      if [[ "$mode" == exec ]]; then
        exec_args=("$@")
      else
        pytest_args=("$@")
      fi
      break
      ;;
    *)
      if [[ "$mode" == exec ]]; then
        exec_args=("$@")
      else
        pytest_args=("$@")
      fi
      break
      ;;
  esac
done

platform_args=()
if [[ -n "$PLATFORM" ]]; then
  platform_args=(--platform "$PLATFORM")
fi

if $build || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build ${platform_args[@]+"${platform_args[@]}"} \
    -f "$ROOT/docker/Dockerfile.linux-test" \
    -t "$IMAGE" \
    "$ROOT"
fi

run_common=(
  docker run --rm --shm-size=2g
  ${platform_args[@]+"${platform_args[@]}"}
  -e PYTHONUNBUFFERED=1
  -e TK_SILENCE_DEPRECATION=1
  -e GITHUB_ACTIONS="$GITHUB_ACTIONS"
  -e PYTHONPATH=/app/tests
)

if $live; then
  run_common+=(
    -v "$ROOT/tkwry/webview.py:/usr/local/lib/python3.12/dist-packages/tkwry/webview.py:ro"
    -v "$ROOT/tkwry/_linux.py:/usr/local/lib/python3.12/dist-packages/tkwry/_linux.py:ro"
    -v "$ROOT/tests:/app/tests:ro"
    -v "$ROOT/scripts:/app/scripts:ro"
  )
fi

run_common+=("$IMAGE")

start_xvfb='
  Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
  sleep 3
  export DISPLAY=:99
'

if [[ "$mode" == ci ]]; then
  "${run_common[@]}" bash -c "$start_xvfb"'
    exec /app/scripts/run-linux-ci-tests.sh
  '
elif [[ "$mode" == exec ]]; then
  if [[ ${#exec_args[@]} -eq 0 ]]; then
    echo "usage: docker/run-linux-tests.sh [--live] --exec -- <command>..." >&2
    exit 2
  fi
  "${run_common[@]}" bash -c "$start_xvfb"'
    exec "$@"
  ' _ "${exec_args[@]}"
else
  "${run_common[@]}" bash -c "$start_xvfb"'
    exec pytest "$@"
  ' _ "${pytest_args[@]}"
fi
