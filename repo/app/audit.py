"""
Audit Log blueprint — read-only access for admin and auditor roles.
- GET /api/audit/logs        — paginated audit log
- GET /api/audit/logs/verify — verify audit log chain integrity
"""

import json
from flask import Blueprint, request, jsonify
from app.models import db, rows_to_list
from app.utils import (
    auditor_or_admin_required, hash_audit_entry,
)

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('/logs', methods=['GET'])
@auditor_or_admin_required
def list_logs():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(200, int(request.args.get('per_page', 50)))
    offset = (page - 1) * per_page
    action_filter = request.args.get('action')
    entity_type_filter = request.args.get('entity_type')
    user_id_filter = request.args.get('user_id')

    with db() as conn:
        query = (
            'SELECT a.id, a.user_id, u.username, a.action, a.entity_type, '
            'a.entity_id, a.details, a.ip_address, a.created_at, a.log_hash '
            'FROM audit_logs a LEFT JOIN users u ON a.user_id = u.id '
            'WHERE 1=1'
        )
        params = []
        if action_filter:
            query += ' AND a.action LIKE ?'
            params.append(f'%{action_filter}%')
        if entity_type_filter:
            query += ' AND a.entity_type = ?'
            params.append(entity_type_filter)
        if user_id_filter:
            query += ' AND a.user_id = ?'
            params.append(user_id_filter)

        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
        query += ' ORDER BY a.id DESC LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    # Parse details JSON for readability
    for r in rows:
        if r.get('details'):
            try:
                r['details'] = json.loads(r['details'])
            except (json.JSONDecodeError, TypeError):
                pass

    return jsonify({
        'logs': rows,
        'total': total,
        'page': page,
        'per_page': per_page,
    }), 200


@audit_bp.route('/logs/verify', methods=['GET'])
@auditor_or_admin_required
def verify_audit_chain():
    """Verify that the audit log chain has not been tampered with."""
    with db() as conn:
        entries = rows_to_list(conn.execute(
            'SELECT * FROM audit_logs ORDER BY id ASC'
        ).fetchall())

    if not entries:
        return jsonify({'valid': True, 'message': 'Audit log is empty.', 'entries': 0}), 200

    broken_at = None
    prev_hash = None
    checked = 0
    for entry in entries:
        expected = hash_audit_entry(entry, prev_hash)
        if expected != entry['log_hash']:
            broken_at = entry['id']
            break
        prev_hash = entry['log_hash']
        checked += 1

    if broken_at:
        return jsonify({
            'valid': False,
            'message': f'Audit chain broken at log entry id={broken_at}.',
            'entries_checked': checked,
        }), 200

    return jsonify({
        'valid': True,
        'message': 'Audit log chain is intact.',
        'entries': len(entries),
    }), 200
