"""Ledger routes — balance, credit/debit (admin), transfer, invoices, chain verify,
AR/AP summaries, and reconciliation."""

import re
from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import (login_required, admin_required,
                        auditor_or_admin_required,
                        check_idempotency, store_idempotency)
from app.services import ledger_service, financial_summary_service
from app.dal import ledger_dal, user_dal, invoice_dal

ledger_bp = Blueprint('ledger', __name__)

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _parse_date_param(value, param_name):
    """
    Validate an optional YYYY-MM-DD query param.
    Returns (value_or_None, error_tuple_or_None).
    error_tuple is (json_response, 400) when the value is present but malformed.
    """
    if not value:
        return None, None
    if not _DATE_RE.match(value):
        return None, (
            jsonify({'error': f"'{param_name}' must be a date in YYYY-MM-DD format."}),
            400,
        )
    return value, None


@ledger_bp.route('', methods=['GET'])
@login_required
def list_ledger():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    privileged = g.role in ('admin', 'auditor')
    uid_filter = int(request.args.get('user_id', g.user_id)) \
                 if privileged else g.user_id
    with db() as conn:
        rows, total = ledger_dal.list_entries(
            conn,
            user_id=uid_filter if not (privileged and not request.args.get('user_id')) else None,
            limit=per_page, offset=(page - 1) * per_page,
            privileged=privileged,
        )
    return jsonify({'entries': rows, 'total': total}), 200


@ledger_bp.route('/balance', methods=['GET'])
@login_required
def get_balance():
    target_id = g.user_id
    if g.role in ('admin', 'auditor') and request.args.get('user_id'):
        target_id = int(request.args.get('user_id'))
    with db() as conn:
        user = user_dal.get_by_id(conn, target_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    return jsonify({
        'user_id':  user['id'],
        'username': user['username'],
        'balance':  user['credit_balance'],
    }), 200


@ledger_bp.route('/credit', methods=['POST'])
@admin_required
def credit_user():
    d    = request.get_json(force=True) or {}
    ikey = request.headers.get('Idempotency-Key') or d.get('idempotency_key')
    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number.'}), 400
    with db() as conn:
        if ikey:
            exists, cached = check_idempotency(conn, ikey, g.user_id)
            if exists:
                return jsonify(cached['body']), cached['status']
        try:
            new_balance = ledger_service.credit(
                conn, int(d.get('user_id', 0)), amount,
                (d.get('description') or 'Admin credit').strip(),
                g.user_id, ikey,
            )
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
        body = {'message': 'Credits added.', 'new_balance': new_balance}
        if ikey:
            store_idempotency(conn, ikey, g.user_id, 200, body)
    return jsonify(body), 200


@ledger_bp.route('/debit', methods=['POST'])
@admin_required
def debit_user():
    d    = request.get_json(force=True) or {}
    ikey = request.headers.get('Idempotency-Key') or d.get('idempotency_key')
    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number.'}), 400
    with db() as conn:
        if ikey:
            exists, cached = check_idempotency(conn, ikey, g.user_id)
            if exists:
                return jsonify(cached['body']), cached['status']
        try:
            new_balance = ledger_service.debit(
                conn, int(d.get('user_id', 0)), amount,
                (d.get('description') or 'Admin debit').strip(),
                g.user_id, ikey,
            )
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
        body = {'message': 'Credits debited.', 'new_balance': new_balance}
        if ikey:
            store_idempotency(conn, ikey, g.user_id, 200, body)
    return jsonify(body), 200


@ledger_bp.route('/transfer', methods=['POST'])
@login_required
def transfer():
    d    = request.get_json(force=True) or {}
    ikey = request.headers.get('Idempotency-Key') or d.get('idempotency_key')
    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number.'}), 400
    with db() as conn:
        if ikey:
            exists, cached = check_idempotency(conn, ikey, g.user_id)
            if exists:
                return jsonify(cached['body']), cached['status']
        try:
            new_balance = ledger_service.transfer(
                conn, g.user_id,
                recipient_id=int(d.get('to_user_id', 0)),
                amount=amount,
                description=(d.get('description') or 'Peer transfer').strip(),
                idempotency_key=ikey,
            )
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
        body = {'message': 'Transfer completed.', 'new_balance': new_balance}
        if ikey:
            store_idempotency(conn, ikey, g.user_id, 200, body)
    return jsonify(body), 200


@ledger_bp.route('/verify', methods=['GET'])
@auditor_or_admin_required
def verify_chain():
    with db() as conn:
        result = ledger_service.verify_chain(conn)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

@ledger_bp.route('/invoices', methods=['POST'])
@login_required
def create_invoice():
    d = request.get_json(force=True) or {}
    try:
        amount   = float(d.get('amount', 0))
        due_days = int(d.get('due_days', 15))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number; due_days must be an integer.'}), 400
    try:
        with db() as conn:
            invoice = ledger_service.create_invoice(
                conn,
                issuer_id=g.user_id,
                payer_id=int(d.get('payer_id', 0)),
                amount=amount,
                notes=(d.get('notes') or '').strip() or None,
                session_id=d.get('session_id'),
                due_days=due_days,
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(invoice), 201


@ledger_bp.route('/invoices', methods=['GET'])
@login_required
def list_invoices():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    role     = request.args.get('role', 'any')   # issuer | payer | any
    status   = request.args.get('status')
    # Admins/auditors may filter by arbitrary user
    if g.role in ('admin', 'auditor') and request.args.get('user_id'):
        uid = int(request.args.get('user_id'))
    else:
        uid = g.user_id
    with db() as conn:
        rows, total = invoice_dal.list_invoices(
            conn, user_id=uid, role=role, status=status,
            limit=per_page, offset=(page - 1) * per_page,
        )
    return jsonify({'invoices': rows, 'total': total}), 200


@ledger_bp.route('/invoices/<int:invoice_id>', methods=['GET'])
@login_required
def get_invoice(invoice_id):
    with db() as conn:
        invoice = invoice_dal.get_by_id(conn, invoice_id)
    if not invoice:
        return jsonify({'error': 'Invoice not found.'}), 404
    if g.role not in ('admin', 'auditor') and \
            g.user_id not in (invoice['issuer_id'], invoice['payer_id']):
        return jsonify({'error': 'Access denied.'}), 403
    return jsonify(invoice), 200


@ledger_bp.route('/invoices/<int:invoice_id>/pay', methods=['POST'])
@login_required
def pay_invoice(invoice_id):
    ikey = request.headers.get('Idempotency-Key')
    try:
        with db() as conn:
            if ikey:
                exists, cached = check_idempotency(conn, ikey, g.user_id)
                if exists:
                    return jsonify(cached['body']), cached['status']
            result = ledger_service.pay_invoice(conn, g.user_id,
                                                invoice_id, ikey)
            if ikey:
                store_idempotency(conn, ikey, g.user_id, 200, result)
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(result), 200


@ledger_bp.route('/invoices/<int:invoice_id>/void', methods=['POST'])
@login_required
def void_invoice(invoice_id):
    try:
        with db() as conn:
            invoice = invoice_dal.get_by_id(conn, invoice_id)
            if not invoice:
                return jsonify({'error': 'Invoice not found.'}), 404
            if g.role != 'admin' and invoice['issuer_id'] != g.user_id:
                return jsonify({'error': 'Only the issuer or an admin may void this invoice.'}), 403
            ledger_service.void_invoice(conn, g.user_id, invoice_id)
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Invoice voided.'}), 200


@ledger_bp.route('/invoices/<int:invoice_id>/refund', methods=['POST'])
@admin_required
def refund_invoice(invoice_id):
    d = request.get_json(force=True) or {}
    try:
        amount = float(d.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'amount must be a number.'}), 400
    reason = (d.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'reason is required.'}), 400
    try:
        with db() as conn:
            result = ledger_service.refund_invoice(conn, g.user_id,
                                                   invoice_id, amount, reason)
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(result), 200


@ledger_bp.route('/invoices/<int:invoice_id>/adjust', methods=['POST'])
@admin_required
def adjust_invoice(invoice_id):
    d = request.get_json(force=True) or {}
    try:
        delta = float(d.get('delta', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'delta must be a number.'}), 400
    reason = (d.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'reason is required.'}), 400
    try:
        with db() as conn:
            result = ledger_service.adjust_invoice(conn, g.user_id,
                                                   invoice_id, delta, reason)
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(result), 200


@ledger_bp.route('/invoices/mark-overdue', methods=['POST'])
@admin_required
def mark_overdue():
    with db() as conn:
        count = ledger_service.mark_overdue_invoices(conn)
    return jsonify({'message': f'{count} invoice(s) marked overdue.', 'count': count}), 200


# ---------------------------------------------------------------------------
# Financial Summaries — AR / AP / Reconciliation
# Requires admin or auditor role.  All accesses are audit-logged.
# ---------------------------------------------------------------------------

@ledger_bp.route('/ar-summary', methods=['GET'])
@auditor_or_admin_required
def ar_summary():
    """
    Accounts Receivable summary.

    Query parameters:
      from_date  — ISO-8601 date string lower bound on issued_at (optional)
      to_date    — ISO-8601 date string upper bound on issued_at (optional)
      issuer_id  — restrict to a single issuer (optional)

    Response schema:
      generated_at  — ISO-8601 timestamp of report generation
      filters       — echo of applied filters
      totals        — {invoice_count, total_invoiced, total_outstanding,
                       overdue_amount, overdue_count}
      by_status     — {issued: {count, outstanding_amount},
                       overdue: {count, outstanding_amount}}
      by_issuer     — list of {issuer_id, issuer_name, invoice_count,
                               total_invoiced, total_outstanding,
                               overdue_count, overdue_amount}
    """
    from_date, err = _parse_date_param(request.args.get('from_date'), 'from_date')
    if err:
        return err
    to_date, err = _parse_date_param(request.args.get('to_date'), 'to_date')
    if err:
        return err
    issuer_id = request.args.get('issuer_id')
    if issuer_id:
        try:
            issuer_id = int(issuer_id)
        except ValueError:
            return jsonify({'error': 'issuer_id must be an integer.'}), 400

    with db() as conn:
        result = financial_summary_service.get_ar_summary(
            conn, actor_id=g.user_id,
            from_date=from_date, to_date=to_date, issuer_id=issuer_id,
        )
    return jsonify(result), 200


@ledger_bp.route('/ap-summary', methods=['GET'])
@auditor_or_admin_required
def ap_summary():
    """
    Accounts Payable summary.

    Query parameters:
      from_date  — ISO-8601 date string lower bound on issued_at (optional)
      to_date    — ISO-8601 date string upper bound on issued_at (optional)
      payer_id   — restrict to a single payer (optional)

    Response schema:
      generated_at  — ISO-8601 timestamp of report generation
      filters       — echo of applied filters
      totals        — {invoice_count, total_owed, overdue_amount, overdue_count}
      by_status     — {issued: {count, amount_owed},
                       overdue: {count, amount_owed}}
      by_payer      — list of {payer_id, payer_name, invoice_count,
                               total_owed, overdue_count, overdue_amount}
    """
    from_date, err = _parse_date_param(request.args.get('from_date'), 'from_date')
    if err:
        return err
    to_date, err = _parse_date_param(request.args.get('to_date'), 'to_date')
    if err:
        return err
    payer_id  = request.args.get('payer_id')
    if payer_id:
        try:
            payer_id = int(payer_id)
        except ValueError:
            return jsonify({'error': 'payer_id must be an integer.'}), 400

    with db() as conn:
        result = financial_summary_service.get_ap_summary(
            conn, actor_id=g.user_id,
            from_date=from_date, to_date=to_date, payer_id=payer_id,
        )
    return jsonify(result), 200


@ledger_bp.route('/reconciliation-summary', methods=['GET'])
@auditor_or_admin_required
def reconciliation_summary():
    """
    Reconciliation summary — cross-checks paid/refunded invoices against
    the immutable ledger to detect any amount mismatches.

    Query parameters:
      from_date  — ISO-8601 date string lower bound on paid_at (optional)
      to_date    — ISO-8601 date string upper bound on paid_at (optional)

    Response schema:
      generated_at    — ISO-8601 timestamp of report generation
      filters         — echo of applied filters
      totals          — {invoices_examined, total_invoiced, total_collected}
      reconciliation  — {reconciled, discrepant, unmatched}
      discrepancies   — list of {invoice_id, invoice_number, invoice_amount,
                                 amount_paid, status, ledger_payer_debits,
                                 ledger_issuer_credits, issue}
    """
    from_date, err = _parse_date_param(request.args.get('from_date'), 'from_date')
    if err:
        return err
    to_date, err = _parse_date_param(request.args.get('to_date'), 'to_date')
    if err:
        return err

    with db() as conn:
        result = financial_summary_service.get_reconciliation_summary(
            conn, actor_id=g.user_id,
            from_date=from_date, to_date=to_date,
        )
    return jsonify(result), 200
