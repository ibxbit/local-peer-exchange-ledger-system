"""
Admin permissions DAL.

Permission resolution:
  1. role='auditor'  → read-only on every resource (table not consulted).
  2. role='admin', no rows in admin_permissions → super-admin (full access).
  3. role='admin', has rows → restricted to explicit grants.
"""

import json
from app.models import row_to_dict, rows_to_list
from app.utils import utcnow

VALID_RESOURCES = frozenset((
    'violations', 'appeals', 'bans', 'mutes',
    'verification', 'ledger', 'sessions', 'users',
))


def get_permission(conn, admin_id: int, resource: str) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM admin_permissions WHERE admin_id = ? AND resource = ?',
        (admin_id, resource)
    ).fetchone())


def has_any_permission(conn, admin_id: int) -> bool:
    """True when the admin has at least one explicit grant (i.e. is restricted)."""
    return conn.execute(
        'SELECT 1 FROM admin_permissions WHERE admin_id = ? LIMIT 1',
        (admin_id,)
    ).fetchone() is not None


def list_for_admin(conn, admin_id: int) -> list:
    rows = rows_to_list(conn.execute(
        'SELECT * FROM admin_permissions WHERE admin_id = ? ORDER BY resource',
        (admin_id,)
    ).fetchall())
    for r in rows:
        if r.get('scope'):
            try:
                r['scope'] = json.loads(r['scope'])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


def upsert_permission(conn, admin_id: int, resource: str,
                      can_read: bool, can_write: bool,
                      scope: dict | None, granted_by: int) -> None:
    scope_str = json.dumps(scope) if scope else None
    conn.execute(
        'INSERT INTO admin_permissions '
        '(admin_id, resource, can_read, can_write, scope, granted_by, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT(admin_id, resource) DO UPDATE SET '
        'can_read=excluded.can_read, can_write=excluded.can_write, '
        'scope=excluded.scope, granted_by=excluded.granted_by, '
        'created_at=excluded.created_at',
        (admin_id, resource, int(can_read), int(can_write),
         scope_str, granted_by, utcnow())
    )


def delete_permission(conn, admin_id: int, resource: str) -> bool:
    """Returns True if a row was deleted."""
    cur = conn.execute(
        'DELETE FROM admin_permissions WHERE admin_id = ? AND resource = ?',
        (admin_id, resource)
    )
    return cur.rowcount > 0
