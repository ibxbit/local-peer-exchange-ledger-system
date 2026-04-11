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
# 1. Prepare environment
# ---------------------------------------------------------------------------
echo "[setup] Ensuring Docker images are up to date..."
docker compose build --quiet api

# ---------------------------------------------------------------------------
# 2. Unit tests
# ---------------------------------------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  UNIT TESTS  (unit_tests/)"
echo "------------------------------------------------------------"
docker compose run --rm api pytest unit_tests/ \
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
docker compose run --rm api pytest API_tests/ \
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
docker compose run --rm api pytest frontend_tests/ \
    -v \
    --tb=short \
    --no-header \
    -q

FE_EXIT=$?

# ---------------------------------------------------------------------------
# 5. E2E tests (Browser smoke tests)
# ---------------------------------------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  E2E TESTS  (e2e_tests/)"
echo "------------------------------------------------------------"
# We use 'docker compose run' which respects 'depends_on' and 'service_healthy'
docker compose run --rm e2e

E2E_EXIT=$?

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo "[cleanup] Stopping any remaining containers..."
docker compose down --remove-orphans > /dev/null 2>&1

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
if [ $UNIT_EXIT -eq 0 ] && [ $API_EXIT -eq 0 ] && [ $FE_EXIT -eq 0 ] && [ $E2E_EXIT -eq 0 ]; then
    echo "  ALL TESTS PASSED"
    exit 0
else
    echo "  SOME TESTS FAILED"
    [ $UNIT_EXIT -ne 0 ] && echo "  Unit tests:     FAILED (exit $UNIT_EXIT)"
    [ $API_EXIT  -ne 0 ] && echo "  API tests:      FAILED (exit $API_EXIT)"
    [ $FE_EXIT   -ne 0 ] && echo "  Frontend tests: FAILED (exit $FE_EXIT)"
    [ $E2E_EXIT  -ne 0 ] && echo "  E2E tests:      FAILED (exit $E2E_EXIT)"
    exit 1
fi
