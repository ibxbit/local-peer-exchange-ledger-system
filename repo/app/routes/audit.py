"""
Audit log routes — read-only, admin/auditor only.

All read operations are themselves audit-logged (AUDIT_LOG_ACCESSED)
so there is a complete chain of custody for who read what and when.

GET  /api/audit/logs               — paginated log listing with filters
GET  /api/audit/logs/summary       — event counts by category and action
GET  /api/audit/logs/verify        — SHA-256 chain integrity check
"""

from datetime import date, timedelta

from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import auditor_or_admin_required, hash_audit_entry
from app.dal import audit_dal

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('/logs', methods=['GET'])
@auditor_or_admin_required
def list_logs():
    """
    Query params:
        user_id      — filter by actor user ID
        action       — substring match against action name
        entity_type  — exact match on entity_type
        category     — one of: auth, permissions, financial, data_access, admin
        from_date    — YYYY-MM-DD (start of day UTC)
        to_date      — YYYY-MM-DD (end of day UTC)
        page, per_page
    """
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(200, int(request.args.get('per_page', 50)))

    category = request.args.get('category')
    if category and category not in audit_dal.CATEGORIES:
        return jsonify({
            'error': f'category must be one of: {", ".join(audit_dal.CATEGORIES)}.'
        }), 400

    raw_from = request.args.get('from_date')
    raw_to   = request.args.get('to_date')
    from_ts  = f'{raw_from}T00:00:00' if raw_from else None
    to_ts    = f'{raw_to}T23:59:59'   if raw_to   else None

    with db() as conn:
        rows, total = audit_dal.list_logs(
            conn,
            user_id=request.args.get('user_id'),
            action=request.args.get('action'),
            entity_type=request.args.get('entity_type'),
            category=category,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        # Log this read access (after the query so it doesn't appear in its own result)
        audit_dal.write(conn, 'AUDIT_LOG_ACCESSED', user_id=g.user_id,
                        entity_type='audit_logs',
                        details={
                            'filters': {k: v for k, v in {
                                'user_id':     request.args.get('user_id'),
                                'action':      request.args.get('action'),
                                'entity_type': request.args.get('entity_type'),
                                'category':    category,
                                'from_date':   raw_from,
                                'to_date':     raw_to,
                            }.items() if v is not None},
                            'result_count': len(rows),
                        })

    return jsonify({
        'logs':  rows,
        'total': total,
        'page':  page,
        'categories': list(audit_dal.CATEGORIES),
    }), 200


@audit_bp.route('/logs/summary', methods=['GET'])
@auditor_or_admin_required
def summary():
    """
    Return event counts grouped by category and action name.

    Query params:
        from_date  YYYY-MM-DD  (default: 30 days ago)
        to_date    YYYY-MM-DD  (default: today)
    """
    today    = date.today()
    raw_from = request.args.get('from_date', (today - timedelta(days=30)).isoformat())
    raw_to   = request.args.get('to_date',   today.isoformat())
    from_ts  = f'{raw_from}T00:00:00'
    to_ts    = f'{raw_to}T23:59:59'

    with db() as conn:
        data = audit_dal.summary_by_category(conn, from_ts, to_ts)
        audit_dal.write(conn, 'AUDIT_LOG_ACCESSED', user_id=g.user_id,
                        entity_type='audit_summary',
                        details={'from_date': raw_from, 'to_date': raw_to})

    return jsonify({
        'date_range':   {'from': raw_from, 'to': raw_to},
        'by_category':  data['by_category'],
        'by_action':    data['by_action'],
        'categories':   list(audit_dal.CATEGORIES),
    }), 200


@audit_bp.route('/logs/verify', methods=['GET'])
@auditor_or_admin_required
def verify_chain():
    """
    Walk the full audit log chain and verify each SHA-256 hash.
    Reports the exact entry where the chain breaks, if any.
    """
    with db() as conn:
        entries = audit_dal.get_all_ordered(conn)

    if not entries:
        return jsonify({'valid': True, 'message': 'Audit log is empty.',
                        'entries': 0}), 200

    prev_hash = None
    for i, entry in enumerate(entries):
        expected = hash_audit_entry(entry, prev_hash)
        if expected != entry['log_hash']:
            return jsonify({
                'valid':           False,
                'message':         f'Audit chain broken at log id={entry["id"]}.',
                'entries_checked': i,
            }), 200
        prev_hash = entry['log_hash']

    return jsonify({
        'valid':   True,
        'message': 'Audit log chain is intact.',
        'entries': len(entries),
    }), 200
