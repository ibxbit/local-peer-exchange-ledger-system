"""User DAL — all SQL touching the users table."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def get_by_id(conn, user_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM users WHERE id = ?', (user_id,)
    ).fetchone())


def get_by_username(conn, username: str) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM users WHERE username = ?', (username,)
    ).fetchone())


def get_by_email(conn, email: str) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM users WHERE email = ?', (email,)
    ).fetchone())


def create(conn, username: str, email: str, password_hash: str,
           role: str = 'user') -> int:
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO users (username, email, password_hash, role, '
        'is_active, credit_balance, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, 1, 0.0, ?, ?)',
        (username, email, password_hash, role, now, now)
    )
    return cur.lastrowid


def update_fields(conn, user_id: int, **fields) -> None:
    if not fields:
        return
    fields['updated_at'] = utcnow()
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    conn.execute(
        f'UPDATE users SET {set_clause} WHERE id = ?',
        list(fields.values()) + [user_id]
    )


def increment_failed_attempts(conn, user_id: int) -> int:
    conn.execute(
        'UPDATE users SET failed_login_attempts = failed_login_attempts + 1, '
        'updated_at = ? WHERE id = ?',
        (utcnow(), user_id)
    )
    return conn.execute(
        'SELECT failed_login_attempts FROM users WHERE id = ?', (user_id,)
    ).fetchone()[0]


def reset_auth_state(conn, user_id: int) -> None:
    conn.execute(
        'UPDATE users SET failed_login_attempts = 0, lockout_until = NULL, '
        'updated_at = ? WHERE id = ?',
        (utcnow(), user_id)
    )


def list_users(conn, role: str = None, is_active: int = None,
               search: str = None, limit: int = 20,
               offset: int = 0) -> tuple[list, int]:
    query  = 'SELECT * FROM users WHERE 1=1'
    cquery = 'SELECT COUNT(*) FROM users WHERE 1=1'
    params = []
    if role is not None:
        query  += ' AND role = ?'
        cquery += ' AND role = ?'
        params.append(role)
    if is_active is not None:
        query  += ' AND is_active = ?'
        cquery += ' AND is_active = ?'
        params.append(is_active)
    if search:
        query  += ' AND (username LIKE ? OR email LIKE ?)'
        cquery += ' AND (username LIKE ? OR email LIKE ?)'
        params += [f'%{search}%', f'%{search}%']
    total = conn.execute(cquery, params).fetchone()[0]
    query += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


def count_active_disputes(conn, user_id: int) -> int:
    """Number of open violations filed against this user."""
    return conn.execute(
        "SELECT COUNT(*) FROM violations WHERE user_id = ? AND status = 'open'",
        (user_id,)
    ).fetchone()[0]
