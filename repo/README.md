Project Type: fullstack

# SkillShare Platform

A peer skill-exchange platform with ledger, identity verification, admin
moderation, analytics, and offline payment support.

- **Backend**: Flask (Python 3.11), SQLite, APScheduler
- **Frontend**: Vanilla JS SPA (ES modules) + HTMX for live partials
- **Tests**: Pytest (unit + API + frontend) · Vitest (JS unit) · Playwright (E2E)

---

## Quick Start (Docker — the only supported path)

Everything runs inside Docker. **No `pip install`, no `npm install`, no manual
database setup**, and no local Python interpreter is required.

```bash
docker-compose up
```

The equivalent modern form works identically:

```bash
docker compose up
```

On first boot the application container:

1. Creates `instance/config.json` with the demo secrets.
2. Initialises `instance/app.db` (SQLite) and applies all migrations.
3. Seeds three demo accounts (see the **Demo Credentials** section below).
4. Binds Flask to `0.0.0.0:8000` inside the container, published to the host
   as `http://localhost:8001`.

When the container is up you will see `Running on http://0.0.0.0:8000` in the
logs; the app is then reachable at:

- **Web UI (SPA)**:  http://localhost:8001/
- **REST API root**: http://localhost:8001/api/

Stop it with `Ctrl-C`, or run it detached: `docker-compose up -d`.

---

## Demo Credentials

The Docker stack seeds one account per role. Credentials are **deterministic**
— they are re-applied every time the container starts, so you can log in
immediately without scraping `instance/config.json`.

| Role     | Username    | Password          | Notes                                      |
|----------|-------------|-------------------|--------------------------------------------|
| Admin    | `admin`     | `Admin@Demo123!`  | Full administrative access                 |
| Auditor  | `auditor`   | `Auditor@Demo123!`| Read-only audit + analytics + ledger views |
| User     | `demo_user` | `User@Demo123!`   | Standard user account (500 credits)        |

All three accounts are real rows in the `users` table (role column enforced),
not mocks. Password rotation is disabled in Docker, so these credentials stay
valid for the lifetime of the container.

> **Custom passwords**: override `PEX_ADMIN_BOOTSTRAP_PASSWORD`,
> `PEX_DEMO_AUDITOR_PASSWORD`, `PEX_DEMO_USER_PASSWORD` in
> `docker-compose.yml` if you need different demo credentials.

---

## Verifying the system works

### 1. Probe the API is reachable

```bash
curl -s http://localhost:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@Demo123!"}' | python -m json.tool
```

Expected response (abridged):

```json
{
  "token": "eyJ...",
  "user": { "username": "admin", "role": "admin", ... }
}
```

### 2. Confirm each role can authenticate

```bash
for role in admin auditor demo_user; do
  case $role in
    admin)     pw='Admin@Demo123!' ;;
    auditor)   pw='Auditor@Demo123!' ;;
    demo_user) pw='User@Demo123!' ;;
  esac
  echo "-- $role --"
  curl -s http://localhost:8001/api/auth/login \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$role\",\"password\":\"$pw\"}" | python -m json.tool
done
```

Each request should return a JSON body containing a `token` field and the
user's `role`.

### 3. Admin KPI dashboard

```bash
TOKEN=$(curl -s http://localhost:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@Demo123!"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:8001/api/analytics/kpis \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Expected: JSON object with `kpis.conversion_rate`, `kpis.dispute_rate`, etc.

### 4. Verify the audit-log chain

```bash
curl -s http://localhost:8001/api/audit/logs/verify \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Expected: `{"valid": true, "message": "Audit log chain is intact.", ...}`

### 5. UI smoke check

1. Open http://localhost:8001/ in a browser.
2. Log in as **`demo_user` / `User@Demo123!`**.
3. You should land on the Dashboard with the user's name in the top right.
4. Navigate to **Find Peers** — HTMX-powered peer search should render.
5. Log out; repeat with `admin` — admin-only menu items appear on the sidebar.

---

## End-to-end flow (offline payments + ledger)

The following flow exercises the core value path using only the demo
accounts that exist on a freshly started container:

```bash
# 1. Grab an admin token
ADMIN=$(curl -s http://localhost:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@Demo123!"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 2. Grab a user token
USER=$(curl -s http://localhost:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_user","password":"User@Demo123!"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 3. Submit an offline payment as the user
curl -s http://localhost:8001/api/payments/submit \
  -H "Authorization: Bearer $USER" \
  -H 'Content-Type: application/json' \
  -d '{"amount":200,"payment_type":"check","reference_number":"CHK-00001"}' \
  | python -m json.tool
# → { "payment_id": 1, "signature": "<64-char hex>" }

# 4. Admin confirms the payment (credits the user account)
curl -s -X POST http://localhost:8001/api/payments/1/confirm \
  -H "Authorization: Bearer $ADMIN" | python -m json.tool
# → { "message": "Payment confirmed and account credited.", "new_balance": ... }
```

---

## API Endpoint Summary

All REST endpoints are prefixed with `/api/`. The frontend SPA is served at
`/` (catch-all route rendering the SPA shell).

| Prefix                                            | Description                                    |
|---------------------------------------------------|------------------------------------------------|
| `POST /api/auth/register`                         | Register a new user                            |
| `POST /api/auth/login`                            | Login (sets httpOnly cookie + returns JWT)     |
| `GET  /api/auth/me`                               | Get current user profile                       |
| `POST /api/auth/logout`                           | Logout (clears httpOnly cookie)                |
| `GET  /api/users`                                 | List users (admin only)                        |
| `GET  /api/ledger/balance`                        | Check credit balance                           |
| `POST /api/ledger/credit`                         | Credit a user (admin only)                     |
| `POST /api/ledger/transfer`                       | Peer credit transfer                           |
| `POST /api/ledger/invoices`                       | Create an invoice                              |
| `POST /api/payments/submit`                       | Submit offline payment                         |
| `POST /api/payments/<id>/confirm`                 | Confirm payment and credit account (admin)     |
| `POST /api/payments/<id>/refund`                  | Refund a confirmed payment (admin)             |
| `GET  /api/ledger/ar-summary`                     | Accounts Receivable summary (admin/auditor)    |
| `GET  /api/ledger/ap-summary`                     | Accounts Payable summary (admin/auditor)       |
| `GET  /api/ledger/reconciliation-summary`         | Invoice ↔ ledger reconciliation                |
| `GET  /api/audit/logs`                            | Audit log listing (admin/auditor)              |
| `GET  /api/audit/logs/verify`                     | SHA-256 chain integrity check                  |
| `GET  /api/analytics/kpis`                        | KPI dashboard                                  |
| `GET  /api/analytics/export`                      | CSV export (kpi or daily)                      |
| `GET  /api/analytics/reports`                     | List saved daily reports                       |
| `GET  /api/analytics/reports/<date>`              | Download a specific daily report CSV           |
| `POST /api/admin/violations`                      | Report a violation                             |
| `PUT  /api/admin/users/<id>/ban`                  | Ban a user                                     |
| `PUT  /api/admin/users/<id>/unban`                | Unban a user                                   |
| `GET  /api/admin/resources`                       | List schedule inventory resources              |
| `POST /api/admin/resources`                       | Create schedule inventory resource             |
| `PUT  /api/admin/resources/<id>`                  | Update schedule inventory resource             |
| `DELETE /api/admin/resources/<id>`                | Deactivate schedule inventory resource         |
| `PUT  /api/admin/permissions/<admin_id>/<res>`    | Grant a permission on `<res>`                  |
| `DELETE /api/admin/permissions/<admin_id>/<res>`  | Revoke a permission on `<res>`                 |
| `GET  /api/matching/search`                       | Search peers by skill / tag / time slot        |
| `POST /api/matching/profile`                      | Create matching profile                        |
| `PUT  /api/matching/profile`                      | Upsert matching profile                        |
| `GET  /api/matching/profile`                      | Get matching profile                           |
| `POST /api/matching/queue`                        | Join the auto-match queue                      |
| `GET  /api/matching/queue`                        | List queue entries (scoped by role)            |
| `PUT  /api/matching/queue/<id>/cancel`            | Cancel a waiting queue entry                   |
| `POST /api/matching/sessions`                     | Request a session with a peer                  |
| `GET  /api/matching/sessions`                     | List sessions for the current user             |
| `GET  /api/matching/peers-partial`                | HTMX: peer search HTML fragment                |
| `GET  /api/matching/queue/<id>/status-partial`    | HTMX: queue status polling fragment            |
| `GET  /api/matching/sessions-partial`             | HTMX: session table HTML fragment              |
| `GET  /<path:path>`                               | SPA catch-all (renders `templates/index.html`) |

---

## Running Tests

All test suites run inside the same Docker stack — no local Python/Node
install is required.

```bash
bash run_tests.sh
```

The script invokes, in order:

1. **Unit tests** — `docker compose run --rm api pytest unit_tests/`
   (service logic, hash chaining, HMAC verification, state transitions,
    boundary conditions, matching governance).
2. **API tests** — `docker compose run --rm api pytest API_tests/`
   (every REST endpoint, RBAC enforcement, validation error bodies,
    matching governance).
3. **Frontend tests** — `docker compose run --rm api pytest frontend_tests/`
   (SPA shell validation, HTMX partial endpoints, core UI flow states).
4. **Client-side JS unit tests** — `docker compose run --rm js-unit`
   (Vitest + happy-dom; covers every major `static/js/` module:
    `api.js`, `app.js`, `auth.js`, `admin.js`, `dashboard.js`, `ledger.js`,
    `matching.js`, `verification.js`, `utils.js`).
5. **Browser E2E smoke** — `docker compose run --rm e2e`
   (Playwright: login → queue → status poll → logout).

Each suite is idempotent; a fresh temp database is used per Pytest session.

---

## Environment variables (Docker)

| Variable                         | Default           | Effect                                     |
|----------------------------------|-------------------|--------------------------------------------|
| `PEX_ADMIN_BOOTSTRAP_PASSWORD`   | `Admin@Demo123!`  | Password for the seeded `admin` account    |
| `PEX_DEMO_AUDITOR_PASSWORD`      | `Auditor@Demo123!`| Password for the seeded `auditor` account  |
| `PEX_DEMO_USER_PASSWORD`         | `User@Demo123!`   | Password for the seeded `demo_user` account|
| `PEX_SEED_DEMO_USERS`            | `1` (Docker)      | Seed auditor + demo_user on startup        |
| `PEX_FORCE_PASSWORD_ROTATION`    | `0` (Docker)      | Force admin to rotate on first login       |
| `PEX_SESSION_COOKIE_SECURE`      | `0` (Docker)      | Enable only when running behind HTTPS      |

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
├── frontend_tests/     # Pytest frontend tests (HTMX + SPA shell)
├── static/
│   ├── js/             # SPA source modules (api.js, app.js, …)
│   │   └── __tests__/  # Vitest client-side unit tests
│   └── css/            # SPA stylesheet
├── templates/          # SPA entry point (index.html)
├── e2e_tests/          # Playwright browser E2E specs
├── config.py           # Deterministic secrets + runtime config
├── run.py              # Application entry point
├── requirements.txt    # Python dependencies (used inside Docker image)
├── package.json        # Node dev-deps (Vitest + Playwright)
├── Dockerfile
├── docker-compose.yml
└── run_tests.sh        # One-command test runner (all suites, Docker-only)
```

---

## Financial Summary Endpoints (AR / AP / Reconciliation)

All three endpoints require **admin or auditor** role. Every access is
audit-logged under `AR_SUMMARY_ACCESSED`, `AP_SUMMARY_ACCESSED`, or
`RECONCILIATION_ACCESSED`.

### GET /api/ledger/ar-summary — Accounts Receivable

Returns outstanding amounts owed **to** users as invoice issuers
(status: `issued` / `overdue`).

| Parameter   | Type   | Description                                    |
|-------------|--------|------------------------------------------------|
| `from_date` | string | ISO-8601 lower bound on `issued_at` (optional) |
| `to_date`   | string | ISO-8601 upper bound on `issued_at` (optional) |
| `issuer_id` | int    | Restrict to a single issuer (optional)         |

```bash
curl -s 'http://localhost:8001/api/ledger/ar-summary' \
  -H "Authorization: Bearer $ADMIN" | python -m json.tool
```

### GET /api/ledger/ap-summary — Accounts Payable

Returns outstanding amounts owed **by** users as invoice payers
(status: `issued` / `overdue`). Accepts the same date bounds plus an
optional `payer_id` filter.

### GET /api/ledger/reconciliation-summary — Reconciliation

Cross-checks every paid/refunded invoice against the immutable ledger to
detect amount mismatches. A non-empty `discrepancies` list means an invoice
was marked paid but its ledger entries are missing or have wrong amounts —
this indicates a data integrity issue requiring investigation.

---

## Ledger / Audit Immutability (DB Triggers)

In addition to application-layer INSERT-only conventions, **SQLite
`BEFORE UPDATE` and `BEFORE DELETE` triggers** are installed on
`ledger_entries` and `audit_logs`. Any attempt to modify or remove a row
raises:

```
IMMUTABLE VIOLATION: UPDATE on ledger_entries is forbidden.
Ledger records are append-only; use a correcting entry instead.
```

The triggers are created with `IF NOT EXISTS` and applied on every app
startup via `init_db()`, so they are present in both new and existing
databases.

---

## Security Notes

- Passwords hashed with PBKDF2-SHA256 (600,000 iterations).
- Seeded admin defaults to `Admin@Demo123!` in Docker demo mode; override
  with `PEX_ADMIN_BOOTSTRAP_PASSWORD` for production.
- JWTs signed with HS256, expire after 24 hours.
- Session cookie uses `httpOnly` + `SameSite=Strict` (and `Secure` outside
  localhost HTTP).
- Identity documents encrypted with AES-256-GCM.
- Offline payment records signed with HMAC-SHA256.
- Audit log entries form a tamper-evident SHA-256 hash chain (+ DB triggers).
- Ledger entries form a tamper-evident SHA-256 hash chain (+ DB triggers).
- No external API calls — fully offline compliant.
