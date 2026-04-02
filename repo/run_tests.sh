#!/usr/bin/env bash
# =============================================================================
# run_tests.sh — Execute all unit and API tests.
# Idempotent: safe to run multiple times without manual setup.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "============================================================"
echo "  SkillShare Platform — Test Suite"
echo "============================================================"

# ---------------------------------------------------------------------------
# 1. Dependency check
# ---------------------------------------------------------------------------
if ! python -c "import pytest" 2>/dev/null; then
    echo "[setup] Installing dependencies..."
    pip install -r requirements.txt --quiet
fi

# ---------------------------------------------------------------------------
# 2. Unit tests
# ---------------------------------------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  UNIT TESTS  (unit_tests/)"
echo "------------------------------------------------------------"
python -m pytest unit_tests/ \
    -v \
    --tb=short \
    --no-header \
    -q

UNIT_EXIT=$?

# ---------------------------------------------------------------------------
# 3. API tests
# ---------------------------------------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  API TESTS  (API_tests/)"
echo "------------------------------------------------------------"
python -m pytest API_tests/ \
    -v \
    --tb=short \
    --no-header \
    -q

API_EXIT=$?

# ---------------------------------------------------------------------------
# 4. Frontend tests (HTMX partials + SPA shell)
# ---------------------------------------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  FRONTEND TESTS  (frontend_tests/)"
echo "------------------------------------------------------------"
python -m pytest frontend_tests/ \
    -v \
    --tb=short \
    --no-header \
    -q

FE_EXIT=$?

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
if [ $UNIT_EXIT -eq 0 ] && [ $API_EXIT -eq 0 ] && [ $FE_EXIT -eq 0 ]; then
    echo "  ALL TESTS PASSED"
    exit 0
else
    echo "  SOME TESTS FAILED"
    [ $UNIT_EXIT -ne 0 ] && echo "  Unit tests:     FAILED (exit $UNIT_EXIT)"
    [ $API_EXIT  -ne 0 ] && echo "  API tests:      FAILED (exit $API_EXIT)"
    [ $FE_EXIT   -ne 0 ] && echo "  Frontend tests: FAILED (exit $FE_EXIT)"
    exit 1
fi
