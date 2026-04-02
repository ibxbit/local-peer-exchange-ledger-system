"""
Offline payment routes.

POST /api/payments/submit          — user submits a payment claim
POST /api/payments/<id>/confirm    — admin confirms + fires callback simulation
POST /api/payments/<id>/refund     — admin refunds a confirmed payment
GET  /api/payments/                — list payments (admin: all; user: own)
GET  /api/payments/<id>            — get single payment (admin: any; user: own)
"""

from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import login_required, admin_required
from app.services import payment_service
from app.dal import payment_dal

payments_bp = Blueprint('payments', __name__)

VALID_STATUSES = ('pending', 'confirmed', 'refunded', 'failed')
VALID_TYPES    = ('cash', 'check', 'ach')


@payments_bp.route('/submit', methods=['POST'])
@login_required
def submit():
    """
    Body:
      amount          float   required
      payment_type    string  required  (cash | check | ach)
      reference_number string required
      notes           string  optional
    """
    d = request.get_json(force=True) or {}
    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number.'}), 400

    payment_type     = d.get('payment_type', '').lower()
    reference_number = d.get('reference_number', '')
    notes            = d.get('notes')

    try:
        with db() as conn:
            result = payment_service.submit_payment(
                conn, g.user_id, amount, payment_type, reference_number, notes
            )
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({
        'message':    'Payment submitted and awaiting confirmation.',
        'payment_id': result['payment_id'],
        'signature':  result['signature'],
    }), 201


@payments_bp.route('/<int:payment_id>/confirm', methods=['POST'])
@admin_required
def confirm(payment_id):
    """Confirm a pending payment and credit the user's account."""
    try:
        with db() as conn:
            result = payment_service.confirm_payment(conn, payment_id, g.user_id)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({
        'message':     'Payment confirmed and account credited.',
        'payment_id':  result['payment_id'],
        'new_balance': result['new_balance'],
    }), 200


@payments_bp.route('/<int:payment_id>/refund', methods=['POST'])
@admin_required
def refund(payment_id):
    """
    Refund a confirmed payment via a reversing ledger debit.
    Body (optional): { "reason": "..." }
    """
    d      = request.get_json(force=True, silent=True) or {}
    reason = d.get('reason')

    try:
        with db() as conn:
            result = payment_service.refund_payment(conn, payment_id, g.user_id, reason)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({
        'message':     'Payment refunded.',
        'payment_id':  result['payment_id'],
        'new_balance': result['new_balance'],
    }), 200


@payments_bp.route('/', methods=['GET'])
@login_required
def list_payments():
    """
    Query params:
      status        — filter by status (pending | confirmed | refunded | failed)
      payment_type  — filter by type (cash | check | ach)
      page, per_page
    Admins see all payments; regular users see only their own.
    """
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    status       = request.args.get('status')
    payment_type = request.args.get('payment_type')

    if status and status not in VALID_STATUSES:
        return jsonify({'error': f'status must be one of: {", ".join(VALID_STATUSES)}.'}), 400
    if payment_type and payment_type not in VALID_TYPES:
        return jsonify({'error': f'payment_type must be one of: {", ".join(VALID_TYPES)}.'}), 400

    # Non-admin users can only see their own payments
    filter_user_id = None if g.role == 'admin' else g.user_id

    with db() as conn:
        rows, total = payment_dal.list_payments(
            conn,
            user_id=filter_user_id,
            status=status,
            payment_type=payment_type,
            limit=per_page,
            offset=(page - 1) * per_page,
        )

    return jsonify({'payments': rows, 'total': total, 'page': page}), 200


@payments_bp.route('/<int:payment_id>', methods=['GET'])
@login_required
def get_payment(payment_id):
    """Admins can fetch any payment; users can only fetch their own."""
    with db() as conn:
        payment = payment_dal.get_by_id(conn, payment_id)

    if not payment:
        return jsonify({'error': 'Payment not found.'}), 404
    if g.role != 'admin' and payment['user_id'] != g.user_id:
        return jsonify({'error': 'Access denied.'}), 403

    return jsonify({'payment': payment}), 200
