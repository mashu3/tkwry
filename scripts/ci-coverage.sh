# Optional coverage helpers for ``run-*-ci-tests.sh``.
# Enable with ``TKWRY_COVERAGE=1`` (Codecov workflow). Default CI stays unchanged.
#
# Multi-process suites need ``--cov-append`` + a shared ``COVERAGE_FILE``.

ci_coverage_prepare() {
  if [[ "${TKWRY_COVERAGE:-}" != "1" ]]; then
    return 0
  fi
  export COVERAGE_FILE="${COVERAGE_FILE:-$(pwd)/.coverage}"
  rm -f "${COVERAGE_FILE}" "${COVERAGE_FILE}".* coverage.xml 2>/dev/null || true
}

ci_coverage_finalize() {
  if [[ "${TKWRY_COVERAGE:-}" != "1" ]]; then
    return 0
  fi
  python -m coverage xml -o coverage.xml
  python -m coverage report
}
