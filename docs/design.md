# Design Document

## Product Intent
The system is a local-first peer exchange and ledger platform that combines:
- peer matching and session flow,
- identity verification and moderation,
- credit/ledger traceability,
- analytics and reporting,
- strict offline-capable operations.

The functional scope is aligned with the prompt in `metadata.json`, with implementation centered on Flask APIs, a single-page frontend shell, and SQLite persistence.

## High-Level Architecture
- **Frontend:** SPA served from `templates/index.html` with static assets in `static/`
- **Backend:** Flask application factory in `app/__init__.py`
- **API Layer:** Blueprint-based route modules in `app/routes/`
- **Service Layer:** Business logic in `app/services/`
- **DAL Layer:** SQL-only data access in `app/dal/`
- **Persistence:** SQLite schema and access via `app/models.py`
- **Security Utilities:** JWT, hashing, encryption helpers in `app/utils.py`

## Domain Modules
- **Auth & RBAC:** Registration, login, JWT auth, role enforcement
- **Verification:** Real-name workflow and controlled document retrieval
- **Matching:** Profile, queueing, session lifecycle, block list controls
- **Reputation:** Ratings, score aggregation, violations, appeals
- **Ledger & Invoicing:** Credits/debits/transfers, invoice actions, integrity verification
- **Payments:** Offline payment capture/confirm/refund with signature checks
- **Audit:** Immutable event logging and hash-chain validation
- **Analytics:** KPI aggregation, CSV export, generated reports
- **Admin Operations:** User moderation, permission management, oversight endpoints

## Data and Integrity Model
- SQLite stores users, verification events, matching/session records, reputation and moderation records, payments, ledger entries, invoices, and audit logs.
- Ledger and audit modules include hash-chain integrity verification endpoints to support tamper-evident traceability.
- Security-sensitive data paths include password hashing, token-based auth, and encrypted identity document storage/access workflows.

## Runtime Behavior
- App startup initializes DB and seeds default admin account.
- Scheduler starts in non-test runtime to support background/report tasks.
- REST APIs under `/api/*` are the system contract for both UI and test automation.
- System is designed to be operable without external online dependencies (local/offline-first constraints).

## Non-Functional Priorities
- **Reliability:** deterministic API behaviors and automated tests (`unit_tests/`, `API_tests/`)
- **Security:** password policy, JWT auth, document protection, audited admin actions
- **Traceability:** immutable-style logs and hash verification endpoints
- **Operational clarity:** KPI/reporting endpoints for admin observability

## Current Implementation Notes
- Prompt mentions HTMX behavior and scheduled reporting details; current repository implements a REST-first Flask architecture with route-level support for the corresponding business capabilities.
- The implementation cleanly separates HTTP, business rules, and SQL access to support maintainability and future extension.
