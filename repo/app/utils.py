"""
Utility functions: AES-256-GCM encryption, SHA-256 hashing,
password validation, JWT helpers, RBAC decorators, idempotency.
"""

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone, timedelta
from functools import wraps

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str)

# ---------------------------------------------------------------------------
# AES-256-GCM encryption
# ---------------------------------------------------------------------------

def encrypt_data(plaintext: str) -> str:
    """Encrypt a string with AES-256-GCM; returns base64(nonce + ciphertext)."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(Config.ENCRYPTION_KEY)
    ct = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    return base64.b64encode(nonce + ct).decode('ascii')


def decrypt_data(token: str) -> str:
    """Decrypt a base64(nonce + ciphertext) string."""
    raw = base64.b64decode(token.encode('ascii'))
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(Config.ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ct, None).decode('utf-8')


def encrypt_bytes(data: bytes) -> str:
    """Encrypt raw bytes with AES-256-GCM; returns base64(nonce + ciphertext)."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(Config.ENCRYPTION_KEY)
    ct = aesgcm.encrypt(nonce, data, None)
    return base64.b64encode(nonce + ct).decode('ascii')


def decrypt_bytes(token: str) -> bytes:
    """Decrypt a base64(nonce + ciphertext) token back to raw bytes."""
    raw = base64.b64decode(token.encode('ascii'))
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(Config.ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ct, None)


# ---------------------------------------------------------------------------
# Document upload validation (magic-byte, no OCR, no external services)
# ---------------------------------------------------------------------------

# (header_prefix, mime_type)
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b'\xff\xd8\xff',               'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n',         'image/png'),
    (b'%PDF',                       'application/pdf'),
]

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024   # 5 MB


def validate_document_upload(data: bytes) -> tuple[bool, str, str]:
    """
    Validate a document by size and magic bytes only (no OCR, no external calls).
    Returns (ok, error_message, detected_mime_type).
    """
    if not data:
        return False, 'Document is empty.', ''
    if len(data) > MAX_DOCUMENT_BYTES:
        mb = MAX_DOCUMENT_BYTES // (1024 * 1024)
        return False, f'Document exceeds maximum size of {mb} MB.', ''
    for magic, mime in _MAGIC_SIGNATURES:
        if data[:len(magic)] == magic:
            return True, '', mime
    return False, 'Unsupported file type. Allowed: JPEG, PNG, PDF.', ''

# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------

def sha256(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes — used as document fingerprint."""
    return hashlib.sha256(data).hexdigest()


def hash_ledger_entry(entry: dict, previous_hash: str | None) -> str:
    """Deterministic hash for a ledger entry."""
    payload = {
        'previous_hash': previous_hash or '',
        'user_id': entry['user_id'],
        'transaction_type': entry['transaction_type'],
        'amount': entry['amount'],
        'balance_after': entry['balance_after'],
        'created_at': entry['created_at'],
        'created_by': entry['created_by'],
        'description': entry.get('description', ''),
    }
    return sha256(json.dumps(payload, sort_keys=True))


def hash_audit_entry(entry: dict, previous_hash: str | None) -> str:
    """Deterministic hash for an audit log entry."""
    payload = {
        'previous_hash': previous_hash or '',
        'user_id': entry.get('user_id'),
        'action': entry['action'],
        'entity_type': entry.get('entity_type'),
        'entity_id': entry.get('entity_id'),
        'created_at': entry['created_at'],
    }
    return sha256(json.dumps(payload, sort_keys=True))

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

PASSWORD_RE = re.compile(
    r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]).{12,}$'
)


def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < Config.MIN_PASSWORD_LENGTH:
        return False, f'Password must be at least {Config.MIN_PASSWORD_LENGTH} characters.'
    if not PASSWORD_RE.match(password):
        return False, ('Password must contain uppercase, lowercase, '
                       'a digit, and a special character.')
    return True, ''


def hash_password(password: str) -> str:
    return generate_password_hash(password, method='pbkdf2:sha256:600000')


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)

# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def generate_token(user_id: int, username: str, role: str) -> str:
    payload = {
        'sub': user_id,
        'username': username,
        'role': role,
        'iat': utcnow_dt(),
        'exp': utcnow_dt() + timedelta(hours=Config.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, Config.SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

# ---------------------------------------------------------------------------
# RBAC decorators
# ---------------------------------------------------------------------------

def _get_token_from_request() -> str | None:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _get_token_from_request()
        if not token:
            return jsonify({'error': 'Authentication required.'}), 401
        claims = decode_token(token)
        if not claims:
            return jsonify({'error': 'Invalid or expired token.'}), 401
        g.user_id = claims['sub']
        g.username = claims['username']
        g.role = claims['role']
        return f(*args, **kwargs)
    return decorated


def roles_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if g.role not in allowed_roles:
                return jsonify({'error': 'Insufficient permissions.'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    return roles_required('admin')(f)


def auditor_or_admin_required(f):
    return roles_required('admin', 'auditor')(f)

# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def check_idempotency(conn, key: str, user_id: int):
    """
    Returns (already_exists: bool, cached_response: dict | None).
    If already_exists, caller should return the cached response immediately.
    """
    if not key:
        return False, None
    row = conn.execute(
        'SELECT response_status, response_body FROM idempotency_keys '
        'WHERE key_value = ? AND user_id = ?',
        (key, user_id)
    ).fetchone()
    if row:
        return True, {'status': row['response_status'],
                      'body': json.loads(row['response_body'])}
    return False, None


def store_idempotency(conn, key: str, user_id: int, status: int, body: dict):
    if not key:
        return
    conn.execute(
        'INSERT OR IGNORE INTO idempotency_keys '
        '(key_value, response_status, response_body, created_at, user_id) '
        'VALUES (?, ?, ?, ?, ?)',
        (key, status, json.dumps(body), utcnow(), user_id)
    )

# ---------------------------------------------------------------------------
# Masking helpers
# ---------------------------------------------------------------------------

def mask_email(email: str) -> str:
    parts = email.split('@')
    if len(parts) != 2:
        return '***'
    local = parts[0]
    visible = local[:2] if len(local) >= 2 else local[0]
    return visible + '***@' + parts[1]


def mask_document(doc_type: str) -> str:
    return f'[{doc_type.upper()} on file]'

# ---------------------------------------------------------------------------
# Offline payment HMAC signing
# ---------------------------------------------------------------------------

def sign_payment_payload(payload: dict) -> str:
    """HMAC-SHA256 hex digest over sorted-key JSON of the canonical payload."""
    msg = json.dumps(payload, sort_keys=True).encode('utf-8')
    return hmac.new(Config.PAYMENT_SIGNING_KEY, msg, hashlib.sha256).hexdigest()


def verify_payment_signature(payload: dict, signature: str) -> bool:
    """Constant-time comparison of expected vs supplied HMAC-SHA256 signature."""
    expected = sign_payment_payload(payload)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Audit log writer
# ---------------------------------------------------------------------------

def write_audit_log(conn, action: str, user_id=None, entity_type=None,
                    entity_id=None, details: dict = None):
    from app.models import row_to_dict
    now = utcnow()
    last = conn.execute(
        'SELECT log_hash FROM audit_logs ORDER BY id DESC LIMIT 1'
    ).fetchone()
    prev_hash = last['log_hash'] if last else None

    entry = {
        'user_id': user_id,
        'action': action,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'created_at': now,
    }
    log_hash = hash_audit_entry(entry, prev_hash)
    details_str = json.dumps(details) if details else None
    ip = request.remote_addr if request else None

    conn.execute(
        'INSERT INTO audit_logs '
        '(log_hash, previous_hash, user_id, action, entity_type, entity_id, '
        ' details, ip_address, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (log_hash, prev_hash, user_id, action, entity_type,
         entity_id, details_str, ip, now)
    )
