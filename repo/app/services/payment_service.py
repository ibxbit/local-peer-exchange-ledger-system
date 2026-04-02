"""
Offline Payment Service.

Flow:
  1. User calls submit_payment() → record created with HMAC-SHA256 signature.
  2. Admin calls confirm_payment() → signature re-verified, ledger credited,
     callback simulated in-process (no external calls).
  3. Admin calls refund_payment() → ledger debited (reversing entry),
     status set to 'refunded'.

Signature canonical payload (sorted JSON):
  { amount, created_at, payment_type, reference_number, user_id }
"""

from app.dal import ledger_dal, user_dal, audit_dal, payment_dal
from app.utils import sign_payment_payload, verify_payment_signature, utcnow


def submit_payment(conn, user_id: int, amount: float,
                   payment_type: str, reference_number: str,
                   notes: str = None) -> dict:
    """
    Record a new offline payment claim.
    Signs the canonical payload so the signature travels with the record.
    Returns {'payment_id': int, 'signature': str}.
    Raises LookupError if user not found.
    Raises ValueError on bad input.
    """
    if amount <= 0:
        raise ValueError('Amount must be positive.')
    if payment_type not in ('cash', 'check', 'ach'):
        raise ValueError("payment_type must be 'cash', 'check', or 'ach'.")
    if not reference_number or not reference_number.strip():
        raise ValueError('reference_number is required.')

    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')

    # Build canonical payload — same fields reconstructed on confirm
    now = utcnow()
    canonical = {
        'user_id':          user_id,
        'amount':           amount,
        'payment_type':     payment_type,
        'reference_number': reference_number.strip(),
        'created_at':       now,
    }
    signature = sign_payment_payload(canonical)

    payment_id = payment_dal.create(
        conn, user_id, amount, payment_type,
        reference_number.strip(), signature, notes
    )
    # Overwrite created_at with the exact value used for signing
    conn.execute(
        'UPDATE offline_payments SET created_at = ? WHERE id = ?',
        (now, payment_id)
    )

    audit_dal.write(conn, 'PAYMENT_SUBMITTED', user_id=user_id,
                    entity_type='offline_payment', entity_id=payment_id,
                    details={'amount': amount, 'payment_type': payment_type,
                             'reference_number': reference_number.strip()})
    return {'payment_id': payment_id, 'signature': signature}


def confirm_payment(conn, payment_id: int, admin_id: int) -> dict:
    """
    Admin confirms a pending payment.
    Verifies HMAC-SHA256 signature; on failure marks the record 'failed'.
    On success credits the user's ledger balance and fires callback simulation.
    Returns {'payment_id': int, 'new_balance': float}.
    Raises LookupError, ValueError, or PermissionError.
    """
    payment = payment_dal.get_by_id(conn, payment_id)
    if not payment:
        raise LookupError('Payment not found.')
    if payment['status'] != 'pending':
        raise ValueError(f'Payment is already {payment["status"]}.')

    # Re-construct the canonical payload exactly as it was at submit time
    canonical = {
        'user_id':          payment['user_id'],
        'amount':           payment['amount'],
        'payment_type':     payment['payment_type'],
        'reference_number': payment['reference_number'],
        'created_at':       payment['created_at'],
    }
    if not verify_payment_signature(canonical, payment['signature']):
        payment_dal.set_failed(conn, payment_id)
        audit_dal.write(conn, 'PAYMENT_FAILED', user_id=admin_id,
                        entity_type='offline_payment', entity_id=payment_id,
                        details={'reason': 'signature_mismatch'})
        raise ValueError('Payment signature verification failed.')

    # Credit the payer's balance
    user = user_dal.get_by_id(conn, payment['user_id'])
    if not user:
        raise LookupError('Payer user not found.')

    new_balance = round(user['credit_balance'] + payment['amount'], 4)
    user_dal.update_fields(conn, payment['user_id'], credit_balance=new_balance)
    ledger_dal.insert_entry(
        conn,
        payment['user_id'], 'credit',
        payment['amount'], new_balance,
        admin_id,
        description=(
            f'Offline payment confirmed: {payment["payment_type"]} '
            f'ref {payment["reference_number"]}'
        ),
        reference_id=payment_id,
        reference_type='offline_payment',
    )

    ledger_row = conn.execute(
        'SELECT id FROM ledger_entries '
        'WHERE reference_id = ? AND reference_type = ? ORDER BY id DESC LIMIT 1',
        (payment_id, 'offline_payment')
    ).fetchone()
    ledger_entry_id = ledger_row['id'] if ledger_row else None

    payment_dal.set_confirmed(conn, payment_id, admin_id, ledger_entry_id)

    # Simulate callback in-process (no network calls)
    _simulate_callback(conn, payment_id, admin_id, canonical, payment['signature'])

    audit_dal.write(conn, 'PAYMENT_CONFIRMED', user_id=admin_id,
                    entity_type='offline_payment', entity_id=payment_id,
                    details={
                        'amount':      payment['amount'],
                        'user_id':     payment['user_id'],
                        'new_balance': new_balance,
                    })
    return {'payment_id': payment_id, 'new_balance': new_balance}


def _simulate_callback(conn, payment_id: int, admin_id: int,
                       canonical: dict, stored_signature: str) -> None:
    """
    Simulate an external payment provider callback entirely in-process.
    Re-verifies the HMAC signature and logs the outcome as PAYMENT_CALLBACK_FIRED.
    No network calls are made.
    """
    verified = verify_payment_signature(canonical, stored_signature)
    audit_dal.write(conn, 'PAYMENT_CALLBACK_FIRED', user_id=admin_id,
                    entity_type='offline_payment', entity_id=payment_id,
                    details={
                        'verified':      verified,
                        'payment_type':  canonical['payment_type'],
                        'amount':        canonical['amount'],
                        'callback_type': 'local_simulation',
                    })


def refund_payment(conn, payment_id: int, admin_id: int,
                   reason: str = None) -> dict:
    """
    Refund a confirmed payment by inserting a reversing debit ledger entry.
    Returns {'payment_id': int, 'new_balance': float}.
    Raises LookupError or ValueError.
    """
    payment = payment_dal.get_by_id(conn, payment_id)
    if not payment:
        raise LookupError('Payment not found.')
    if payment['status'] != 'confirmed':
        raise ValueError(
            f'Only confirmed payments can be refunded; '
            f'current status: {payment["status"]}.'
        )

    user = user_dal.get_by_id(conn, payment['user_id'])
    if not user:
        raise LookupError('Payer user not found.')
    if user['credit_balance'] < payment['amount']:
        raise ValueError('Insufficient balance for refund.')

    new_balance = round(user['credit_balance'] - payment['amount'], 4)
    user_dal.update_fields(conn, payment['user_id'], credit_balance=new_balance)

    desc = (
        f'Offline payment refund: {payment["payment_type"]} '
        f'ref {payment["reference_number"]}'
        + (f' — {reason}' if reason else '')
    )
    ledger_dal.insert_entry(
        conn,
        payment['user_id'], 'debit',
        payment['amount'], new_balance,
        admin_id,
        description=desc,
        reference_id=payment_id,
        reference_type='offline_payment_refund',
    )

    refund_row = conn.execute(
        'SELECT id FROM ledger_entries '
        'WHERE reference_id = ? AND reference_type = ? ORDER BY id DESC LIMIT 1',
        (payment_id, 'offline_payment_refund')
    ).fetchone()
    refund_entry_id = refund_row['id'] if refund_row else None

    payment_dal.set_refunded(conn, payment_id, refund_entry_id)

    audit_dal.write(conn, 'PAYMENT_REFUNDED', user_id=admin_id,
                    entity_type='offline_payment', entity_id=payment_id,
                    details={
                        'amount':      payment['amount'],
                        'user_id':     payment['user_id'],
                        'new_balance': new_balance,
                        'reason':      reason,
                    })
    return {'payment_id': payment_id, 'new_balance': new_balance}
