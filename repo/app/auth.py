"""
Authentication blueprint.
Endpoints: register, login, logout (token invalidation via client-side),
           /me (current user info).
Login enforces 5-attempt lockout for 15 minutes.
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict
from app.utils import (
    utcnow, utcnow_dt, parse_dt,
    validate_password, hash_password, verify_password,
    generate_token, login_required,
    write_audit_log, mask_email,
)
from config import Config
from datetime import timedelta

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    # --- validation ---
    if not username or not email or not password:
        return jsonify({'error': 'username, email, and password are required.'}), 400

    if len(username) < 3 or len(username) > 32:
        return jsonify({'error': 'Username must be 3–32 characters.'}), 400

    import re
    if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
        return jsonify({'error': 'Username contains invalid characters.'}), 400

    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({'error': 'Invalid email address.'}), 400

    ok, msg = validate_password(password)
    if not ok:
        return jsonify({'error': msg}), 400

    now = utcnow()
    pw_hash = hash_password(password)

    with db() as conn:
        # uniqueness check
        if conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
            return jsonify({'error': 'Username already taken.'}), 409
        if conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            return jsonify({'error': 'Email already registered.'}), 409

        cur = conn.execute(
            'INSERT INTO users (username, email, password_hash, role, '
            'is_active, credit_balance, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, 1, 0.0, ?, ?)',
            (username, email, pw_hash, 'user', now, now)
        )
        user_id = cur.lastrowid
        write_audit_log(conn, 'USER_REGISTERED', user_id=user_id,
                        entity_type='user', entity_id=user_id,
                        details={'username': username})

    return jsonify({'message': 'Registration successful.', 'user_id': user_id}), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': 'username and password are required.'}), 400

    with db() as conn:
        user = row_to_dict(conn.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone())

        if not user:
            return jsonify({'error': 'Invalid credentials.'}), 401

        # Lockout check
        if user['lockout_until']:
            lockout_dt = parse_dt(user['lockout_until'])
            if utcnow_dt() < lockout_dt:
                remaining = int((lockout_dt - utcnow_dt()).total_seconds() / 60) + 1
                return jsonify({
                    'error': f'Account locked. Try again in {remaining} minute(s).'
                }), 429

        if not user['is_active']:
            return jsonify({'error': 'Account is disabled.'}), 403

        # Verify password
        if not verify_password(password, user['password_hash']):
            new_attempts = user['failed_login_attempts'] + 1
            lockout_until = None
            if new_attempts >= Config.MAX_LOGIN_ATTEMPTS:
                lockout_until = (
                    utcnow_dt() + timedelta(minutes=Config.LOCKOUT_DURATION_MINUTES)
                ).isoformat()
            conn.execute(
                'UPDATE users SET failed_login_attempts = ?, lockout_until = ?, '
                'updated_at = ? WHERE id = ?',
                (new_attempts, lockout_until, utcnow(), user['id'])
            )
            write_audit_log(conn, 'LOGIN_FAILED', user_id=user['id'],
                            entity_type='user', entity_id=user['id'],
                            details={'attempts': new_attempts})
            remaining_attempts = Config.MAX_LOGIN_ATTEMPTS - new_attempts
            if lockout_until:
                return jsonify({'error': 'Too many failed attempts. Account locked for '
                                         f'{Config.LOCKOUT_DURATION_MINUTES} minutes.'}), 429
            return jsonify({
                'error': f'Invalid credentials. {remaining_attempts} attempt(s) remaining.'
            }), 401

        # Success: reset counter
        conn.execute(
            'UPDATE users SET failed_login_attempts = 0, lockout_until = NULL, '
            'updated_at = ? WHERE id = ?',
            (utcnow(), user['id'])
        )
        write_audit_log(conn, 'LOGIN_SUCCESS', user_id=user['id'],
                        entity_type='user', entity_id=user['id'])

    token = generate_token(user['id'], user['username'], user['role'])
    return jsonify({
        'token': token,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': mask_email(user['email']),
            'role': user['role'],
        }
    }), 200


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    with db() as conn:
        user = row_to_dict(conn.execute(
            'SELECT id, username, email, role, is_active, credit_balance, created_at '
            'FROM users WHERE id = ?', (g.user_id,)
        ).fetchone())
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    user['email'] = mask_email(user['email'])
    return jsonify({'user': user}), 200


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json(force=True) or {}
    current_pw = data.get('current_password') or ''
    new_pw = data.get('new_password') or ''

    if not current_pw or not new_pw:
        return jsonify({'error': 'current_password and new_password are required.'}), 400

    ok, msg = validate_password(new_pw)
    if not ok:
        return jsonify({'error': msg}), 400

    with db() as conn:
        user = row_to_dict(conn.execute(
            'SELECT * FROM users WHERE id = ?', (g.user_id,)
        ).fetchone())
        if not verify_password(current_pw, user['password_hash']):
            return jsonify({'error': 'Current password is incorrect.'}), 401

        conn.execute(
            'UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?',
            (hash_password(new_pw), utcnow(), g.user_id)
        )
        write_audit_log(conn, 'PASSWORD_CHANGED', user_id=g.user_id,
                        entity_type='user', entity_id=g.user_id)

    return jsonify({'message': 'Password changed successfully.'}), 200
