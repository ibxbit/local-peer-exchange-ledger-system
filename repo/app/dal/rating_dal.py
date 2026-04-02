"""Ratings DAL."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def create(conn, rater_id: int, ratee_id: int,
           session_id: int, score: int, comment: str) -> int:
    cur = conn.execute(
        'INSERT INTO ratings (rater_id, ratee_id, session_id, score, comment, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (rater_id, ratee_id, session_id, score, comment, utcnow())
    )
    return cur.lastrowid


def get_by_rater_session(conn, rater_id: int, session_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM ratings WHERE rater_id = ? AND session_id = ?',
        (rater_id, session_id)
    ).fetchone())


def list_for_user(conn, ratee_id: int,
                  limit: int = 10, offset: int = 0) -> tuple[list, int]:
    total = conn.execute(
        'SELECT COUNT(*) FROM ratings WHERE ratee_id = ?', (ratee_id,)
    ).fetchone()[0]
    rows = rows_to_list(conn.execute(
        'SELECT r.id, r.score, r.comment, r.created_at, u.username as rater_name '
        'FROM ratings r JOIN users u ON r.rater_id = u.id '
        'WHERE r.ratee_id = ? ORDER BY r.id DESC LIMIT ? OFFSET ?',
        (ratee_id, limit, offset)
    ).fetchall())
    return rows, total


def get_stats(conn, ratee_id: int) -> dict:
    row = row_to_dict(conn.execute(
        'SELECT COUNT(*) as total, AVG(score) as avg_score, '
        'SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END) as positive '
        'FROM ratings WHERE ratee_id = ?',
        (ratee_id,)
    ).fetchone())
    return row
