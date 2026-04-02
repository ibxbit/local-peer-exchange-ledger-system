"""
Analytics routes.

GET  /api/analytics/kpis              — KPI dashboard (JSON)
GET  /api/analytics/export            — CSV export (kpi or daily breakdown)
GET  /api/analytics/reports           — list saved daily reports
GET  /api/analytics/reports/<date>    — download a saved daily report CSV
POST /api/analytics/reports/generate  — manually trigger daily report generation
"""

import os
from datetime import date, timedelta

from flask import Blueprint, request, jsonify, Response, g
from app.models import db
from app.utils import auditor_or_admin_required, admin_required
from app.services import analytics_service
from app.dal import analytics_dal, audit_dal

analytics_bp = Blueprint('analytics', __name__)


def _parse_range() -> tuple[str | None, str | None, str | None]:
    """
    Parse and validate from_date / to_date query params.
    Returns (from_date, to_date, error_message).
    Defaults: from_date = 30 days ago, to_date = today.
    """
    today    = date.today()
    raw_from = request.args.get('from_date', (today - timedelta(days=30)).isoformat())
    raw_to   = request.args.get('to_date',   today.isoformat())
    try:
        analytics_service._validate_date(raw_from, 'from_date')
        analytics_service._validate_date(raw_to,   'to_date')
    except ValueError as e:
        return None, None, str(e)
    if raw_from > raw_to:
        return None, None, 'from_date must not be after to_date.'
    return raw_from, raw_to, None


# ---------------------------------------------------------------------------
# KPI dashboard
# ---------------------------------------------------------------------------

@analytics_bp.route('/kpis', methods=['GET'])
@auditor_or_admin_required
def kpis():
    """
    Returns all four KPIs plus supporting counts for the requested date range.

    Query params:
        from_date  YYYY-MM-DD  (default: 30 days ago)
        to_date    YYYY-MM-DD  (default: today)
    """
    from_date, to_date, err = _parse_range()
    if err:
        return jsonify({'error': err}), 400

    with db() as conn:
        data = analytics_service.compute_kpis(conn, from_date, to_date)
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@analytics_bp.route('/export', methods=['GET'])
@auditor_or_admin_required
def export_csv():
    """
    Export analytics data as CSV.

    Query params:
        from_date  YYYY-MM-DD
        to_date    YYYY-MM-DD
        type       'kpi'   — KPI summary only (default)
                   'daily' — per-day breakdown with appended KPI summary
    """
    from_date, to_date, err = _parse_range()
    if err:
        return jsonify({'error': err}), 400

    export_type = request.args.get('type', 'kpi').lower()
    if export_type not in ('kpi', 'daily'):
        return jsonify({'error': "type must be 'kpi' or 'daily'."}), 400

    filename = f'analytics_{from_date}_{to_date}_{export_type}.csv'

    with db() as conn:
        kpis_data = analytics_service.compute_kpis(conn, from_date, to_date)
        if export_type == 'kpi':
            csv_content = analytics_service.build_kpi_csv(kpis_data)
        else:
            csv_content = analytics_service.build_daily_csv(
                conn, from_date, to_date, kpis_data
            )
        audit_dal.write(conn, 'DATA_EXPORTED', user_id=g.user_id,
                        entity_type='analytics',
                        details={'type': export_type, 'from_date': from_date,
                                 'to_date': to_date, 'filename': filename})

    return Response(
        csv_content,
        status=200,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Saved daily reports
# ---------------------------------------------------------------------------

@analytics_bp.route('/reports', methods=['GET'])
@auditor_or_admin_required
def list_reports():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(90, int(request.args.get('per_page', 30)))
    with db() as conn:
        rows, total = analytics_dal.list_reports(
            conn, limit=per_page, offset=(page - 1) * per_page
        )
    return jsonify({'reports': rows, 'total': total, 'page': page}), 200


@analytics_bp.route('/reports/generate', methods=['POST'])
@admin_required
def trigger_report():
    """
    Manually trigger daily report generation.
    Body (optional): { "date": "YYYY-MM-DD" } — defaults to yesterday.
    """
    d = request.get_json(force=True) or {}
    target_date = d.get('date')
    if target_date:
        try:
            analytics_service._validate_date(target_date, 'date')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

    with db() as conn:
        result = analytics_service.generate_daily_report(conn, target_date)

    return jsonify({'message': 'Report generated.', **result}), 201


@analytics_bp.route('/reports/<report_date>', methods=['GET'])
@auditor_or_admin_required
def get_report(report_date):
    """
    Download the CSV for a previously generated daily report.
    Pass the date string (YYYY-MM-DD) or 'latest'.
    """
    with db() as conn:
        if report_date == 'latest':
            row_list, _ = analytics_dal.list_reports(conn, limit=1)
            rec = row_list[0] if row_list else None
        else:
            rec = analytics_dal.get_report(conn, report_date)

    if not rec:
        return jsonify({'error': 'Report not found.'}), 404

    file_path = rec['file_path']
    if not os.path.isfile(file_path):
        return jsonify({'error': 'Report file missing from disk.'}), 500

    with open(file_path, 'r', encoding='utf-8') as f:
        csv_content = f.read()

    return Response(
        csv_content,
        status=200,
        mimetype='text/csv',
        headers={
            'Content-Disposition':
                f'attachment; filename="{os.path.basename(file_path)}"'
        },
    )
