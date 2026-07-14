#!/usr/bin/env bash
# Run the Linux test suite locally (macOS/Windows host → Docker).
# Closer to GitHub Actions when using --ci (and --amd64 on Apple Silicon).
#
#   docker/run-linux-tests.sh --build --ci
#   docker/run-linux-tests.sh --build --ci --amd64
#   docker/run-linux-tests.sh -- /app/tests/integration/test_content.py -v
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${TKWRY_LINUX_IMAGE:-tkwry-linux-test}"
GITHUB_ACTIONS="${GITHUB_ACTIONS:-false}"
PLATFORM="${TKWRY_LINUX_PLATFORM:-}"
mode="pytest" # pytest | ci
build=false
pytest_args=("/app/tests/" "-v" "--tb=short")

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
    --)
      shift
      pytest_args=("$@")
      break
      ;;
    *)
      pytest_args=("$@")
      break
      ;;
  esac
done

platform_args=()
if [[ -n "$PLATFORM" ]]; then
  platform_args=(--platform "$PLATFORM")
fi

if $build || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build "${platform_args[@]}" \
    -f "$ROOT/docker/Dockerfile.linux-test" \
    -t "$IMAGE" \
    "$ROOT"
fi

run_common=(
  docker run --rm --shm-size=2g
  "${platform_args[@]}"
  -e PYTHONUNBUFFERED=1
  -e TK_SILENCE_DEPRECATION=1
  -e GITHUB_ACTIONS="$GITHUB_ACTIONS"
  "$IMAGE"
)

if [[ "$mode" == ci ]]; then
  "${run_common[@]}" bash -c '
    Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
    sleep 3
    export DISPLAY=:99
    exec /app/scripts/run-linux-ci-tests.sh
  '
else
  "${run_common[@]}" bash -c '
    Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
    sleep 3
    export DISPLAY=:99
    exec pytest "$@"
  ' _ "${pytest_args[@]}"
fi
