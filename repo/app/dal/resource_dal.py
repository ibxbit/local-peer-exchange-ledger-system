"""Schedule inventory DAL (building/room/time-slot resources)."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def create(conn, building: str, room: str, time_slot: str) -> int:
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO schedule_resources '
        '(building, room, time_slot, is_active, created_at, updated_at) '
        'VALUES (?, ?, ?, 1, ?, ?)',
        (building, room, time_slot, now, now)
    )
    return cur.lastrowid


def get_by_id(conn, resource_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM schedule_resources WHERE id = ?',
        (resource_id,)
    ).fetchone())


def update(conn, resource_id: int, **fields) -> None:
    if not fields:
        return
    fields['updated_at'] = utcnow()
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    conn.execute(
        f'UPDATE schedule_resources SET {set_clause} WHERE id = ?',
        list(fields.values()) + [resource_id]
    )


def list_resources(conn, building: str = None, room: str = None,
                   time_slot: str = None, is_active: int | None = 1,
                   limit: int = 50, offset: int = 0) -> tuple[list, int]:
    query = 'SELECT * FROM schedule_resources WHERE 1=1'
    count_query = 'SELECT COUNT(*) FROM schedule_resources WHERE 1=1'
    params = []

    if building:
        query += ' AND building = ?'
        count_query += ' AND building = ?'
        params.append(building)
    if room:
        query += ' AND room = ?'
        count_query += ' AND room = ?'
        params.append(room)
    if time_slot:
        query += ' AND time_slot = ?'
        count_query += ' AND time_slot = ?'
        params.append(time_slot)
    if is_active is not None:
        query += ' AND is_active = ?'
        count_query += ' AND is_active = ?'
        params.append(is_active)

    total = conn.execute(count_query, params).fetchone()[0]
    query += ' ORDER BY building, room, time_slot LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total
