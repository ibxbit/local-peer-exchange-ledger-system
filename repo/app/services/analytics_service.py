"""
Analytics service — KPI computation, CSV generation, daily report scheduling.

KPI Formulas
────────────
conversion_rate  = completed_sessions / max(1, total_sessions)
                   Range [0,1]. "What fraction of created sessions closed successfully?"

aov              = total_revenue / max(1, completed_sessions)
                   Average credit value per completed session.

repurchase_rate  = repeat_buyers / max(1, eligible_users)
                   repeat_buyers  = unique users with ≥2 completed sessions in period.
                   eligible_users = unique users with ≥1 completed session in period.
                   Range [0,1]. "What fraction of buyers came back?"

dispute_rate     = resolved_violations / max(1, completed_sessions)
                   resolved_violations = violations confirmed by admin (status='resolved').
                   Range [0,∞); capped display at 1.0. "How often do closed deals spark disputes?"
"""

import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone

from config import Config
from app.dal import analytics_dal, audit_dal
from app.utils import utcnow


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _default_range() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=30)).isoformat(), today.isoformat()


def _ts_bounds(from_date: str, to_date: str) -> tuple[str, str]:
    """Expand date strings to full-day ISO-8601 timestamps."""
    return f'{from_date}T00:00:00', f'{to_date}T23:59:59'


def _validate_date(d: str, name: str) -> str:
    try:
        date.fromisoformat(d)
        return d
    except ValueError:
        raise ValueError(f'{name} must be a valid date (YYYY-MM-DD). Got: {d!r}')


# ---------------------------------------------------------------------------
# KPI computation
# ---------------------------------------------------------------------------

def compute_kpis(conn, from_date: str, to_date: str) -> dict:
    """
    Compute all KPIs for the given date range.
    Returns a flat dict ready for JSON serialisation or CSV.
    """
    from_ts, to_ts = _ts_bounds(from_date, to_date)

    sess    = analytics_dal.session_stats(conn, from_ts, to_ts)
    repurch = analytics_dal.repurchase_counts(conn, from_ts, to_ts)
    viols   = analytics_dal.violation_counts(conn, from_ts, to_ts)
    new_usr = analytics_dal.new_user_count(conn, from_ts, to_ts)

    total_sess = sess['total'] or 0
    completed  = sess['completed'] or 0
    revenue    = round(sess['total_revenue'] or 0.0, 4)

    eligible      = repurch['eligible'] or 0
    repeat_buyers = repurch['repeat_buyers'] or 0
    resolved_v    = viols['resolved'] or 0

    # ── Formulas ──────────────────────────────────────────────────────────
    conversion_rate = round(completed / max(1, total_sess), 4)
    aov             = round(revenue   / max(1, completed),  4)
    repurchase_rate = round(repeat_buyers / max(1, eligible), 4)
    dispute_rate    = round(resolved_v    / max(1, completed), 4)
    # ──────────────────────────────────────────────────────────────────────

    return {
        'date_range':        {'from': from_date, 'to': to_date},
        'kpis': {
            'conversion_rate':  conversion_rate,
            'aov':              aov,
            'repurchase_rate':  repurchase_rate,
            'dispute_rate':     dispute_rate,
        },
        'sessions': {
            'total':      total_sess,
            'completed':  completed,
            'cancelled':  sess['cancelled'] or 0,
            'active':     sess['active']    or 0,
            'pending':    sess['pending']   or 0,
            'revenue':    revenue,
        },
        'users': {
            'new':          new_usr,
            'eligible':     eligible,
            'repeat_buyers': repeat_buyers,
        },
        'violations': {
            'total':     viols['total']     or 0,
            'resolved':  resolved_v,
            'open':      viols['open']      or 0,
            'dismissed': viols['dismissed'] or 0,
        },
    }


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------

def _kpi_summary_rows(kpis: dict) -> list[list]:
    """Flat rows for the KPI summary sheet."""
    dr = kpis['date_range']
    k  = kpis['kpis']
    s  = kpis['sessions']
    u  = kpis['users']
    v  = kpis['violations']
    return [
        ['metric', 'value', 'description'],
        ['date_range_from',  dr['from'], 'Inclusive start date (UTC)'],
        ['date_range_to',    dr['to'],   'Inclusive end date (UTC)'],
        [],
        ['# KPIs'],
        ['conversion_rate',  k['conversion_rate'],
         'Completed sessions / total sessions'],
        ['aov',              k['aov'],
         'Total revenue / completed sessions (credits)'],
        ['repurchase_rate',  k['repurchase_rate'],
         'Users with 2+ sessions / users with 1+ sessions'],
        ['dispute_rate',     k['dispute_rate'],
         'Resolved violations / completed sessions'],
        [],
        ['# Sessions'],
        ['sessions_total',      s['total'],     ''],
        ['sessions_completed',  s['completed'], ''],
        ['sessions_cancelled',  s['cancelled'], ''],
        ['sessions_active',     s['active'],    ''],
        ['sessions_pending',    s['pending'],   ''],
        ['total_revenue',       s['revenue'],   'Sum of credit_amount for completed sessions'],
        [],
        ['# Users'],
        ['new_users',       u['new'],           'Registered in period'],
        ['eligible_users',  u['eligible'],      'Had at least one completed session'],
        ['repeat_buyers',   u['repeat_buyers'], 'Had two or more completed sessions'],
        [],
        ['# Violations'],
        ['violations_total',     v['total'],     ''],
        ['violations_resolved',  v['resolved'],  ''],
        ['violations_open',      v['open'],      ''],
        ['violations_dismissed', v['dismissed'], ''],
    ]


def _daily_detail_rows(conn, from_date: str, to_date: str) -> list[list]:
    """Per-day detail rows for the daily-breakdown sheet."""
    from_ts, to_ts = _ts_bounds(from_date, to_date)

    sessions  = {r['day']: r for r in analytics_dal.daily_breakdown(conn, from_ts, to_ts)}
    new_users = {r['day']: r['new_users'] for r in analytics_dal.daily_new_users(conn, from_ts, to_ts)}
    viols     = {r['day']: r['violations'] for r in analytics_dal.daily_violations(conn, from_ts, to_ts)}

    # Build a complete day-by-day spine
    start = date.fromisoformat(from_date)
    end   = date.fromisoformat(to_date)
    rows  = [['date', 'new_users', 'sessions_created', 'sessions_completed',
               'sessions_cancelled', 'revenue', 'daily_aov', 'violations']]

    current = start
    while current <= end:
        d_str = current.isoformat()
        s = sessions.get(d_str, {})
        rows.append([
            d_str,
            new_users.get(d_str, 0),
            s.get('sessions_created', 0),
            s.get('completed', 0),
            s.get('cancelled', 0),
            round(s.get('revenue', 0.0), 4),
            round(s.get('daily_aov', 0.0), 4),
            viols.get(d_str, 0),
        ])
        current += timedelta(days=1)

    return rows


def build_kpi_csv(kpis: dict) -> str:
    """Return KPI summary as a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(_kpi_summary_rows(kpis))
    return buf.getvalue()


def build_daily_csv(conn, from_date: str, to_date: str,
                    kpis: dict | None = None) -> str:
    """Return per-day breakdown CSV, with a KPI summary appended."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Section 1 — daily breakdown
    writer.writerows(_daily_detail_rows(conn, from_date, to_date))

    if kpis:
        writer.writerow([])
        writer.writerow(['# KPI Summary'])
        writer.writerows(_kpi_summary_rows(kpis))

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Daily report (called by scheduler at 2:00 AM local time)
# ---------------------------------------------------------------------------

def generate_daily_report(conn, target_date: str | None = None) -> dict:
    """
    Generate the CSV report for `target_date` (defaults to yesterday).
    Writes the file to REPORTS_DIR, saves metadata in daily_reports table,
    and emits an audit log entry.

    Returns the report metadata dict.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    os.makedirs(Config.REPORTS_DIR, exist_ok=True)
    file_path = os.path.join(Config.REPORTS_DIR, f'{target_date}.csv')
    now = utcnow()

    kpis = compute_kpis(conn, target_date, target_date)
    csv_content = build_daily_csv(conn, target_date, target_date, kpis)

    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        f.write(csv_content)

    snapshot = json.dumps(kpis)
    analytics_dal.save_report(conn, target_date, file_path, now, snapshot)
    audit_dal.write(conn, 'DAILY_REPORT_GENERATED',
                    details={'report_date': target_date, 'file_path': file_path})

    return {'report_date': target_date, 'file_path': file_path, 'generated_at': now}
