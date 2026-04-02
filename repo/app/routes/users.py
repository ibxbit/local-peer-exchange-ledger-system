"""User profile and management routes."""

import re
from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import login_required, admin_required, mask_email
from app.dal import user_dal, rating_dal, session_dal, audit_dal

users_bp = Blueprint('users', __name__)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@users_bp.route('', methods=['GET'])
@admin_required
def list_users():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    with db() as conn:
        rows, total = user_dal.list_users(
            conn,
            role=request.args.get('role'),
            search=request.args.get('search', '').strip(),
            limit=per_page, offset=(page - 1) * per_page,
        )
    for r in rows:
        r['email'] = mask_email(r['email'])
        r.pop('password_hash', None)
    return jsonify({'users': rows, 'total': total}), 200


@users_bp.route('/<int:user_id>', methods=['GET'])
@login_required
def get_user(user_id):
    if g.role != 'admin' and g.user_id != user_id:
        return jsonify({'error': 'Access denied.'}), 403
    with db() as conn:
        user = user_dal.get_by_id(conn, user_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    user['email'] = mask_email(user['email'])
    user.pop('password_hash', None)
    return jsonify({'user': user}), 200


@users_bp.route('/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    if g.role != 'admin' and g.user_id != user_id:
        return jsonify({'error': 'Access denied.'}), 403
    d = request.get_json(force=True) or {}
    updates = {}
    if 'email' in d:
        email = d['email'].strip().lower()
        if not EMAIL_RE.match(email):
            return jsonify({'error': 'Invalid email address.'}), 400
        updates['email'] = email
    if not updates:
        return jsonify({'error': 'No updatable fields provided.'}), 400
    with db() as conn:
        if 'email' in updates:
            dup = conn.execute(
                'SELECT id FROM users WHERE email = ? AND id != ?',
                (updates['email'], user_id)
            ).fetchone()
            if dup:
                return jsonify({'error': 'Email already in use.'}), 409
        user_dal.update_fields(conn, user_id, **updates)
        audit_dal.write(conn, 'USER_UPDATED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'fields': list(updates.keys())})
    return jsonify({'message': 'Profile updated.'}), 200


@users_bp.route('/<int:user_id>/role', methods=['PUT'])
@admin_required
def update_role(user_id):
    d = request.get_json(force=True) or {}
    new_role = d.get('role')
    if new_role not in ('user', 'admin', 'auditor'):
        return jsonify({'error': 'role must be user, admin, or auditor.'}), 400
    with db() as conn:
        user = user_dal.get_by_id(conn, user_id)
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        old_role = user['role']
        user_dal.update_fields(conn, user_id, role=new_role)
        audit_dal.write(conn, 'ROLE_CHANGED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'old_role': old_role, 'new_role': new_role})
    return jsonify({'message': f'Role updated to {new_role}.'}), 200


@users_bp.route('/<int:user_id>/status', methods=['PUT'])
@admin_required
def update_status(user_id):
    d = request.get_json(force=True) or {}
    is_active = d.get('is_active')
    if is_active is None:
        return jsonify({'error': 'is_active (bool) is required.'}), 400
    with db() as conn:
        user = user_dal.get_by_id(conn, user_id)
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        new_active = 1 if is_active else 0
        user_dal.update_fields(conn, user_id, is_active=new_active)
        audit_dal.write(conn, 'STATUS_CHANGED', user_id=g.user_id,
                        entity_type='user', entity_id=user_id,
                        details={'old_is_active': bool(user['is_active']),
                                 'new_is_active': bool(new_active)})
    return jsonify({'message': 'Status updated.'}), 200


@users_bp.route('/<int:user_id>/reputation', methods=['GET'])
@login_required
def get_reputation(user_id):
    with db() as conn:
        user  = user_dal.get_by_id(conn, user_id)
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        stats = rating_dal.get_stats(conn, user_id)
        sess  = session_dal.count_completed_for_user(conn, user_id)
    return jsonify({
        'user_id':         user_id,
        'username':        user['username'],
        'total_ratings':   stats['total'],
        'average_score':   round(stats['avg_score'], 2) if stats['avg_score'] else None,
        'sessions_completed': sess,
    }), 200
