# Optional coverage helpers for ``run-*-ci-tests.sh``.
# Enable with ``TKWRY_COVERAGE=1`` (ci.yml macOS/Windows coverage matrix).
# Default / Linux / windows-11-arm CI stays wheel-only without coverage.
#
# Multi-process suites need ``--cov-append`` + a shared ``COVERAGE_FILE``.

ci_coverage_prepare() {
  if [[ "${TKWRY_COVERAGE:-}" != "1" ]]; then
    return 0
  fi
  export COVERAGE_FILE="${COVERAGE_FILE:-$(pwd)/.coverage}"
  rm -f "${COVERAGE_FILE}" coverage.xml
  # Clear parallel/append leftovers without relying on shell glob + ``set -u``.
  find . -maxdepth 1 -name '.coverage.*' -delete 2>/dev/null || true
}

ci_coverage_finalize() {
  if [[ "${TKWRY_COVERAGE:-}" != "1" ]]; then
    return 0
  fi
  python -m coverage xml -o coverage.xml
  python -m coverage report
  # Cobertura uses <source>…/tkwry</source> + filename="webview.py".
  # Wheel installs put site-packages in <source> → Codecov shows ~0%.
  if ! grep -q 'filename="webview.py"' coverage.xml; then
    echo "error: coverage.xml missing webview.py" >&2
    return 1
  fi
  if ! grep -E '<source>[^<]*[/\\]tkwry</source>' coverage.xml >/dev/null; then
    echo "error: coverage.xml <source> is not the checkout tkwry/ package dir" >&2
    grep -E '<source>' coverage.xml >&2 || true
    return 1
  fi
  if grep -E '<source>[^<]*site-packages[/\\]tkwry</source>' coverage.xml >/dev/null; then
    echo "error: coverage.xml <source> is site-packages (use maturin develop)" >&2
    grep -E '<source>' coverage.xml >&2 || true
    return 1
  fi
}
