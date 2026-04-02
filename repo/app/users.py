"""
Users blueprint.
- GET  /api/users            — admin: list all users (masked)
- GET  /api/users/<id>       — own profile or admin
- PUT  /api/users/<id>       — update own profile
- PUT  /api/users/<id>/role  — admin: change role
- PUT  /api/users/<id>/status — admin: activate / deactivate
- GET  /api/users/<id>/reputation — public reputation summary
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import (
    utcnow, login_required, admin_required,
    write_audit_log, mask_email,
)

users_bp = Blueprint('users', __name__)


def _safe_user(user: dict, full: bool = False) -> dict:
    """Return a user dict with sensitive fields masked."""
    out = {
        'id': user['id'],
        'username': user['username'],
        'role': user['role'],
        'is_active': bool(user['is_active']),
        'credit_balance': user['credit_balance'],
        'created_at': user['created_at'],
    }
    if full:
        out['email'] = mask_email(user['email'])
        out['updated_at'] = user['updated_at']
    return out


@users_bp.route('', methods=['GET'])
@admin_required
def list_users():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page
    role_filter = request.args.get('role')
    search = request.args.get('search', '').strip()

    with db() as conn:
        query = 'SELECT * FROM users WHERE 1=1'
        params = []
        if role_filter:
            query += ' AND role = ?'
            params.append(role_filter)
        if search:
            query += ' AND (username LIKE ? OR email LIKE ?)'
            params += [f'%{search}%', f'%{search}%']
        total = conn.execute(
            f'SELECT COUNT(*) FROM ({query})', params
        ).fetchone()[0]
        query += ' ORDER BY id LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    users = [_safe_user(r, full=True) for r in rows]
    return jsonify({'users': users, 'total': total, 'page': page, 'per_page': per_page}), 200


@users_bp.route('/<int:user_id>', methods=['GET'])
@login_required
def get_user(user_id: int):
    # Users can only view their own profile; admin can view any
    if g.role != 'admin' and g.user_id != user_id:
        return jsonify({'error': 'Access denied.'}), 403

    with db() as conn:
        user = row_to_dict(conn.execute(
            'SELECT * FROM users WHERE id = ?', (user_id,)
        ).fetchone())

    if not user:
        return jsonify({'error': 'User not found.'}), 404

    return jsonify({'user': _safe_user(user, full=True)}), 200


@users_bp.route('/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id: int):
    if g.role != 'admin' and g.user_id != user_id:
        return jsonify({'error': 'Access denied.'}), 403

    data = request.get_json(force=True) or {}
    allowed_fields = {'email', 'bio'}  # only these can be self-updated
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({'error': 'No updatable fields provided.'}), 400

    import re
    if 'email' in updates:
        email = updates['email'].strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            return jsonify({'error': 'Invalid email address.'}), 400
        updates['email'] = email

    set_clause = ', '.join(f'{k} = ?' for k in updates)
    values = list(updates.values()) + [utcnow(), user_id]

    with db() as conn:
        if 'email' in updates:
            dup = conn.execute(
                'SELECT id FROM users WHERE email = ? AND id != ?',
                (updates['email'], user_id)
            ).fetchone()
            if dup:
                return jsonify({'error': 'Email already in use.'}), 409
        conn.execute(
            f'UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?', values
        )
        write_audit_log(conn, 'USER_UPDATED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'fields': list(updates.keys())})

    return jsonify({'message': 'Profile updated.'}), 200


@users_bp.route('/<int:user_id>/role', methods=['PUT'])
@admin_required
def update_role(user_id: int):
    data = request.get_json(force=True) or {}
    new_role = data.get('role')
    if new_role not in ('user', 'admin', 'auditor'):
        return jsonify({'error': 'role must be one of: user, admin, auditor.'}), 400

    with db() as conn:
        user = conn.execute('SELECT id, role FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        old_role = user['role']
        conn.execute(
            'UPDATE users SET role = ?, updated_at = ? WHERE id = ?',
            (new_role, utcnow(), user_id)
        )
        write_audit_log(conn, 'ROLE_CHANGED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'old_role': old_role, 'new_role': new_role})

    return jsonify({'message': f'Role updated to {new_role}.'}), 200


@users_bp.route('/<int:user_id>/status', methods=['PUT'])
@admin_required
def update_status(user_id: int):
    data = request.get_json(force=True) or {}
    is_active = data.get('is_active')
    if is_active is None:
        return jsonify({'error': 'is_active (bool) is required.'}), 400

    with db() as conn:
        user = conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        conn.execute(
            'UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?',
            (1 if is_active else 0, utcnow(), user_id)
        )
        action = 'USER_ACTIVATED' if is_active else 'USER_DEACTIVATED'
        write_audit_log(conn, action, user_id=g.user_id,
                        entity_type='user', entity_id=user_id)

    status_str = 'activated' if is_active else 'deactivated'
    return jsonify({'message': f'User {status_str}.'}), 200


@users_bp.route('/<int:user_id>/reputation', methods=['GET'])
@login_required
def get_reputation(user_id: int):
    with db() as conn:
        user = conn.execute(
            'SELECT id, username FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404

        stats = conn.execute(
            'SELECT COUNT(*) as total, AVG(score) as avg_score '
            'FROM ratings WHERE ratee_id = ?',
            (user_id,)
        ).fetchone()
        sessions_completed = conn.execute(
            'SELECT COUNT(*) FROM sessions '
            'WHERE (initiator_id = ? OR participant_id = ?) AND status = "completed"',
            (user_id, user_id)
        ).fetchone()[0]

    return jsonify({
        'user_id': user_id,
        'username': user['username'],
        'total_ratings': stats['total'],
        'average_score': round(stats['avg_score'], 2) if stats['avg_score'] else None,
        'sessions_completed': sessions_completed,
    }), 200
