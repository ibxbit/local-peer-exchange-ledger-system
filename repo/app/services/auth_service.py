"""Auth service — registration, login, password change."""

import re
from datetime import timedelta

from config import Config
from app.utils import (
    utcnow, utcnow_dt, parse_dt,
    validate_password, hash_password, verify_password,
    generate_token,
)
from app.dal import user_dal, audit_dal

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_.-]{3,32}$')


def register(conn, username: str, email: str, password: str) -> dict:
    """
    Validates input, creates user, writes audit log.
    Returns {'user_id': int}.
    Raises ValueError on validation failure.
    Raises LookupError on duplicate username/email.
    """
    username = username.strip()
    email    = email.strip().lower()

    if not USERNAME_RE.match(username):
        raise ValueError('Username must be 3–32 characters (letters, digits, _ . -).')
    if not EMAIL_RE.match(email):
        raise ValueError('Invalid email address.')
    ok, msg = validate_password(password)
    if not ok:
        raise ValueError(msg)

    if user_dal.get_by_username(conn, username):
        raise LookupError('Username already taken.')
    if user_dal.get_by_email(conn, email):
        raise LookupError('Email already registered.')

    user_id = user_dal.create(conn, username, email, hash_password(password))
    audit_dal.write(conn, 'USER_REGISTERED', user_id=user_id,
                    entity_type='user', entity_id=user_id,
                    details={'username': username})
    return {'user_id': user_id}


def login(conn, username: str, password: str) -> dict:
    """
    Validates credentials, enforces lockout, returns JWT token.
    Raises PermissionError on banned/locked.
    Raises ValueError on bad credentials.
    """
    user = user_dal.get_by_username(conn, username.strip())
    if not user:
        raise ValueError('Invalid credentials.')

    # Lockout check
    if user['lockout_until']:
        lockout_dt = parse_dt(user['lockout_until'])
        if utcnow_dt() < lockout_dt:
            remaining = max(1, int((lockout_dt - utcnow_dt()).total_seconds() / 60))
            raise PermissionError(
                f'Account locked. Try again in {remaining} minute(s).'
            )

    if not user['is_active']:
        audit_dal.write(conn, 'LOGIN_ACCOUNT_INACTIVE', user_id=user['id'],
                        entity_type='user', entity_id=user['id'])
        raise PermissionError('Account is disabled.')

    if not verify_password(password, user['password_hash']):
        new_attempts = user_dal.increment_failed_attempts(conn, user['id'])
        lockout_until = None
        if new_attempts >= Config.MAX_LOGIN_ATTEMPTS:
            lockout_until = (
                utcnow_dt() + timedelta(minutes=Config.LOCKOUT_DURATION_MINUTES)
            ).isoformat()
            user_dal.update_fields(conn, user['id'], lockout_until=lockout_until)
            audit_dal.write(conn, 'LOGIN_LOCKED', user_id=user['id'],
                            entity_type='user', entity_id=user['id'],
                            details={'lockout_until': lockout_until,
                                     'attempts': new_attempts})
        else:
            audit_dal.write(conn, 'LOGIN_FAILED', user_id=user['id'],
                            entity_type='user', entity_id=user['id'],
                            details={'attempts': new_attempts,
                                     'remaining': Config.MAX_LOGIN_ATTEMPTS - new_attempts})
        if lockout_until:
            raise PermissionError(
                f'Too many failed attempts. Account locked for '
                f'{Config.LOCKOUT_DURATION_MINUTES} minutes.'
            )
        remaining = Config.MAX_LOGIN_ATTEMPTS - new_attempts
        raise ValueError(f'Invalid credentials. {remaining} attempt(s) remaining.')

    user_dal.reset_auth_state(conn, user['id'])
    audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user['id'],
                    entity_type='user', entity_id=user['id'])
    token = generate_token(user['id'], user['username'], user['role'])
    return {
        'token': token,
        'user': {
            'id':       user['id'],
            'username': user['username'],
            'role':     user['role'],
            'must_change_password': bool(user.get('must_change_password', 0)),
        },
    }


def change_password(conn, user_id: int,
                    current_pw: str, new_pw: str) -> None:
    user = user_dal.get_by_id(conn, user_id)
    if not verify_password(current_pw, user['password_hash']):
        raise ValueError('Current password is incorrect.')
    ok, msg = validate_password(new_pw)
    if not ok:
        raise ValueError(msg)
    user_dal.update_fields(conn, user_id, password_hash=hash_password(new_pw))
    if Config.FORCE_PASSWORD_ROTATION:
        user_dal.update_fields(conn, user_id, must_change_password=0)
    audit_dal.write(conn, 'PASSWORD_CHANGED', user_id=user_id,
                    entity_type='user', entity_id=user_id)
