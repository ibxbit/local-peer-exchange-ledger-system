"""Session DAL."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def create(conn, initiator_id: int, participant_id: int,
           description: str, duration_minutes: int,
           credit_amount: float, scheduled_at: str,
           idempotency_key: str = None,
           building: str = None, room: str = None,
           time_slot: str = None) -> int:
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO sessions '
        '(initiator_id, participant_id, status, description, '
        'duration_minutes, credit_amount, scheduled_at, '
        'building, room, time_slot, '
        'created_at, updated_at, idempotency_key) '
        'VALUES (?, ?, "pending", ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (initiator_id, participant_id, description,
         duration_minutes, credit_amount, scheduled_at,
         building, room, time_slot,
         now, now, idempotency_key)
    )
    return cur.lastrowid


def get_by_id(conn, session_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT s.id, s.initiator_id, s.participant_id, s.status, '
        's.description, s.duration_minutes, s.credit_amount, s.scheduled_at, '
        's.building, s.room, s.time_slot, '
        's.started_at, s.completed_at, s.cancelled_at, s.cancel_reason, '
        's.created_at, s.updated_at, s.idempotency_key, '
        'u1.username as initiator_name, u2.username as participant_name '
        'FROM sessions s '
        'JOIN users u1 ON s.initiator_id = u1.id '
        'JOIN users u2 ON s.participant_id = u2.id '
        'WHERE s.id = ?',
        (session_id,)
    ).fetchone())


def update_status(conn, session_id: int, new_status: str,
                  cancel_reason: str = None) -> None:
    now = utcnow()
    extra_fields = {'updated_at': now}
    if new_status == 'active':
        extra_fields['started_at'] = now
    elif new_status == 'completed':
        extra_fields['completed_at'] = now
    elif new_status == 'cancelled':
        extra_fields['cancelled_at'] = now
        if cancel_reason:
            extra_fields['cancel_reason'] = cancel_reason

    extra_fields['status'] = new_status
    set_clause = ', '.join(f'{k} = ?' for k in extra_fields)
    conn.execute(
        f'UPDATE sessions SET {set_clause} WHERE id = ?',
        list(extra_fields.values()) + [session_id]
    )


def list_for_user(conn, user_id: int, role: str = 'all',
                  status: str = None, limit: int = 20,
                  offset: int = 0) -> tuple[list, int]:
    query = (
        'SELECT s.*, '
        'u1.username as initiator_name, u2.username as participant_name '
        'FROM sessions s '
        'JOIN users u1 ON s.initiator_id = u1.id '
        'JOIN users u2 ON s.participant_id = u2.id '
        'WHERE 1=1'
    )
    params = []
    if role == 'initiator':
        query += ' AND s.initiator_id = ?'
        params.append(user_id)
    elif role == 'participant':
        query += ' AND s.participant_id = ?'
        params.append(user_id)
    else:
        query += ' AND (s.initiator_id = ? OR s.participant_id = ?)'
        params += [user_id, user_id]
    if status:
        query += ' AND s.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY s.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(
        conn.execute(query, params + [limit, offset]).fetchall()
    )
    for r in rows:
        r.pop('idempotency_key', None)
    return rows, total


def list_all(conn, status: str = None,
             scheduled_after: str = None, scheduled_before: str = None,
             buildings: list = None, rooms: list = None,
             time_slots: list = None,
             limit: int = 20, offset: int = 0) -> tuple[list, int]:
    """
    List all sessions with optional filters.
    scheduled_after/before, buildings, rooms, time_slots implement
    fine-grained resource scoping for restricted admin permissions.
    """
    query = (
        'SELECT s.id, s.status, s.credit_amount, s.duration_minutes, '
        's.scheduled_at, s.created_at, s.completed_at, s.cancel_reason, '
        's.building, s.room, s.time_slot, '
        'u1.username as initiator, u2.username as participant '
        'FROM sessions s '
        'JOIN users u1 ON s.initiator_id = u1.id '
        'JOIN users u2 ON s.participant_id = u2.id '
        'WHERE 1=1'
    )
    params = []
    if status:
        query += ' AND s.status = ?'
        params.append(status)
    if scheduled_after:
        query += ' AND s.scheduled_at >= ?'
        params.append(scheduled_after)
    if scheduled_before:
        query += ' AND s.scheduled_at <= ?'
        params.append(scheduled_before)
    if buildings:
        placeholders = ','.join('?' * len(buildings))
        query += f' AND s.building IN ({placeholders})'
        params.extend(buildings)
    if rooms:
        placeholders = ','.join('?' * len(rooms))
        query += f' AND s.room IN ({placeholders})'
        params.extend(rooms)
    if time_slots:
        placeholders = ','.join('?' * len(time_slots))
        query += f' AND s.time_slot IN ({placeholders})'
        params.extend(time_slots)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY s.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


def count_completed_for_user(conn, user_id: int) -> int:
    return conn.execute(
        'SELECT COUNT(*) FROM sessions '
        'WHERE (initiator_id = ? OR participant_id = ?) AND status = "completed"',
        (user_id, user_id)
    ).fetchone()[0]


def count_session_stats_for_user(conn, user_id: int) -> dict:
    """Return total, completed, and cancelled session counts for a user."""
    row = conn.execute(
        'SELECT '
        '  COUNT(*) as total, '
        '  SUM(CASE WHEN status = "completed"  THEN 1 ELSE 0 END) as completed, '
        '  SUM(CASE WHEN status = "cancelled"  THEN 1 ELSE 0 END) as cancelled '
        'FROM sessions '
        'WHERE initiator_id = ? OR participant_id = ?',
        (user_id, user_id)
    ).fetchone()
    return {'total': row[0], 'completed': row[1] or 0, 'cancelled': row[2] or 0}
