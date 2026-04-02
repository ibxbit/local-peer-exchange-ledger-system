# SkillShare Platform

A peer skill-exchange platform with ledger, identity verification, admin moderation, analytics, and offline payment support.

---

## Quick Start

```bash
docker compose up
```

That's it. No manual configuration or SQL imports required. The application auto-seeds an admin account and initialises the SQLite database on first run.

---

## Services

| Service | Address | Description |
|---------|---------|-------------|
| Application (API + UI) | http://localhost:8000 | Flask app — REST API and frontend |

All REST endpoints are prefixed with `/api/`. The frontend SPA is served at `/`.

---

## Default Admin Credentials

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `Admin@123456!` |

**Change the admin password immediately after first login.**

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
| `GET  /api/audit/logs` | Audit log listing (admin/auditor) |
| `GET  /api/audit/logs/summary` | Event counts by category |
| `GET  /api/audit/logs/verify` | SHA-256 chain integrity check |
| `GET  /api/analytics/kpis` | KPI dashboard |
| `GET  /api/analytics/export` | CSV export (kpi or daily) |
| `POST /api/admin/violations` | Report a violation |
| `PUT  /api/admin/users/<id>/ban` | Ban a user |
| `PUT  /api/admin/users/<id>/unban` | Unban a user |
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
curl -s http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@123456!"}' | python -m json.tool
```

Expected: `{"token": "...", "user": {"role": "admin", ...}}`

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
  -d '{"username":"admin","password":"Admin@123456!"}' \
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
python run.py
```

The app listens on `http://127.0.0.1:5000` by default.
Override host/port with environment variables:

```bash
PORT=8000 HOST=0.0.0.0 python run.py
```

### First-time setup

On startup the app:
1. Creates `instance/config.json` with auto-generated secrets (SECRET_KEY, ENCRYPTION_KEY, PAYMENT_SIGNING_KEY).
2. Initialises `instance/app.db` (SQLite).
3. Seeds the default admin account (`admin` / `Admin@123456!`).

No SQL imports or manual configuration required.

### Cross-platform notes

| Platform | Command |
|----------|---------|
| Linux / macOS | `python run.py` or `python3 run.py` |
| Windows (cmd) | `py -3 run.py` |
| Windows (PowerShell) | `py -3 run.py` |

---

## Running Tests

```bash
bash run_tests.sh
```

The script installs dependencies if needed, then runs:
- **Unit tests** in `unit_tests/` — service logic, hash chaining, HMAC verification, state transitions, boundary conditions, matching governance
- **API tests** in `API_tests/` — every REST endpoint, RBAC enforcement, error responses, matching governance API boundaries
- **Frontend tests** in `frontend_tests/` — SPA shell validation, HTMX partial endpoint responses, core UI flow states

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
```

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

## Security Notes

- All passwords hashed with PBKDF2-SHA256 (600,000 iterations)
- JWTs signed with HS256, expire after 24 hours
- Identity documents encrypted with AES-256-GCM
- Offline payment records signed with HMAC-SHA256
- Audit log entries form a tamper-evident SHA-256 hash chain
- Ledger entries form a tamper-evident SHA-256 hash chain
- No external API calls — fully offline compliant
