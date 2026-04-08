# SkillShare Platform

A peer skill-exchange platform with ledger, identity verification, admin moderation, analytics, and offline payment support.

---

## Quick Start

### Without Docker (recommended for local dev)

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Start the server  (auto-creates instance/config.json and instance/app.db)
#    Linux / macOS:
python3 run.py
#    Windows:
py -3 run.py
```

The app starts on `http://127.0.0.1:5000` by default.  
Read the bootstrap password and verify the API is up:

```bash
# Read the generated admin password (Linux/macOS)
BOOTSTRAP_PW=$(python3 -c "import json; print(json.load(open('instance/config.json'))['ADMIN_BOOTSTRAP_PASSWORD'])")

# Verify login works
curl -s http://127.0.0.1:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$BOOTSTRAP_PW\"}" \
  | python3 -m json.tool
```

Expected response: `{"token": "...", "user": {"role": "admin", ...}}`

### With Docker

```bash
docker compose up
```

The application auto-seeds an admin account and initialises the SQLite database on first run.
Address: `http://localhost:8000`

---

## Services

| Service | Address | Description |
|---------|---------|-------------|
| Application (API + UI) | http://localhost:8000 | Flask app — REST API and frontend |

All REST endpoints are prefixed with `/api/`. The frontend SPA is served at `/`.

---

## Bootstrap Admin Credentials

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password source | `instance/config.json` -> `ADMIN_BOOTSTRAP_PASSWORD` |

On first login, the admin must change password before privileged actions.

---

## API Endpoint Summary

| Prefix | Description |
|--------|-------------|
| `POST /api/auth/register` | Register a new user |
| `POST /api/auth/login` | Login and receive JWT |
| `GET  /api/auth/me` | Get current user profile |
| `GET  /api/users` | List users (admin only) |
| `GET  /api/ledger/balance` | Check credit balance |
| `POST /api/ledger/credit` | Credit a user (admin only) |
| `POST /api/ledger/transfer` | Peer credit transfer |
| `POST /api/ledger/invoices` | Create an invoice |
| `POST /api/payments/submit` | Submit offline payment (cash/check/ACH) |
| `POST /api/payments/<id>/confirm` | Confirm payment and credit account (admin) |
| `POST /api/payments/<id>/refund` | Refund a confirmed payment (admin) |
| `GET  /api/ledger/ar-summary` | Accounts Receivable summary (admin/auditor) |
| `GET  /api/ledger/ap-summary` | Accounts Payable summary (admin/auditor) |
| `GET  /api/ledger/reconciliation-summary` | Invoice ↔ ledger reconciliation (admin/auditor) |
| `GET  /api/audit/logs` | Audit log listing (admin/auditor) |
| `GET  /api/audit/logs/summary` | Event counts by category |
| `GET  /api/audit/logs/verify` | SHA-256 chain integrity check |
| `GET  /api/analytics/kpis` | KPI dashboard |
| `GET  /api/analytics/export` | CSV export (kpi or daily) |
| `POST /api/admin/violations` | Report a violation |
| `PUT  /api/admin/users/<id>/ban` | Ban a user |
| `PUT  /api/admin/users/<id>/unban` | Unban a user |
| `GET  /api/admin/resources` | List schedule inventory resources |
| `POST /api/admin/resources` | Create schedule inventory resource |
| `PUT  /api/admin/resources/<id>` | Update schedule inventory resource |
| `DELETE /api/admin/resources/<id>` | Deactivate schedule inventory resource |
| `GET  /api/matching/search` | Search peers by skill / tag / time slot |
| `POST /api/matching/profile` | Create / update matching profile |
| `POST /api/matching/queue` | Join the auto-match queue |
| `PUT  /api/matching/queue/<id>/cancel` | Cancel a waiting queue entry |
| `POST /api/matching/sessions` | Request a session with a peer |
| `GET  /api/matching/peers-partial` | HTMX: peer search HTML fragment |
| `GET  /api/matching/queue/<id>/status-partial` | HTMX: queue status HTML fragment (polls every 10 s) |
| `GET  /api/matching/sessions-partial` | HTMX: session table HTML fragment |

---

## Step-by-Step Verification Guide

### 1. Start the application

```bash
docker compose up
```

Wait for the line: `Running on http://0.0.0.0:8000`

### 2. Verify the API is reachable

```bash
BOOTSTRAP_PW=$(python - <<'PY'
import json
print(json.load(open('instance/config.json'))['ADMIN_BOOTSTRAP_PASSWORD'])
PY
)

curl -s http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$BOOTSTRAP_PW\"}" | python -m json.tool
```

Expected: `{"token": "...", "user": {"role": "admin", ...}}`

If `user.must_change_password` is `true`, rotate immediately:

```bash
TOKEN=$(curl -s http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$BOOTSTRAP_PW\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:8000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"current_password\":\"$BOOTSTRAP_PW\",\"new_password\":\"Admin@Rotate123456!\"}" \
  | python -m json.tool
```

### 3. Register a user

```bash
curl -s http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"Alice@123456!"}' \
  | python -m json.tool
```

Expected: `{"message": "Registration successful.", "user_id": 2}`

### 4. Credit a user (admin)

```bash
# Get admin token first
TOKEN=$(curl -s http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$BOOTSTRAP_PW\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:8000/api/ledger/credit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":2,"amount":500,"description":"Welcome bonus"}' \
  | python -m json.tool
```

Expected: `{"message": "Credits added.", "new_balance": 500.0}`

### 5. Submit an offline payment

```bash
USER_TOKEN=$(curl -s http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"Alice@123456!"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:8000/api/payments/submit \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount":200,"payment_type":"check","reference_number":"CHK-00001"}' \
  | python -m json.tool
```

Expected: `{"payment_id": 1, "signature": "<64-char hex>"}`

### 6. Confirm the payment (admin)

```bash
curl -s http://localhost:8000/api/payments/1/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -X POST | python -m json.tool
```

Expected: `{"message": "Payment confirmed and account credited.", "new_balance": 700.0}`

### 7. Verify the audit chain

```bash
curl -s http://localhost:8000/api/audit/logs/verify \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Expected: `{"valid": true, "message": "Audit log chain is intact.", "entries": <n>}`

### 8. View the analytics dashboard

```bash
curl -s "http://localhost:8000/api/analytics/kpis" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Expected: JSON object with `kpis.conversion_rate`, `kpis.dispute_rate`, etc.

---

## Running Without Docker (Local Development)

### Prerequisites

- Python 3.11 or later
- pip

### Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the development server (auto-creates instance/config.json on first run)
#    Linux / macOS:
python3 run.py
#    Windows (cmd or PowerShell):
py -3 run.py
```

The app listens on `http://127.0.0.1:5000` by default.
Override host/port with environment variables:

```bash
# Linux / macOS
PORT=8000 HOST=0.0.0.0 python3 run.py

# Windows PowerShell
$env:PORT=8000; $env:HOST="0.0.0.0"; py -3 run.py
```

### First-time setup

On startup the app:
1. Creates `instance/config.json` with auto-generated secrets (SECRET_KEY, ENCRYPTION_KEY, PAYMENT_SIGNING_KEY, ADMIN_BOOTSTRAP_PASSWORD).
2. Initialises `instance/app.db` (SQLite).
3. Seeds the default admin account (`admin` / value of `ADMIN_BOOTSTRAP_PASSWORD`).

No SQL imports or manual configuration required.

### Verify the server is running

```bash
# Read bootstrap password (Linux / macOS)
BOOTSTRAP_PW=$(python3 -c "import json; print(json.load(open('instance/config.json'))['ADMIN_BOOTSTRAP_PASSWORD'])")

# Confirm the API responds
curl -s http://127.0.0.1:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$BOOTSTRAP_PW\"}" \
  | python3 -m json.tool

# Windows (PowerShell) — read password and hit /api/auth/me with a token
$pw = (Get-Content instance\config.json | ConvertFrom-Json).ADMIN_BOOTSTRAP_PASSWORD
$r  = Invoke-RestMethod http://127.0.0.1:5000/api/auth/login `
        -Method POST -ContentType "application/json" `
        -Body "{`"username`":`"admin`",`"password`":`"$pw`"}"
Invoke-RestMethod http://127.0.0.1:5000/api/auth/me `
  -Headers @{Authorization="Bearer $($r.token)"}
```

### Cross-platform notes

| Platform | Start command | Override port |
|----------|---------------|---------------|
| Linux / macOS | `python3 run.py` | `PORT=8000 python3 run.py` |
| Windows (cmd) | `py -3 run.py` | `set PORT=8000 && py -3 run.py` |
| Windows (PowerShell) | `py -3 run.py` | `$env:PORT=8000; py -3 run.py` |

---

## Running Tests

```bash
bash run_tests.sh
```

The script installs dependencies if needed, then runs:
- **Unit tests** in `unit_tests/` — service logic, hash chaining, HMAC verification, state transitions, boundary conditions, matching governance
- **API tests** in `API_tests/` — every REST endpoint, RBAC enforcement, error responses, matching governance API boundaries
- **Frontend tests** in `frontend_tests/` — SPA shell validation, HTMX partial endpoint responses, core UI flow states

### Browser E2E smoke test (Playwright)

A dedicated browser E2E suite is available in `e2e_tests/` to cover browser-only behavior.

```bash
# Install JS test dependencies
npm install

# Install Chromium used by Playwright
npx playwright install chromium

# Run browser smoke flow: login -> queue -> status poll -> logout
npm run test:e2e
```

Notes:
- By default Playwright starts the Flask server automatically using `run.py`.
- To target an already running server, set `PEX_E2E_USE_EXTERNAL_SERVER=1` and (optionally) `BASE_URL`.
- If your local admin password was already rotated to a custom value, set `PEX_ADMIN_PASSWORD` before running the E2E test.

Tests are idempotent and use isolated in-memory / temp databases. No manual setup required.

### Running individual suites

```bash
# Unit tests only
py -3 -m pytest unit_tests/ -v

# API tests only
py -3 -m pytest API_tests/ -v

# Frontend tests only
py -3 -m pytest frontend_tests/ -v

# Matching-specific tests
py -3 -m pytest unit_tests/test_matching.py API_tests/test_matching_api.py frontend_tests/test_ui.py -v

# Browser E2E only
npm run test:e2e
```

### Scheduler 2:00 AM ops check

To validate the real-clock scheduler behavior in CI/staging after 2:00 AM local time:

```bash
PEX_BASE_URL=http://127.0.0.1:5000 \
PEX_ADMIN_USER=admin \
PEX_ADMIN_PASSWORD='<admin password>' \
python scripts/check_daily_scheduler_firing.py
```

The check passes only when the latest saved daily report date equals yesterday.

---

## Project Structure

```
repo/
├── app/
│   ├── dal/            # Data Access Layer (SQL only, no business logic)
│   ├── routes/         # Flask blueprints (HTTP only, no SQL)
│   ├── services/       # Business logic layer
│   ├── models.py       # Schema, migrations, db() context manager
│   └── utils.py        # Crypto, hashing, JWT, decorators
├── unit_tests/         # Pytest unit tests (service + DAL layer)
├── API_tests/          # Pytest API tests (Flask test client)
├── static/             # Frontend assets (CSS, JS)
├── templates/          # SPA entry point (index.html)
├── config.py           # Auto-generated secrets + runtime config
├── run.py              # Application entry point
├── requirements.txt    # Python dependencies
├── Dockerfile
├── docker-compose.yml
└── run_tests.sh        # One-command test runner
```

---

## Financial Summary Endpoints (AR / AP / Reconciliation)

All three endpoints require **admin or auditor** role. Every access is audit-logged under
`AR_SUMMARY_ACCESSED`, `AP_SUMMARY_ACCESSED`, or `RECONCILIATION_ACCESSED`.

### GET /api/ledger/ar-summary — Accounts Receivable

Returns outstanding amounts owed **to** users as invoice issuers (status: `issued` / `overdue`).

**Query parameters**

| Parameter   | Type   | Description                                       |
|-------------|--------|---------------------------------------------------|
| `from_date` | string | ISO-8601 lower bound on `issued_at` (optional)    |
| `to_date`   | string | ISO-8601 upper bound on `issued_at` (optional)    |
| `issuer_id` | int    | Restrict to a single issuer (optional)            |

**Sample request**

```bash
TOKEN=<admin-or-auditor-jwt>

curl -s "http://localhost:8000/api/ledger/ar-summary" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**Sample response**

```json
{
  "generated_at": "2026-04-08T10:00:00+00:00",
  "filters": { "from_date": null, "to_date": null, "issuer_id": null },
  "totals": {
    "invoice_count": 3,
    "total_invoiced": 750.0,
    "total_outstanding": 600.0,
    "overdue_amount": 200.0,
    "overdue_count": 1
  },
  "by_status": {
    "issued":  { "count": 2, "outstanding_amount": 400.0 },
    "overdue": { "count": 1, "outstanding_amount": 200.0 }
  },
  "by_issuer": [
    {
      "issuer_id": 2, "issuer_name": "alice",
      "invoice_count": 3, "total_invoiced": 750.0,
      "total_outstanding": 600.0, "overdue_count": 1, "overdue_amount": 200.0
    }
  ]
}
```

---

### GET /api/ledger/ap-summary — Accounts Payable

Returns outstanding amounts owed **by** users as invoice payers (status: `issued` / `overdue`).

**Query parameters**

| Parameter   | Type   | Description                                       |
|-------------|--------|---------------------------------------------------|
| `from_date` | string | ISO-8601 lower bound on `issued_at` (optional)    |
| `to_date`   | string | ISO-8601 upper bound on `issued_at` (optional)    |
| `payer_id`  | int    | Restrict to a single payer (optional)             |

**Sample request**

```bash
curl -s "http://localhost:8000/api/ledger/ap-summary?payer_id=3" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**Sample response**

```json
{
  "generated_at": "2026-04-08T10:00:00+00:00",
  "filters": { "from_date": null, "to_date": null, "payer_id": 3 },
  "totals": {
    "invoice_count": 2,
    "total_owed": 400.0,
    "overdue_amount": 200.0,
    "overdue_count": 1
  },
  "by_status": {
    "issued":  { "count": 1, "amount_owed": 200.0 },
    "overdue": { "count": 1, "amount_owed": 200.0 }
  },
  "by_payer": [
    {
      "payer_id": 3, "payer_name": "bob",
      "invoice_count": 2, "total_owed": 400.0,
      "overdue_count": 1, "overdue_amount": 200.0
    }
  ]
}
```

---

### GET /api/ledger/reconciliation-summary — Reconciliation

Cross-checks every paid/refunded invoice against the immutable ledger to detect
amount mismatches. A record is **reconciled** when the payer's debit ledger entry
and the issuer's credit ledger entry each equal the original invoice amount.

**Query parameters**

| Parameter   | Type   | Description                                       |
|-------------|--------|---------------------------------------------------|
| `from_date` | string | ISO-8601 lower bound on `paid_at` (optional)      |
| `to_date`   | string | ISO-8601 upper bound on `paid_at` (optional)      |

**Sample request**

```bash
curl -s "http://localhost:8000/api/ledger/reconciliation-summary" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**Sample response**

```json
{
  "generated_at": "2026-04-08T10:00:00+00:00",
  "filters": { "from_date": null, "to_date": null },
  "totals": {
    "invoices_examined": 5,
    "total_invoiced": 1250.0,
    "total_collected": 1100.0
  },
  "reconciliation": {
    "reconciled": 5,
    "discrepant": 0,
    "unmatched":  0
  },
  "discrepancies": []
}
```

A non-empty `discrepancies` list means an invoice was marked paid but its ledger
entries are missing or have wrong amounts — this would indicate data integrity issues
requiring investigation.

---

## Ledger / Audit Immutability (DB Triggers)

In addition to application-layer INSERT-only conventions, **SQLite `BEFORE UPDATE`
and `BEFORE DELETE` triggers** are installed on `ledger_entries` and `audit_logs`.
Any attempt to modify or remove a row raises:

```
IMMUTABLE VIOLATION: UPDATE on ledger_entries is forbidden.
Ledger records are append-only; use a correcting entry instead.
```

The triggers are created with `IF NOT EXISTS` and are applied on every app startup
via `init_db()`, so they are present in both new and existing databases.

---

## Security Notes

- All passwords hashed with PBKDF2-SHA256 (600,000 iterations)
- Seeded admin uses generated local bootstrap password and forced first-login rotation
- JWTs signed with HS256, expire after 24 hours
- Session cookie uses httpOnly + SameSite=Strict and `Secure` outside localhost HTTP
- Identity documents encrypted with AES-256-GCM
- Offline payment records signed with HMAC-SHA256
- Audit log entries form a tamper-evident SHA-256 hash chain (+ DB triggers)
- Ledger entries form a tamper-evident SHA-256 hash chain (+ DB triggers)
- No external API calls — fully offline compliant
