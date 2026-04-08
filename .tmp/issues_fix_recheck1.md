# Previous Issues Recheck

Date: 2026-04-08

## Recheck Summary

| Issue from previous inspection | Status | Notes |
|---|---|---|
| Non-Docker startup guidance missing/weak | Fixed | README now has explicit non-Docker quick start and verification commands. |
| AR/AP malformed date validation evidence weak | Fixed | Route-level date validation exists + API tests added and passing. |
| Browser E2E verification evidence missing | Not fixed (currently failing) | E2E test exists but currently times out in this environment/run. |

## Evidence

### 1) Non-Docker startup guidance

- `README.md:9` shows `Without Docker (recommended for local dev)`.
- `README.md:13` includes dependency install: `pip install -r requirements.txt`.
- `README.md:17` and `README.md:19` include start commands: `python3 run.py` and `py -3 run.py`.
- Additional dedicated section exists: `README.md:228` (`Running Without Docker (Local Development)`).

### 2) AR/AP date validation and tests

- Date param validator implemented in route layer:
  - `app/routes/ledger.py:15` (`_DATE_RE`)
  - `app/routes/ledger.py:18` (`_parse_date_param`)
  - Used by AR/AP routes at `app/routes/ledger.py:341`, `app/routes/ledger.py:344`, `app/routes/ledger.py:382`, `app/routes/ledger.py:385`.
- New date-validation tests present:
  - `API_tests/test_financial_summary_api.py:294` (`TestDateFilterValidation`)
  - AR invalid-date checks at `API_tests/test_financial_summary_api.py:302` onward.
  - AP invalid-date checks at `API_tests/test_financial_summary_api.py:349` onward.
- Runtime verification:
  - Command: `py -3 -m pytest API_tests/test_financial_summary_api.py -q`
  - Result: `44 passed in 12.17s`.

### 3) E2E verification

- E2E wiring still exists:
  - `package.json:7` (`test:e2e`)
  - `playwright.config.js:7` and `e2e_tests/smoke.spec.js:71`.
- Runtime execution result:
  - Command: `npm run test:e2e`
  - Result: **failed** (`1 failed`) with timeout at `e2e_tests/smoke.spec.js:129`.
  - Failure evidence: Playwright timeout waiting for `#l-user`; repeated navigation/`/api/auth/me` 401 loop in run log.

## Final Status

- Previously reported backend/documentation gaps are fixed.
- Remaining actionable gap: E2E smoke test is not passing and should be debugged before claiming complete closure.
