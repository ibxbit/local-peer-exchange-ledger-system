"""Violations and Appeals DAL."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


# ---- Violations ---------------------------------------------------------

def create(conn, user_id: int, reported_by: int,
           violation_type: str, description: str,
           severity: str = 'low') -> int:
    cur = conn.execute(
        'INSERT INTO violations '
        '(user_id, reported_by, violation_type, description, '
        'severity, status, created_at) VALUES (?, ?, ?, ?, ?, "open", ?)',
        (user_id, reported_by, violation_type, description, severity, utcnow())
    )
    return cur.lastrowid


def get_by_id(conn, vid: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM violations WHERE id = ?', (vid,)
    ).fetchone())


def resolve(conn, vid: int, decision: str,
            resolved_by: int, notes: str) -> None:
    conn.execute(
        'UPDATE violations SET status = ?, resolution_notes = ?, '
        'resolved_at = ?, resolved_by = ? WHERE id = ?',
        (decision, notes, utcnow(), resolved_by, vid)
    )


def list_violations(conn, user_id: int = None, status: str = None,
                    limit: int = 20, offset: int = 0,
                    include_all: bool = False) -> tuple[list, int]:
    query = (
        'SELECT v.*, u1.username as target_username, '
        'u2.username as reporter_username '
        'FROM violations v '
        'JOIN users u1 ON v.user_id = u1.id '
        'JOIN users u2 ON v.reported_by = u2.id '
        'WHERE 1=1'
    )
    params = []
    if not include_all and user_id:
        query += ' AND (v.user_id = ? OR v.reported_by = ?)'
        params += [user_id, user_id]
    elif include_all and user_id:
        query += ' AND v.user_id = ?'
        params.append(user_id)
    if status:
        query += ' AND v.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY v.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


def count_open_against(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM violations WHERE user_id = ? AND status = 'open'",
        (user_id,)
    ).fetchone()[0]


def count_resolved_against(conn, user_id: int) -> int:
    """Count violations confirmed against a user (status='resolved'), used for dispute rate."""
    return conn.execute(
        "SELECT COUNT(*) FROM violations WHERE user_id = ? AND status = 'resolved'",
        (user_id,)
    ).fetchone()[0]


# ---- Appeals ------------------------------------------------------------

def create_appeal(conn, violation_id: int,
                  appellant_id: int, reason: str) -> int:
    cur = conn.execute(
        'INSERT INTO violation_appeals '
        '(violation_id, appellant_id, reason, status, created_at) '
        'VALUES (?, ?, ?, "pending", ?)',
        (violation_id, appellant_id, reason, utcnow())
    )
    return cur.lastrowid


def get_appeal(conn, violation_id: int, appellant_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM violation_appeals '
        'WHERE violation_id = ? AND appellant_id = ?',
        (violation_id, appellant_id)
    ).fetchone())


def resolve_appeal(conn, appeal_id: int, decision: str,
                   reviewed_by: int, notes: str) -> None:
    conn.execute(
        'UPDATE violation_appeals SET status = ?, reviewed_by = ?, '
        'review_notes = ?, reviewed_at = ? WHERE id = ?',
        (decision, reviewed_by, notes, utcnow(), appeal_id)
    )


def list_appeals(conn, status: str = None,
                 limit: int = 20, offset: int = 0) -> tuple[list, int]:
    query = (
        'SELECT va.*, v.violation_type, v.severity, '
        'u.username as appellant_username '
        'FROM violation_appeals va '
        'JOIN violations v ON va.violation_id = v.id '
        'JOIN users u ON va.appellant_id = u.id '
        'WHERE 1=1'
    )
    params = []
    if status:
        query += ' AND va.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY va.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total
