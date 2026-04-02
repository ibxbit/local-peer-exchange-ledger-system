"""
Ledger blueprint — tamper-evident financial ledger.
Each entry stores entry_hash = SHA-256(own data + previous_hash),
forming an immutable, auditable chain.

- GET  /api/ledger          — own transactions (user) or all (admin/auditor)
- GET  /api/ledger/balance  — own balance
- POST /api/ledger/credit   — admin: add credits to a user
- POST /api/ledger/debit    — admin: debit credits from a user
- POST /api/ledger/transfer — user-to-user transfer (idempotent)
- GET  /api/ledger/verify   — admin/auditor: verify chain integrity
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import (
    utcnow, login_required, admin_required, auditor_or_admin_required,
    hash_ledger_entry, check_idempotency, store_idempotency,
    write_audit_log,
)

ledger_bp = Blueprint('ledger', __name__)


def _get_user_balance(conn, user_id: int) -> float:
    row = conn.execute(
        'SELECT credit_balance FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    return row['credit_balance'] if row else 0.0


def _add_ledger_entry(conn, user_id: int, transaction_type: str, amount: float,
                      balance_after: float, created_by: int,
                      description: str = None, reference_id: int = None,
                      reference_type: str = None, idempotency_key: str = None):
    now = utcnow()
    # Get previous hash
    last = conn.execute(
        'SELECT entry_hash FROM ledger_entries ORDER BY id DESC LIMIT 1'
    ).fetchone()
    previous_hash = last['entry_hash'] if last else None

    entry_data = {
        'user_id': user_id,
        'transaction_type': transaction_type,
        'amount': amount,
        'balance_after': balance_after,
        'created_at': now,
        'created_by': created_by,
        'description': description or '',
    }
    entry_hash = hash_ledger_entry(entry_data, previous_hash)

    conn.execute(
        'INSERT INTO ledger_entries '
        '(entry_hash, previous_hash, user_id, transaction_type, amount, '
        'balance_after, reference_id, reference_type, description, '
        'created_at, created_by, idempotency_key) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (entry_hash, previous_hash, user_id, transaction_type, amount,
         balance_after, reference_id, reference_type, description,
         now, created_by, idempotency_key)
    )
    return entry_hash


@ledger_bp.route('', methods=['GET'])
@login_required
def list_ledger():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page

    with db() as conn:
        if g.role in ('admin', 'auditor'):
            uid_filter = request.args.get('user_id')
            query = (
                'SELECT l.id, l.user_id, u.username, l.transaction_type, '
                'l.amount, l.balance_after, l.description, l.created_at, '
                'l.entry_hash, l.reference_type, l.reference_id '
                'FROM ledger_entries l JOIN users u ON l.user_id = u.id '
                'WHERE 1=1'
            )
            params = []
            if uid_filter:
                query += ' AND l.user_id = ?'
                params.append(uid_filter)
        else:
            query = (
                'SELECT l.id, l.user_id, l.transaction_type, '
                'l.amount, l.balance_after, l.description, l.created_at, '
                'l.reference_type, l.reference_id '
                'FROM ledger_entries l WHERE l.user_id = ?'
            )
            params = [g.user_id]

        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
        query += ' ORDER BY l.id DESC LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    return jsonify({'entries': rows, 'total': total}), 200


@ledger_bp.route('/balance', methods=['GET'])
@login_required
def get_balance():
    target_id = g.user_id
    if g.role in ('admin', 'auditor'):
        uid = request.args.get('user_id')
        if uid:
            target_id = int(uid)

    with db() as conn:
        user = conn.execute(
            'SELECT id, username, credit_balance FROM users WHERE id = ?', (target_id,)
        ).fetchone()
    if not user:
        return jsonify({'error': 'User not found.'}), 404

    return jsonify({
        'user_id': user['id'],
        'username': user['username'],
        'balance': user['credit_balance'],
    }), 200


@ledger_bp.route('/credit', methods=['POST'])
@admin_required
def credit_user():
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    amount = data.get('amount')
    description = (data.get('description') or 'Admin credit').strip()
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key')

    if not user_id:
        return jsonify({'error': 'user_id is required.'}), 400
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a positive number.'}), 400

    with db() as conn:
        exists, cached = check_idempotency(conn, idempotency_key, g.user_id)
        if exists:
            return jsonify(cached['body']), cached['status']

        user = conn.execute(
            'SELECT id, credit_balance FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404

        new_balance = round(user['credit_balance'] + amount, 4)
        conn.execute(
            'UPDATE users SET credit_balance = ?, updated_at = ? WHERE id = ?',
            (new_balance, utcnow(), user_id)
        )
        _add_ledger_entry(conn, user_id, 'credit', amount, new_balance,
                          g.user_id, description, idempotency_key=idempotency_key)
        write_audit_log(conn, 'LEDGER_CREDIT', user_id=g.user_id,
                        entity_type='ledger', entity_id=user_id,
                        details={'amount': amount, 'description': description})

        body = {'message': 'Credits added.', 'new_balance': new_balance}
        store_idempotency(conn, idempotency_key, g.user_id, 200, body)

    return jsonify(body), 200


@ledger_bp.route('/debit', methods=['POST'])
@admin_required
def debit_user():
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    amount = data.get('amount')
    description = (data.get('description') or 'Admin debit').strip()
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key')

    if not user_id:
        return jsonify({'error': 'user_id is required.'}), 400
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a positive number.'}), 400

    with db() as conn:
        exists, cached = check_idempotency(conn, idempotency_key, g.user_id)
        if exists:
            return jsonify(cached['body']), cached['status']

        user = conn.execute(
            'SELECT id, credit_balance FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        if not user:
            return jsonify({'error': 'User not found.'}), 404
        if user['credit_balance'] < amount:
            return jsonify({'error': 'Insufficient balance.'}), 422

        new_balance = round(user['credit_balance'] - amount, 4)
        conn.execute(
            'UPDATE users SET credit_balance = ?, updated_at = ? WHERE id = ?',
            (new_balance, utcnow(), user_id)
        )
        _add_ledger_entry(conn, user_id, 'debit', amount, new_balance,
                          g.user_id, description, idempotency_key=idempotency_key)
        write_audit_log(conn, 'LEDGER_DEBIT', user_id=g.user_id,
                        entity_type='ledger', entity_id=user_id,
                        details={'amount': amount})

        body = {'message': 'Credits debited.', 'new_balance': new_balance}
        store_idempotency(conn, idempotency_key, g.user_id, 200, body)

    return jsonify(body), 200


@ledger_bp.route('/transfer', methods=['POST'])
@login_required
def transfer():
    data = request.get_json(force=True) or {}
    to_user_id = data.get('to_user_id')
    amount = data.get('amount')
    description = (data.get('description') or 'Peer transfer').strip()
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key')

    if not to_user_id:
        return jsonify({'error': 'to_user_id is required.'}), 400
    if to_user_id == g.user_id:
        return jsonify({'error': 'Cannot transfer to yourself.'}), 400
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a positive number.'}), 400

    with db() as conn:
        exists, cached = check_idempotency(conn, idempotency_key, g.user_id)
        if exists:
            return jsonify(cached['body']), cached['status']

        sender = conn.execute(
            'SELECT id, credit_balance FROM users WHERE id = ?', (g.user_id,)
        ).fetchone()
        if sender['credit_balance'] < amount:
            return jsonify({'error': 'Insufficient balance.'}), 422

        recipient = conn.execute(
            'SELECT id, credit_balance FROM users WHERE id = ? AND is_active = 1',
            (to_user_id,)
        ).fetchone()
        if not recipient:
            return jsonify({'error': 'Recipient not found or inactive.'}), 404

        sender_new = round(sender['credit_balance'] - amount, 4)
        recipient_new = round(recipient['credit_balance'] + amount, 4)

        conn.execute(
            'UPDATE users SET credit_balance = ?, updated_at = ? WHERE id = ?',
            (sender_new, utcnow(), g.user_id)
        )
        conn.execute(
            'UPDATE users SET credit_balance = ?, updated_at = ? WHERE id = ?',
            (recipient_new, utcnow(), to_user_id)
        )
        _add_ledger_entry(conn, g.user_id, 'transfer_out', amount, sender_new,
                          g.user_id, f'Transfer to user #{to_user_id}: {description}',
                          idempotency_key=idempotency_key)
        _add_ledger_entry(conn, to_user_id, 'transfer_in', amount, recipient_new,
                          g.user_id, f'Transfer from user #{g.user_id}: {description}')

        write_audit_log(conn, 'LEDGER_TRANSFER', user_id=g.user_id,
                        entity_type='ledger', entity_id=to_user_id,
                        details={'amount': amount, 'to_user_id': to_user_id})

        body = {'message': 'Transfer completed.', 'new_balance': sender_new}
        store_idempotency(conn, idempotency_key, g.user_id, 200, body)

    return jsonify(body), 200


@ledger_bp.route('/verify', methods=['GET'])
@auditor_or_admin_required
def verify_chain():
    """Verify the integrity of the tamper-evident ledger chain."""
    with db() as conn:
        entries = rows_to_list(conn.execute(
            'SELECT * FROM ledger_entries ORDER BY id ASC'
        ).fetchall())

    if not entries:
        return jsonify({'valid': True, 'message': 'Ledger is empty.', 'entries': 0}), 200

    broken_at = None
    prev_hash = None
    for entry in entries:
        expected = hash_ledger_entry(entry, prev_hash)
        if expected != entry['entry_hash']:
            broken_at = entry['id']
            break
        prev_hash = entry['entry_hash']

    if broken_at:
        return jsonify({
            'valid': False,
            'message': f'Chain broken at entry id={broken_at}.',
            'entries_checked': entries.index(
                next(e for e in entries if e['id'] == broken_at)
            ),
        }), 200

    return jsonify({
        'valid': True,
        'message': 'Ledger chain is intact.',
        'entries': len(entries),
    }), 200
