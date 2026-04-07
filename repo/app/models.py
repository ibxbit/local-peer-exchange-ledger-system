"""
Database schema initialization and connection management.
All timestamps are stored as ISO-8601 TEXT (UTC).
Ledger and audit_log are tamper-evident via chained SHA-256 hashes.
No UPDATE/DELETE is permitted on ledger_entries or audit_logs (insert-only).
"""

import sqlite3
from contextlib import contextmanager
from config import Config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- =========================================================
-- 1. USERS & AUTH
-- =========================================================
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT    UNIQUE NOT NULL,
    email                 TEXT    UNIQUE NOT NULL,
    password_hash         TEXT    NOT NULL,
    role                  TEXT    NOT NULL DEFAULT 'user'
                              CHECK(role IN ('user', 'admin', 'auditor')),
    is_active             INTEGER NOT NULL DEFAULT 1
                              CHECK(is_active IN (0, 1)),
    credit_balance        REAL    NOT NULL DEFAULT 0.0
                              CHECK(credit_balance >= 0),
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    lockout_until         TEXT,                       -- ISO-8601 or NULL
    muted_until           TEXT,                       -- ISO-8601 or NULL (temp mute)
    must_change_password  INTEGER NOT NULL DEFAULT 0
                              CHECK(must_change_password IN (0, 1)),
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username  ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email     ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role      ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- =========================================================
-- 1b. SCHEDULE INVENTORY (building/classroom/time-slot)
-- Managed by admins; used for explicit schedule resource governance.
-- =========================================================
CREATE TABLE IF NOT EXISTS schedule_resources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    building   TEXT    NOT NULL,
    room       TEXT    NOT NULL,
    time_slot  TEXT    NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1
                       CHECK(is_active IN (0, 1)),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE(building, room, time_slot)
);

CREATE INDEX IF NOT EXISTS idx_sr_active   ON schedule_resources(is_active);
CREATE INDEX IF NOT EXISTS idx_sr_building ON schedule_resources(building);
CREATE INDEX IF NOT EXISTS idx_sr_room     ON schedule_resources(room);
CREATE INDEX IF NOT EXISTS idx_sr_slot     ON schedule_resources(time_slot);

-- =========================================================
-- 2. IDENTITY VERIFICATION
-- Sensitive document content is AES-256-GCM encrypted at rest.
-- =========================================================
CREATE TABLE IF NOT EXISTS identity_verifications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    document_type       TEXT    NOT NULL
                            CHECK(document_type IN (
                                'passport','national_id','drivers_license','utility_bill'
                            )),
    document_data_enc   TEXT    NOT NULL,             -- AES-256-GCM ciphertext (base64)
    document_fingerprint TEXT,                        -- SHA-256 hex of raw bytes (before enc)
    content_type        TEXT,                         -- validated MIME type
    file_size_bytes     INTEGER,                      -- raw file size
    status              TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','verified','rejected')),
    submitted_at        TEXT    NOT NULL,
    reviewed_at         TEXT,
    reviewer_id         INTEGER,
    notes               TEXT,
    FOREIGN KEY (user_id)     REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (reviewer_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_iv_user_id ON identity_verifications(user_id);
CREATE INDEX IF NOT EXISTS idx_iv_status  ON identity_verifications(status);

-- =========================================================
-- 3a. MATCHING PROFILES
-- =========================================================
CREATE TABLE IF NOT EXISTS matching_profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER UNIQUE NOT NULL,
    skills_offered TEXT    NOT NULL DEFAULT '[]',     -- JSON array
    skills_needed  TEXT    NOT NULL DEFAULT '[]',     -- JSON array
    availability   TEXT    NOT NULL DEFAULT '{}',     -- JSON object
    bio            TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1
                       CHECK(is_active IN (0, 1)),
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mp_user_id   ON matching_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_mp_is_active ON matching_profiles(is_active);

-- =========================================================
-- 3b. MATCHING QUEUE
-- Tracks users waiting for auto-match against a specific skill.
-- =========================================================
CREATE TABLE IF NOT EXISTS matching_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    skill       TEXT    NOT NULL,                     -- skill they are seeking
    priority    INTEGER NOT NULL DEFAULT 0,           -- higher = matched first
    status      TEXT    NOT NULL DEFAULT 'waiting'
                    CHECK(status IN ('waiting','matched','cancelled','expired')),
    matched_to  INTEGER,                              -- user_id of matched peer
    session_id  INTEGER,                              -- created session reference
    expires_at  TEXT,                                 -- ISO-8601 or NULL (no expiry)
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (matched_to) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_mq_user_id ON matching_queue(user_id);
CREATE INDEX IF NOT EXISTS idx_mq_skill   ON matching_queue(skill);
CREATE INDEX IF NOT EXISTS idx_mq_status  ON matching_queue(status);

-- =========================================================
-- 3c. BLACKLIST
-- Bidirectional blocks: user A blocks user B.
-- The system must check both directions before matching.
-- =========================================================
CREATE TABLE IF NOT EXISTS blacklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    reason     TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE(blocker_id, blocked_id),
    FOREIGN KEY (blocker_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (blocked_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bl_blocker ON blacklist(blocker_id);
CREATE INDEX IF NOT EXISTS idx_bl_blocked ON blacklist(blocked_id);

-- =========================================================
-- 4. SESSIONS (peer exchange)
-- =========================================================
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    initiator_id     INTEGER NOT NULL,
    participant_id   INTEGER NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending'
                         CHECK(status IN ('pending','active','completed','cancelled')),
    description      TEXT,
    duration_minutes INTEGER CHECK(duration_minutes > 0),
    credit_amount    REAL    NOT NULL DEFAULT 0.0 CHECK(credit_amount >= 0),
    scheduled_at     TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    cancelled_at     TEXT,
    cancel_reason    TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    idempotency_key  TEXT    UNIQUE,
    FOREIGN KEY (initiator_id)   REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (participant_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sess_initiator    ON sessions(initiator_id);
CREATE INDEX IF NOT EXISTS idx_sess_participant  ON sessions(participant_id);
CREATE INDEX IF NOT EXISTS idx_sess_status       ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sess_scheduled_at ON sessions(scheduled_at);

-- =========================================================
-- 5. RATINGS & REPUTATION
-- One rating per rater per session (enforced by UNIQUE).
-- =========================================================
CREATE TABLE IF NOT EXISTS ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rater_id   INTEGER NOT NULL,
    ratee_id   INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    score      INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    comment    TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE(rater_id, session_id),
    FOREIGN KEY (rater_id)   REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (ratee_id)   REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ratings_ratee_id   ON ratings(ratee_id);
CREATE INDEX IF NOT EXISTS idx_ratings_rater_id   ON ratings(rater_id);
CREATE INDEX IF NOT EXISTS idx_ratings_session_id ON ratings(session_id);

-- =========================================================
-- 6a. VIOLATIONS
-- =========================================================
CREATE TABLE IF NOT EXISTS violations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,                -- accused user
    reported_by      INTEGER NOT NULL,
    violation_type   TEXT    NOT NULL
                         CHECK(violation_type IN (
                             'spam','harassment','fraud','no_show','abuse','other',
                             'admin_ban'
                         )),
    description      TEXT    NOT NULL,
    severity         TEXT    NOT NULL DEFAULT 'low'
                         CHECK(severity IN ('low','medium','high')),
    status           TEXT    NOT NULL DEFAULT 'open'
                         CHECK(status IN ('open','resolved','dismissed')),
    resolution_notes TEXT,
    created_at       TEXT    NOT NULL,
    resolved_at      TEXT,
    resolved_by      INTEGER,
    FOREIGN KEY (user_id)     REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (reported_by) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (resolved_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_violations_user_id     ON violations(user_id);
CREATE INDEX IF NOT EXISTS idx_violations_reported_by ON violations(reported_by);
CREATE INDEX IF NOT EXISTS idx_violations_status      ON violations(status);

-- =========================================================
-- 6b. VIOLATION APPEALS
-- A user accused in a violation may file one appeal.
-- =========================================================
CREATE TABLE IF NOT EXISTS violation_appeals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL,
    appellant_id INTEGER NOT NULL,                    -- user filing the appeal
    reason       TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','upheld','denied')),
    reviewed_by  INTEGER,
    review_notes TEXT,
    created_at   TEXT    NOT NULL,
    reviewed_at  TEXT,
    UNIQUE(violation_id, appellant_id),               -- one appeal per user per violation
    FOREIGN KEY (violation_id)  REFERENCES violations(id) ON DELETE CASCADE,
    FOREIGN KEY (appellant_id)  REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (reviewed_by)   REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_appeals_violation_id ON violation_appeals(violation_id);
CREATE INDEX IF NOT EXISTS idx_appeals_appellant_id ON violation_appeals(appellant_id);
CREATE INDEX IF NOT EXISTS idx_appeals_status       ON violation_appeals(status);

-- =========================================================
-- 7. LEDGER (immutable — insert-only, no UPDATE/DELETE)
-- Tamper-evident: entry_hash = SHA-256(own_data || previous_hash).
-- =========================================================
CREATE TABLE IF NOT EXISTS ledger_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_hash       TEXT    UNIQUE NOT NULL,
    previous_hash    TEXT,                            -- NULL for first entry
    user_id          INTEGER NOT NULL,
    transaction_type TEXT    NOT NULL
                         CHECK(transaction_type IN (
                             'credit','debit','transfer_in','transfer_out',
                             'fee','refund'
                         )),
    amount           REAL    NOT NULL CHECK(amount > 0),
    balance_after    REAL    NOT NULL CHECK(balance_after >= 0),
    reference_id     INTEGER,
    reference_type   TEXT,
    description      TEXT,
    created_at       TEXT    NOT NULL,
    created_by       INTEGER NOT NULL,
    idempotency_key  TEXT    UNIQUE,
    FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ledger_user_id    ON ledger_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_type       ON ledger_entries(transaction_type);

-- =========================================================
-- 8. AUDIT LOGS (immutable — insert-only, no UPDATE/DELETE)
-- Tamper-evident: log_hash = SHA-256(own_data || previous_hash).
-- =========================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    log_hash      TEXT    UNIQUE NOT NULL,
    previous_hash TEXT,                               -- NULL for first entry
    user_id       INTEGER,                            -- NULL for system events
    action        TEXT    NOT NULL,
    entity_type   TEXT,
    entity_id     INTEGER,
    details       TEXT,                               -- JSON string
    ip_address    TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_user_id    ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action     ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_entity     ON audit_logs(entity_type, entity_id);

-- =========================================================
-- 9. ADMIN PERMISSIONS
-- Additive grant table for fine-grained admin resource control.
-- An admin with NO rows here is a super-admin (full access).
-- An admin with ANY rows is restricted to those explicit grants.
-- Auditors always have read-only access regardless of this table.
-- =========================================================
CREATE TABLE IF NOT EXISTS admin_permissions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,
    resource   TEXT    NOT NULL
                   CHECK(resource IN (
                       'violations','appeals','bans','mutes',
                       'verification','ledger','sessions','users'
                   )),
    can_read   INTEGER NOT NULL DEFAULT 1 CHECK(can_read  IN (0, 1)),
    can_write  INTEGER NOT NULL DEFAULT 0 CHECK(can_write IN (0, 1)),
    -- JSON scope constraints, NULL = unrestricted within this resource.
    -- sessions:   {"scheduled_after":"YYYY-MM-DD","scheduled_before":"YYYY-MM-DD"}
    -- violations: {"severity":["high","medium"]}
    scope      TEXT,
    granted_by INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    UNIQUE(admin_id, resource),
    FOREIGN KEY (admin_id)   REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ap_admin_id  ON admin_permissions(admin_id);
CREATE INDEX IF NOT EXISTS idx_ap_resource  ON admin_permissions(resource);

-- =========================================================
-- 10. DAILY REPORTS
-- One row per calendar day; CSV file generated at 2:00 AM local time.
-- kpi_snapshot stores a JSON copy of all KPIs at generation time.
-- =========================================================
CREATE TABLE IF NOT EXISTS daily_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date  TEXT    UNIQUE NOT NULL,   -- YYYY-MM-DD (the day being reported)
    file_path    TEXT    NOT NULL,          -- absolute path to CSV on disk
    generated_at TEXT    NOT NULL,
    kpi_snapshot TEXT    NOT NULL           -- JSON
);

CREATE INDEX IF NOT EXISTS idx_dr_report_date ON daily_reports(report_date);

-- =========================================================
-- 11. INVOICES
-- Immutable after issuance; corrections via refund/adjustment entries only.
-- due_date = issued_at + net_days (default 15); overdue after midnight of due_date+1.
-- =========================================================
CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT    UNIQUE NOT NULL,             -- INV-000001 format
    issuer_id      INTEGER NOT NULL,                    -- user raising the invoice
    payer_id       INTEGER NOT NULL,                    -- user who owes
    session_id     INTEGER,                             -- optional session reference
    amount         REAL    NOT NULL CHECK(amount > 0),
    amount_paid    REAL    NOT NULL DEFAULT 0.0
                       CHECK(amount_paid >= 0),
    status         TEXT    NOT NULL DEFAULT 'issued'
                       CHECK(status IN (
                           'draft','issued','paid','overdue','voided','refunded'
                       )),
    due_date       TEXT    NOT NULL,                    -- ISO-8601 date (YYYY-MM-DD UTC)
    notes          TEXT,
    issued_at      TEXT,                                -- NULL while draft
    paid_at        TEXT,
    voided_at      TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    FOREIGN KEY (issuer_id)  REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (payer_id)   REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_issuer_id ON invoices(issuer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_payer_id  ON invoices(payer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status    ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_due_date  ON invoices(due_date);

-- =========================================================
-- 12. OFFLINE PAYMENTS
-- Cash / check / ACH reference payments submitted by users and
-- confirmed by admins.  Each record carries an HMAC-SHA256
-- signature over the canonical payload so any tampering is
-- detectable without an external service.
-- Ledger integration: confirm → credit; refund → debit (reference_type='offline_payment_refund').
-- =========================================================
CREATE TABLE IF NOT EXISTS offline_payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    amount           REAL    NOT NULL CHECK(amount > 0),
    payment_type     TEXT    NOT NULL
                         CHECK(payment_type IN ('cash','check','ach')),
    reference_number TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending'
                         CHECK(status IN ('pending','confirmed','refunded','failed')),
    signature        TEXT    NOT NULL,            -- HMAC-SHA256 hex of canonical payload
    ledger_entry_id  INTEGER,                     -- set after ledger credit is posted
    refund_entry_id  INTEGER,                     -- set when refunded
    confirmed_by     INTEGER,
    confirmed_at     TEXT,
    notes            TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    FOREIGN KEY (user_id)         REFERENCES users(id)         ON DELETE RESTRICT,
    FOREIGN KEY (confirmed_by)    REFERENCES users(id)         ON DELETE RESTRICT,
    FOREIGN KEY (ledger_entry_id) REFERENCES ledger_entries(id) ON DELETE RESTRICT,
    FOREIGN KEY (refund_entry_id) REFERENCES ledger_entries(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_op_user_id    ON offline_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_op_status     ON offline_payments(status);
CREATE INDEX IF NOT EXISTS idx_op_created_at ON offline_payments(created_at);

-- =========================================================
-- 10. IDEMPOTENCY KEYS
-- Caches responses for critical mutating operations.
-- =========================================================
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key_value       TEXT    PRIMARY KEY,
    response_status INTEGER NOT NULL,
    response_body   TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    user_id         INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_idem_user_id ON idempotency_keys(user_id);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    """Context manager yielding a database connection with auto-commit/rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize schema and run any incremental migrations."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    # Incremental migrations for existing databases
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Apply additive column migrations that cannot be in CREATE TABLE IF NOT EXISTS."""
    migrations = [
        ("users", "muted_until",   "ALTER TABLE users ADD COLUMN muted_until TEXT"),
        ("users", "must_change_password",
         "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"),
        ("sessions", "cancelled_at",  "ALTER TABLE sessions ADD COLUMN cancelled_at TEXT"),
        ("sessions", "cancel_reason", "ALTER TABLE sessions ADD COLUMN cancel_reason TEXT"),
        ("identity_verifications", "document_fingerprint",
         "ALTER TABLE identity_verifications ADD COLUMN document_fingerprint TEXT"),
        ("identity_verifications", "content_type",
         "ALTER TABLE identity_verifications ADD COLUMN content_type TEXT"),
        ("identity_verifications", "file_size_bytes",
         "ALTER TABLE identity_verifications ADD COLUMN file_size_bytes INTEGER"),
        # Matching governance columns
        ("matching_queue", "retry_count",
         "ALTER TABLE matching_queue ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"),
        ("matching_queue", "last_attempt_at",
         "ALTER TABLE matching_queue ADD COLUMN last_attempt_at TEXT"),
        ("matching_queue", "cancelled_at",
         "ALTER TABLE matching_queue ADD COLUMN cancelled_at TEXT"),
        # Extended matching profile
        ("matching_profiles", "tags",
         "ALTER TABLE matching_profiles ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"),
        ("matching_profiles", "preferred_time_slots",
         "ALTER TABLE matching_profiles ADD COLUMN preferred_time_slots TEXT NOT NULL DEFAULT '[]'"),
        ("matching_profiles", "category",
         "ALTER TABLE matching_profiles ADD COLUMN category TEXT"),
        # Temporary do-not-match list support
        ("blacklist", "is_temporary",
         "ALTER TABLE blacklist ADD COLUMN is_temporary INTEGER NOT NULL DEFAULT 0"),
        ("blacklist", "expires_at",
         "ALTER TABLE blacklist ADD COLUMN expires_at TEXT"),
        # Session resource dimensions (building / room / time-slot scoping)
        ("sessions", "building",
         "ALTER TABLE sessions ADD COLUMN building TEXT"),
        ("sessions", "room",
         "ALTER TABLE sessions ADD COLUMN room TEXT"),
        ("sessions", "time_slot",
         "ALTER TABLE sessions ADD COLUMN time_slot TEXT"),
    ]
    existing_cols = {}
    for table, col, sql in migrations:
        if table not in existing_cols:
            existing_cols[table] = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        if col not in existing_cols[table]:
            conn.execute(sql)
            existing_cols[table].add(col)


def row_to_dict(row) -> dict:
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
