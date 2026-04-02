"""
Admin blueprint — moderation, analytics, user management shortcuts.
All endpoints require admin role.

- GET /api/admin/analytics        — system-wide statistics
- GET /api/admin/users            — user list with full details
- PUT /api/admin/users/<id>/ban   — ban a user (deactivate + note)
- GET /api/admin/sessions         — all sessions overview
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import utcnow, admin_required, write_audit_log, mask_email

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/analytics', methods=['GET'])
@admin_required
def analytics():
    with db() as conn:
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        active_users = conn.execute(
            'SELECT COUNT(*) FROM users WHERE is_active = 1'
        ).fetchone()[0]
        users_by_role = rows_to_list(conn.execute(
            'SELECT role, COUNT(*) as count FROM users GROUP BY role'
        ).fetchall())

        total_sessions = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        sessions_by_status = rows_to_list(conn.execute(
            'SELECT status, COUNT(*) as count FROM sessions GROUP BY status'
        ).fetchall())

        total_ledger = conn.execute(
            'SELECT COUNT(*) as entries, SUM(amount) as volume '
            'FROM ledger_entries'
        ).fetchone()

        pending_verifications = conn.execute(
            "SELECT COUNT(*) FROM identity_verifications WHERE status = 'pending'"
        ).fetchone()[0]
        open_violations = conn.execute(
            "SELECT COUNT(*) FROM violations WHERE status = 'open'"
        ).fetchone()[0]

        avg_reputation = conn.execute(
            'SELECT AVG(avg_score) FROM ('
            '  SELECT AVG(score) as avg_score FROM ratings GROUP BY ratee_id'
            ')'
        ).fetchone()[0]

        recent_registrations = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]

    return jsonify({
        'users': {
            'total': total_users,
            'active': active_users,
            'by_role': {r['role']: r['count'] for r in users_by_role},
            'registered_last_7d': recent_registrations,
        },
        'sessions': {
            'total': total_sessions,
            'by_status': {r['status']: r['count'] for r in sessions_by_status},
        },
        'ledger': {
            'total_entries': total_ledger[0] or 0,
            'total_volume': round(total_ledger[1] or 0, 2),
        },
        'moderation': {
            'pending_verifications': pending_verifications,
            'open_violations': open_violations,
        },
        'reputation': {
            'platform_avg_rating': round(avg_reputation, 2) if avg_reputation else None,
        },
    }), 200


@admin_bp.route('/users', methods=['GET'])
@admin_required
def admin_list_users():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page
    role_filter = request.args.get('role')
    status_filter = request.args.get('status')  # 'active' | 'inactive'
    search = request.args.get('search', '').strip()

    with db() as conn:
        query = (
            'SELECT u.id, u.username, u.email, u.role, u.is_active, '
            'u.credit_balance, u.created_at, u.failed_login_attempts, '
            'u.lockout_until, '
            '(SELECT COUNT(*) FROM sessions '
            ' WHERE initiator_id = u.id OR participant_id = u.id) as session_count, '
            '(SELECT AVG(score) FROM ratings WHERE ratee_id = u.id) as avg_rating, '
            '(SELECT COUNT(*) FROM violations '
            " WHERE user_id = u.id AND status = 'open') as open_violations "
            'FROM users u WHERE 1=1'
        )
        params = []
        if role_filter:
            query += ' AND u.role = ?'
            params.append(role_filter)
        if status_filter == 'active':
            query += ' AND u.is_active = 1'
        elif status_filter == 'inactive':
            query += ' AND u.is_active = 0'
        if search:
            query += ' AND (u.username LIKE ? OR u.email LIKE ?)'
            params += [f'%{search}%', f'%{search}%']

        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
        query += ' ORDER BY u.id DESC LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    for r in rows:
        r['email'] = mask_email(r['email'])
        r['is_active'] = bool(r['is_active'])
        if r['avg_rating']:
            r['avg_rating'] = round(r['avg_rating'], 2)

    return jsonify({'users': rows, 'total': total, 'page': page, 'per_page': per_page}), 200


@admin_bp.route('/users/<int:user_id>/ban', methods=['PUT'])
@admin_required
def ban_user(user_id: int):
    data = request.get_json(force=True) or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'reason is required for banning.'}), 400

    with db() as conn:
        user = conn.execute(
            'SELECT id, username, is_active FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        if not user['is_active']:
            return jsonify({'error': 'User is already inactive.'}), 409

        conn.execute(
            'UPDATE users SET is_active = 0, updated_at = ? WHERE id = ?',
            (utcnow(), user_id)
        )
        # Auto-file a violation record for traceability
        conn.execute(
            'INSERT INTO violations '
            '(user_id, reported_by, violation_type, description, severity, '
            'status, resolution_notes, created_at, resolved_at, resolved_by) '
            'VALUES (?, ?, "admin_ban", ?, "high", "resolved", ?, ?, ?, ?)',
            (user_id, g.user_id, reason, f'Account banned by admin: {reason}',
             utcnow(), utcnow(), g.user_id)
        )
        write_audit_log(conn, 'USER_BANNED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'reason': reason})

    return jsonify({'message': f'User {user["username"]} banned.'}), 200


@admin_bp.route('/sessions', methods=['GET'])
@admin_required
def admin_sessions():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page
    status_filter = request.args.get('status')

    with db() as conn:
        query = (
            'SELECT s.id, s.status, s.credit_amount, s.duration_minutes, '
            's.created_at, s.completed_at, '
            'u1.username as initiator, u2.username as participant '
            'FROM sessions s '
            'JOIN users u1 ON s.initiator_id = u1.id '
            'JOIN users u2 ON s.participant_id = u2.id '
            'WHERE 1=1'
        )
        params = []
        if status_filter:
            query += ' AND s.status = ?'
            params.append(status_filter)

        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
        query += ' ORDER BY s.id DESC LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    return jsonify({'sessions': rows, 'total': total}), 200
