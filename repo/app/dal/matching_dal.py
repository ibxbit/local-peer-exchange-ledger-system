"""Matching profiles, queue, and blacklist DAL."""

import json
from datetime import datetime, timezone, timedelta
from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


# ---- Profiles -----------------------------------------------------------

def get_profile(conn, user_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM matching_profiles WHERE user_id = ?', (user_id,)
    ).fetchone())


def upsert_profile(conn, user_id: int, skills_offered: list,
                   skills_needed: list, availability: dict,
                   bio: str, is_active: bool,
                   tags: list = None,
                   preferred_time_slots: list = None,
                   category: str = None) -> None:
    now = utcnow()
    tags_json  = json.dumps(tags or [])
    slots_json = json.dumps(preferred_time_slots or [])
    existing = conn.execute(
        'SELECT id FROM matching_profiles WHERE user_id = ?', (user_id,)
    ).fetchone()
    if existing:
        conn.execute(
            'UPDATE matching_profiles SET skills_offered=?, skills_needed=?, '
            'availability=?, bio=?, is_active=?, tags=?, '
            'preferred_time_slots=?, category=?, updated_at=? WHERE user_id=?',
            (json.dumps(skills_offered), json.dumps(skills_needed),
             json.dumps(availability), bio, 1 if is_active else 0,
             tags_json, slots_json, category, now, user_id)
        )
    else:
        conn.execute(
            'INSERT INTO matching_profiles '
            '(user_id, skills_offered, skills_needed, availability, bio, '
            'is_active, tags, preferred_time_slots, category, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, json.dumps(skills_offered), json.dumps(skills_needed),
             json.dumps(availability), bio, 1 if is_active else 0,
             tags_json, slots_json, category, now, now)
        )


def search_profiles(conn, exclude_user_id: int,
                    blocked_ids: set) -> list:
    """Return all active profiles excluding the requester and blocked users."""
    rows = rows_to_list(conn.execute(
        'SELECT mp.*, u.username '
        'FROM matching_profiles mp JOIN users u ON mp.user_id = u.id '
        'WHERE mp.is_active = 1 AND mp.user_id != ? AND u.is_active = 1',
        (exclude_user_id,)
    ).fetchall())
    return [r for r in rows if r['user_id'] not in blocked_ids]


# ---- Queue --------------------------------------------------------------

def enqueue(conn, user_id: int, skill: str,
            priority: int = 0, expires_at: str = None) -> int:
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO matching_queue '
        '(user_id, skill, priority, status, expires_at, created_at, updated_at) '
        'VALUES (?, ?, ?, "waiting", ?, ?, ?)',
        (user_id, skill.lower().strip(), priority, expires_at, now, now)
    )
    return cur.lastrowid


def get_queue_entry(conn, entry_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM matching_queue WHERE id = ?', (entry_id,)
    ).fetchone())


def find_waiting_match(conn, skill: str, exclude_user_id: int,
                       blocked_ids: set) -> dict | None:
    """Find the highest-priority waiting entry for a skill."""
    rows = rows_to_list(conn.execute(
        'SELECT * FROM matching_queue '
        'WHERE skill = ? AND status = "waiting" AND user_id != ? '
        'ORDER BY priority DESC, created_at ASC',
        (skill.lower().strip(), exclude_user_id)
    ).fetchall())
    for row in rows:
        if row['user_id'] not in blocked_ids:
            return row
    return None


def update_queue_entry(conn, entry_id: int, **fields) -> None:
    fields['updated_at'] = utcnow()
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    conn.execute(
        f'UPDATE matching_queue SET {set_clause} WHERE id = ?',
        list(fields.values()) + [entry_id]
    )


def list_queue(conn, user_id: int = None, status: str = None,
               limit: int = 20, offset: int = 0) -> tuple[list, int]:
    query = ('SELECT mq.*, u.username FROM matching_queue mq '
             'JOIN users u ON mq.user_id = u.id WHERE 1=1')
    params = []
    if user_id:
        query += ' AND mq.user_id = ?'
        params.append(user_id)
    if status:
        query += ' AND mq.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY mq.priority DESC, mq.created_at ASC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


# ---- Blacklist ----------------------------------------------------------

def add_block(conn, blocker_id: int, blocked_id: int, reason: str = None,
              is_temporary: bool = False, expires_at: str = None) -> None:
    """
    Insert or replace a block entry.
    For temporary blocks set is_temporary=True and provide expires_at (ISO-8601).
    Permanent blocks (is_temporary=False) never expire regardless of expires_at.
    """
    conn.execute(
        'INSERT INTO blacklist '
        '(blocker_id, blocked_id, reason, is_temporary, expires_at, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?) '
        'ON CONFLICT(blocker_id, blocked_id) DO UPDATE SET '
        'reason=excluded.reason, is_temporary=excluded.is_temporary, '
        'expires_at=excluded.expires_at, created_at=excluded.created_at',
        (blocker_id, blocked_id, reason, 1 if is_temporary else 0,
         expires_at, utcnow())
    )


def remove_block(conn, blocker_id: int, blocked_id: int) -> None:
    conn.execute(
        'DELETE FROM blacklist WHERE blocker_id = ? AND blocked_id = ?',
        (blocker_id, blocked_id)
    )


def get_blocked_ids(conn, user_id: int) -> set:
    """
    IDs blocked by user_id or blocking user_id.
    Expired temporary blocks are silently excluded.
    """
    now = utcnow()
    rows = conn.execute(
        'SELECT blocked_id AS other_id FROM blacklist '
        'WHERE blocker_id = ? '
        '  AND (is_temporary = 0 OR expires_at IS NULL OR expires_at > ?) '
        'UNION '
        'SELECT blocker_id AS other_id FROM blacklist '
        'WHERE blocked_id = ? '
        '  AND (is_temporary = 0 OR expires_at IS NULL OR expires_at > ?)',
        (user_id, now, user_id, now)
    ).fetchall()
    return {r[0] for r in rows}


def is_blocked(conn, user_a: int, user_b: int) -> bool:
    """Check bidirectional block, excluding expired temporary blocks."""
    now = utcnow()
    row = conn.execute(
        'SELECT 1 FROM blacklist '
        'WHERE ((blocker_id = ? AND blocked_id = ?) '
        '   OR  (blocker_id = ? AND blocked_id = ?)) '
        '  AND (is_temporary = 0 OR expires_at IS NULL OR expires_at > ?)',
        (user_a, user_b, user_b, user_a, now)
    ).fetchone()
    return row is not None


# ---- Governance helpers -------------------------------------------------

def get_last_cancelled_entry(conn, user_id: int) -> dict | None:
    """Return the most recently cancelled queue entry for user_id."""
    return row_to_dict(conn.execute(
        "SELECT * FROM matching_queue WHERE user_id = ? AND status = 'cancelled' "
        "ORDER BY updated_at DESC LIMIT 1",
        (user_id,)
    ).fetchone())


def count_user_queue_entries_since(conn, user_id: int, hours: int = 1) -> int:
    """Count how many queue entries user_id has created in the last `hours` hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM matching_queue WHERE user_id = ? AND created_at >= ?",
        (user_id, cutoff)
    ).fetchone()
    return row[0]


def expire_timed_out_entries(conn, cutoff_iso: str) -> int:
    """Expire waiting entries created before cutoff_iso. Returns count updated."""
    cur = conn.execute(
        "UPDATE matching_queue SET status = 'expired', updated_at = ? "
        "WHERE status = 'waiting' AND created_at < ?",
        (utcnow(), cutoff_iso)
    )
    return cur.rowcount


def get_waiting_entries(conn) -> list:
    """Return all waiting queue entries ordered by priority then age."""
    return rows_to_list(conn.execute(
        "SELECT * FROM matching_queue WHERE status = 'waiting' "
        "ORDER BY priority DESC, created_at ASC"
    ).fetchall())


def list_blocks(conn, blocker_id: int) -> list:
    now = utcnow()
    rows = rows_to_list(conn.execute(
        'SELECT bl.*, u.username as blocked_username '
        'FROM blacklist bl JOIN users u ON bl.blocked_id = u.id '
        'WHERE bl.blocker_id = ? ORDER BY bl.id DESC',
        (blocker_id,)
    ).fetchall())
    # Annotate each entry with whether it is currently active
    for r in rows:
        if r.get('is_temporary') and r.get('expires_at'):
            r['is_active_block'] = r['expires_at'] > now
        else:
            r['is_active_block'] = True
    return rows
