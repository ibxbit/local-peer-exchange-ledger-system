"""
Analytics DAL — read-only SQL queries for KPI computation.

All queries accept (from_ts, to_ts) ISO-8601 timestamp bounds
normalised by the service layer:
    from_ts = 'YYYY-MM-DDT00:00:00'
    to_ts   = 'YYYY-MM-DDT23:59:59'
"""

from app.models import rows_to_list


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def session_stats(conn, from_ts: str, to_ts: str) -> dict:
    """Counts and revenue for sessions created in the date range."""
    row = conn.execute(
        '''
        SELECT
            COUNT(*)                                                AS total,
            SUM(CASE WHEN status = "completed"  THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = "cancelled"  THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN status = "active"     THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN status = "pending"    THEN 1 ELSE 0 END) AS pending,
            COALESCE(SUM (CASE WHEN status = "completed"
                               THEN credit_amount ELSE 0 END), 0)  AS total_revenue,
            AVG(CASE WHEN status = "completed"
                     THEN credit_amount END)                        AS aov
        FROM sessions
        WHERE created_at >= ? AND created_at <= ?
        ''',
        (from_ts, to_ts)
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def new_user_count(conn, from_ts: str, to_ts: str) -> int:
    return conn.execute(
        'SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at <= ?',
        (from_ts, to_ts)
    ).fetchone()[0]


def repurchase_counts(conn, from_ts: str, to_ts: str) -> dict:
    """
    Count unique users with ≥1 completed session (eligible)
    and unique users with ≥2 completed sessions (repeat buyers).
    A user is counted once even if they appear as both initiator and participant.
    """
    row = conn.execute(
        '''
        SELECT
            COUNT(*)                                    AS eligible,
            SUM(CASE WHEN cnt >= 2 THEN 1 ELSE 0 END)  AS repeat_buyers
        FROM (
            SELECT user_id, SUM(cnt) AS cnt
            FROM (
                SELECT initiator_id   AS user_id, COUNT(*) AS cnt
                FROM sessions
                WHERE status = "completed"
                  AND created_at >= ? AND created_at <= ?
                GROUP BY initiator_id

                UNION ALL

                SELECT participant_id AS user_id, COUNT(*) AS cnt
                FROM sessions
                WHERE status = "completed"
                  AND created_at >= ? AND created_at <= ?
                GROUP BY participant_id
            )
            GROUP BY user_id
        )
        ''',
        (from_ts, to_ts, from_ts, to_ts)
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Violations (for dispute rate)
# ---------------------------------------------------------------------------

def violation_counts(conn, from_ts: str, to_ts: str) -> dict:
    row = conn.execute(
        '''
        SELECT
            COUNT(*)                                                    AS total,
            SUM(CASE WHEN status = "resolved"  THEN 1 ELSE 0 END)      AS resolved,
            SUM(CASE WHEN status = "open"      THEN 1 ELSE 0 END)      AS open,
            SUM(CASE WHEN status = "dismissed" THEN 1 ELSE 0 END)      AS dismissed
        FROM violations
        WHERE created_at >= ? AND created_at <= ?
        ''',
        (from_ts, to_ts)
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Daily breakdown (per-day rows for CSV export)
# ---------------------------------------------------------------------------

def daily_breakdown(conn, from_ts: str, to_ts: str) -> list:
    """One row per calendar day with session counts and revenue."""
    return rows_to_list(conn.execute(
        '''
        SELECT
            date(s.created_at)                                          AS day,
            COUNT(*)                                                    AS sessions_created,
            SUM(CASE WHEN s.status = "completed"  THEN 1 ELSE 0 END)   AS completed,
            SUM(CASE WHEN s.status = "cancelled"  THEN 1 ELSE 0 END)   AS cancelled,
            COALESCE(SUM(CASE WHEN s.status = "completed"
                              THEN s.credit_amount ELSE 0 END), 0)     AS revenue,
            COALESCE(AVG(CASE WHEN s.status = "completed"
                              THEN s.credit_amount END), 0)            AS daily_aov
        FROM sessions s
        WHERE s.created_at >= ? AND s.created_at <= ?
        GROUP BY date(s.created_at)
        ORDER BY day ASC
        ''',
        (from_ts, to_ts)
    ).fetchall())


def daily_new_users(conn, from_ts: str, to_ts: str) -> list:
    """New user count per calendar day."""
    return rows_to_list(conn.execute(
        '''
        SELECT date(created_at) AS day, COUNT(*) AS new_users
        FROM users
        WHERE created_at >= ? AND created_at <= ?
        GROUP BY date(created_at)
        ORDER BY day ASC
        ''',
        (from_ts, to_ts)
    ).fetchall())


def daily_violations(conn, from_ts: str, to_ts: str) -> list:
    """Violation count per calendar day."""
    return rows_to_list(conn.execute(
        '''
        SELECT date(created_at) AS day, COUNT(*) AS violations
        FROM violations
        WHERE created_at >= ? AND created_at <= ?
        GROUP BY date(created_at)
        ORDER BY day ASC
        ''',
        (from_ts, to_ts)
    ).fetchall())


# ---------------------------------------------------------------------------
# Saved reports
# ---------------------------------------------------------------------------

def list_reports(conn, limit: int = 30, offset: int = 0) -> tuple[list, int]:
    from app.models import rows_to_list
    total = conn.execute('SELECT COUNT(*) FROM daily_reports').fetchone()[0]
    rows = rows_to_list(conn.execute(
        'SELECT id, report_date, file_path, generated_at '
        'FROM daily_reports ORDER BY report_date DESC LIMIT ? OFFSET ?',
        (limit, offset)
    ).fetchall())
    return rows, total


def get_report(conn, report_date: str) -> dict | None:
    from app.models import row_to_dict
    return row_to_dict(conn.execute(
        'SELECT * FROM daily_reports WHERE report_date = ?', (report_date,)
    ).fetchone())


def save_report(conn, report_date: str, file_path: str,
                generated_at: str, kpi_snapshot: str) -> None:
    conn.execute(
        'INSERT OR REPLACE INTO daily_reports '
        '(report_date, file_path, generated_at, kpi_snapshot) '
        'VALUES (?, ?, ?, ?)',
        (report_date, file_path, generated_at, kpi_snapshot)
    )
