"""
Audit Log DAL — INSERT ONLY.

Immutability guarantees:
  1. No UPDATE or DELETE is ever issued against audit_logs in this file.
  2. Every entry is chained: log_hash = SHA-256(entry_data ‖ previous_hash).
     Tampering with any row breaks every subsequent hash in the chain.
  3. The chain can be verified end-to-end via ledger_service.verify_chain()
     (same pattern) or the dedicated GET /api/audit/logs/verify route.

Linking:
  - user_id  → the actor (NULL for system events)
  - entity_type + entity_id → the resource that was affected
  - ip_address → captured from Flask request context when available
  - details   → arbitrary JSON payload stored as TEXT

Action categories (used for filtering and dashboard summaries):
  auth         — login, registration, password changes
  permissions  — role changes, bans, mutes, permission grants
  financial    — ledger credits/debits/transfers, invoices
  data_access  — CSV exports, document reads, audit log reads
  admin        — violations, appeals, sessions, user management, reports
"""

import json
from app.models import rows_to_list
from app.utils import utcnow, hash_audit_entry

try:
    from flask import request as _flask_request
    def _get_ip():
        try:
            return _flask_request.remote_addr
        except RuntimeError:
            return None
except ImportError:
    def _get_ip():
        return None

# ---------------------------------------------------------------------------
# Action → category mapping
# Exhaustive registry of every action emitted anywhere in the codebase.
# ---------------------------------------------------------------------------
ACTION_CATEGORIES: dict[str, str] = {
    # auth
    'USER_REGISTERED':              'auth',
    'LOGIN_SUCCESS':                'auth',
    'LOGIN_FAILED':                 'auth',
    'LOGIN_LOCKED':                 'auth',
    'LOGIN_ACCOUNT_INACTIVE':       'auth',
    'PASSWORD_CHANGED':             'auth',
    'TOKEN_REFRESHED':              'auth',

    # permissions
    'ROLE_CHANGED':                 'permissions',
    'STATUS_CHANGED':               'permissions',
    'PERMISSION_GRANTED':           'permissions',
    'PERMISSION_REVOKED':           'permissions',
    'USER_BANNED':                  'permissions',
    'USER_UNBANNED':                'permissions',
    'USER_MUTED':                   'permissions',
    'USER_UNMUTED':                 'permissions',

    # financial
    'LEDGER_CREDIT':                'financial',
    'LEDGER_DEBIT':                 'financial',
    'LEDGER_TRANSFER':              'financial',
    'INVOICE_ISSUED':               'financial',
    'INVOICE_PAID':                 'financial',
    'INVOICE_VOIDED':               'financial',
    'INVOICE_REFUNDED':             'financial',
    'INVOICE_ADJUSTED':             'financial',
    'INVOICES_MARKED_OVERDUE':      'financial',

    # financial (offline payments)
    'PAYMENT_SUBMITTED':            'financial',
    'PAYMENT_CONFIRMED':            'financial',
    'PAYMENT_CALLBACK_FIRED':       'financial',
    'PAYMENT_REFUNDED':             'financial',
    'PAYMENT_FAILED':               'financial',

    # data_access
    'DATA_EXPORTED':                'data_access',
    'AUDIT_LOG_ACCESSED':           'data_access',
    'VERIFICATION_LIST_ACCESSED':   'data_access',
    'VERIFICATION_DOCUMENT_ACCESSED': 'data_access',
    'VERIFICATION_FINGERPRINT_MISMATCH': 'data_access',

    # admin
    'USER_UPDATED':                 'admin',
    'VERIFICATION_SUBMITTED':       'admin',
    'VERIFICATION_VERIFIED':        'admin',
    'VERIFICATION_REJECTED':        'admin',
    'VIOLATION_REPORTED':           'admin',
    'VIOLATION_RESOLVED':           'admin',
    'VIOLATION_DISMISSED':          'admin',
    'VIOLATION_ESCALATED':          'admin',
    'APPEAL_FILED':                 'admin',
    'APPEAL_UPHELD':                'admin',
    'APPEAL_DENIED':                'admin',
    'RATING_SUBMITTED':             'admin',
    'SESSION_REQUESTED':            'admin',
    'SESSION_ACTIVE':               'admin',
    'SESSION_COMPLETED':            'admin',
    'SESSION_CANCELLED':            'admin',
    'QUEUE_JOINED':                 'admin',
    'QUEUE_CANCELLED':              'admin',
    'AUTO_MATCHED':                 'admin',
    'USER_BLOCKED':                 'admin',
    'USER_UNBLOCKED':               'admin',
    'DAILY_REPORT_GENERATED':       'admin',
}

CATEGORIES = ('auth', 'permissions', 'financial', 'data_access', 'admin')


def _category_for(action: str) -> str:
    return ACTION_CATEGORIES.get(action, 'admin')


# ---------------------------------------------------------------------------
# Write (INSERT ONLY — never UPDATE or DELETE)
# ---------------------------------------------------------------------------

def write(conn, action: str, user_id: int = None,
          entity_type: str = None, entity_id: int = None,
          details: dict = None) -> None:
    """
    Append a tamper-evident log entry.
    Never call UPDATE or DELETE on audit_logs anywhere in the codebase.
    """
    now = utcnow()
    last = conn.execute(
        'SELECT log_hash FROM audit_logs ORDER BY id DESC LIMIT 1'
    ).fetchone()
    prev_hash = last['log_hash'] if last else None

    entry = {
        'user_id':     user_id,
        'action':      action,
        'entity_type': entity_type,
        'entity_id':   entity_id,
        'created_at':  now,
    }
    log_hash    = hash_audit_entry(entry, prev_hash)
    details_str = json.dumps(details) if details else None

    conn.execute(
        'INSERT INTO audit_logs '
        '(log_hash, previous_hash, user_id, action, entity_type, '
        'entity_id, details, ip_address, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (log_hash, prev_hash, user_id, action, entity_type,
         entity_id, details_str, _get_ip(), now)
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_logs(conn, user_id: int = None, action: str = None,
              entity_type: str = None, category: str = None,
              from_ts: str = None, to_ts: str = None,
              limit: int = 50, offset: int = 0) -> tuple[list, int]:
    """
    List audit log entries with optional filters.

    category  — one of CATEGORIES; translates to an IN-list of action names.
    action    — substring match (LIKE %action%).
    from_ts / to_ts — ISO-8601 timestamp bounds.
    """
    query = (
        'SELECT a.id, a.user_id, u.username, a.action, a.entity_type, '
        'a.entity_id, a.details, a.ip_address, a.created_at, a.log_hash '
        'FROM audit_logs a LEFT JOIN users u ON a.user_id = u.id '
        'WHERE 1=1'
    )
    params = []

    if user_id:
        query += ' AND a.user_id = ?'
        params.append(int(user_id))
    if action:
        query += ' AND a.action LIKE ?'
        params.append(f'%{action}%')
    if entity_type:
        query += ' AND a.entity_type = ?'
        params.append(entity_type)
    if category:
        actions_in_cat = [a for a, c in ACTION_CATEGORIES.items() if c == category]
        if actions_in_cat:
            placeholders = ', '.join('?' * len(actions_in_cat))
            query += f' AND a.action IN ({placeholders})'
            params.extend(actions_in_cat)
        else:
            # Unknown category → return empty
            return [], 0
    if from_ts:
        query += ' AND a.created_at >= ?'
        params.append(from_ts)
    if to_ts:
        query += ' AND a.created_at <= ?'
        params.append(to_ts)

    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY a.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())

    for r in rows:
        if r.get('details'):
            try:
                r['details'] = json.loads(r['details'])
            except (json.JSONDecodeError, TypeError):
                pass
        r['category'] = _category_for(r['action'])

    return rows, total


def summary_by_category(conn, from_ts: str = None,
                        to_ts: str = None) -> dict:
    """
    Return event counts grouped by category for the given time window.
    Used by the audit dashboard.
    """
    query  = 'SELECT action, COUNT(*) as cnt FROM audit_logs WHERE 1=1'
    params = []
    if from_ts:
        query += ' AND created_at >= ?'
        params.append(from_ts)
    if to_ts:
        query += ' AND created_at <= ?'
        params.append(to_ts)
    query += ' GROUP BY action'

    rows = conn.execute(query, params).fetchall()
    totals: dict[str, int] = {c: 0 for c in CATEGORIES}
    totals['unknown'] = 0
    by_action: dict[str, int] = {}

    for row in rows:
        action = row[0]
        cnt    = row[1]
        cat    = _category_for(action)
        totals[cat] = totals.get(cat, 0) + cnt
        by_action[action] = cnt

    return {'by_category': totals, 'by_action': by_action}


def get_all_ordered(conn) -> list:
    """Return all entries ordered by id ASC (for chain verification)."""
    return rows_to_list(conn.execute(
        'SELECT * FROM audit_logs ORDER BY id ASC'
    ).fetchall())
