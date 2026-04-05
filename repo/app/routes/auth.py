"""Auth routes — register, login, logout, /me, change-password."""

from flask import Blueprint, request, jsonify, g, make_response
from app.models import db
from app.utils import login_required, mask_email
from app.services import auth_service
from app.dal import user_dal
from config import Config

auth_bp = Blueprint('auth', __name__)


def _svc_error(e):
    if isinstance(e, PermissionError):
        return jsonify({'error': str(e)}), 403
    return jsonify({'error': str(e)}), 400


@auth_bp.route('/register', methods=['POST'])
def register():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            result = auth_service.register(
                conn,
                username=d.get('username', ''),
                email=d.get('email', ''),
                password=d.get('password', ''),
            )
    except (ValueError, LookupError) as e:
        code = 409 if isinstance(e, LookupError) else 400
        return jsonify({'error': str(e)}), code
    return jsonify({'message': 'Registration successful.', **result}), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            result = auth_service.login(
                conn,
                username=d.get('username', ''),
                password=d.get('password', ''),
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 429
    except ValueError as e:
        return jsonify({'error': str(e)}), 401

    resp = make_response(jsonify(result), 200)
    # Set the JWT in an httpOnly cookie so JS cannot read it (XSS protection).
    # SameSite=Strict prevents CSRF for same-origin requests.
    # secure=False here (HTTP dev); set to True when deploying over HTTPS.
    resp.set_cookie(
        'pex_session',
        result['token'],
        httponly=True,
        samesite='Strict',
        max_age=int(Config.JWT_EXPIRY_HOURS * 3600),
        secure=False,
        path='/',
    )
    return resp


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Clear the httpOnly session cookie and invalidate client state."""
    resp = make_response(jsonify({'message': 'Logged out successfully.'}), 200)
    resp.delete_cookie('pex_session', path='/', samesite='Strict')
    return resp


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    with db() as conn:
        user = user_dal.get_by_id(conn, g.user_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    return jsonify({'user': {
        'id':             user['id'],
        'username':       user['username'],
        'email':          mask_email(user['email']),
        'role':           user['role'],
        'is_active':      bool(user['is_active']),
        'credit_balance': user['credit_balance'],
        'created_at':     user['created_at'],
    }}), 200


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            auth_service.change_password(
                conn, g.user_id,
                current_pw=d.get('current_password', ''),
                new_pw=d.get('new_password', ''),
            )
    except (ValueError, PermissionError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Password changed successfully.'}), 200
