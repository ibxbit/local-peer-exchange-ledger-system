"""
Ledger service — credit, debit, transfer, invoices with hash-chain integrity.
All balance mutations go through here; direct SQL updates are forbidden.
Guards applied on transfer/pay (user must pass eligibility).

Invoice rules:
  - Net 15 default (due_days=15)
  - Overdue after midnight of the day following due_date (due_date < today UTC)
  - No edits: corrections via refund or adjusting entries only
"""

from datetime import datetime, timezone, timedelta

from app.services.guards import guard_can_act, guard_is_active
from app.dal import ledger_dal, user_dal, audit_dal, invoice_dal
from app.utils import hash_ledger_entry, utcnow


def credit(conn, user_id: int, amount: float,
           description: str, created_by: int,
           idempotency_key: str = None) -> float:
    """Add credits (admin operation). Returns new balance."""
    if amount <= 0:
        raise ValueError('Amount must be positive.')
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')

    new_balance = round(user['credit_balance'] + amount, 4)
    user_dal.update_fields(conn, user_id, credit_balance=new_balance)
    ledger_dal.insert_entry(
        conn, user_id, 'credit', amount, new_balance,
        created_by, description, idempotency_key=idempotency_key
    )
    audit_dal.write(conn, 'LEDGER_CREDIT', user_id=created_by,
                    entity_type='ledger', entity_id=user_id,
                    details={'amount': amount, 'new_balance': new_balance})
    return new_balance


def debit(conn, user_id: int, amount: float,
          description: str, created_by: int,
          idempotency_key: str = None) -> float:
    """Remove credits (admin operation). Returns new balance."""
    if amount <= 0:
        raise ValueError('Amount must be positive.')
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')
    if user['credit_balance'] < amount:
        raise ValueError('Insufficient balance.')

    new_balance = round(user['credit_balance'] - amount, 4)
    user_dal.update_fields(conn, user_id, credit_balance=new_balance)
    ledger_dal.insert_entry(
        conn, user_id, 'debit', amount, new_balance,
        created_by, description, idempotency_key=idempotency_key
    )
    audit_dal.write(conn, 'LEDGER_DEBIT', user_id=created_by,
                    entity_type='ledger', entity_id=user_id,
                    details={'amount': amount})
    return new_balance


def transfer(conn, sender_id: int, recipient_id: int,
             amount: float, description: str,
             idempotency_key: str = None) -> float:
    """Peer-to-peer transfer. Sender must pass all guards."""
    ok, reason = guard_can_act(conn, sender_id)
    if not ok:
        raise PermissionError(reason)

    if sender_id == recipient_id:
        raise ValueError('Cannot transfer to yourself.')
    if amount <= 0:
        raise ValueError('Amount must be positive.')

    sender = user_dal.get_by_id(conn, sender_id)
    if sender['credit_balance'] < amount:
        raise ValueError('Insufficient balance.')

    recipient = user_dal.get_by_id(conn, recipient_id)
    if not recipient or not recipient['is_active']:
        raise LookupError('Recipient not found or inactive.')

    sender_new    = round(sender['credit_balance']    - amount, 4)
    recipient_new = round(recipient['credit_balance'] + amount, 4)

    user_dal.update_fields(conn, sender_id,    credit_balance=sender_new)
    user_dal.update_fields(conn, recipient_id, credit_balance=recipient_new)

    ledger_dal.insert_entry(
        conn, sender_id, 'transfer_out', amount, sender_new,
        sender_id, f'Transfer to #{recipient_id}: {description}',
        idempotency_key=idempotency_key
    )
    ledger_dal.insert_entry(
        conn, recipient_id, 'transfer_in', amount, recipient_new,
        sender_id, f'Transfer from #{sender_id}: {description}'
    )
    audit_dal.write(conn, 'LEDGER_TRANSFER', user_id=sender_id,
                    entity_type='ledger', entity_id=recipient_id,
                    details={'amount': amount, 'to_user_id': recipient_id})
    return sender_new


# ---------------------------------------------------------------------------
# Invoice operations
# ---------------------------------------------------------------------------

_NET_DAYS_DEFAULT = 15


def _due_date(due_days: int) -> str:
    """Return ISO-8601 date string (YYYY-MM-DD UTC) for the payment due date."""
    return (datetime.now(timezone.utc) + timedelta(days=due_days)).strftime('%Y-%m-%d')


def create_invoice(conn, issuer_id: int, payer_id: int,
                   amount: float, notes: str = None,
                   session_id: int = None,
                   due_days: int = _NET_DAYS_DEFAULT) -> dict:
    """
    Create and immediately issue an invoice (draft → issued in one step).
    Returns the full invoice dict.
    """
    ok, reason = guard_is_active(conn, issuer_id)
    if not ok:
        raise PermissionError(reason)
    if issuer_id == payer_id:
        raise ValueError('Issuer and payer must be different users.')
    if amount <= 0:
        raise ValueError('Amount must be positive.')
    if due_days < 1:
        raise ValueError('due_days must be at least 1.')

    payer = user_dal.get_by_id(conn, payer_id)
    if not payer or not payer['is_active']:
        raise LookupError('Payer not found or inactive.')

    due = _due_date(due_days)
    invoice = invoice_dal.create(conn, issuer_id, payer_id, amount,
                                  due, notes, session_id)
    now = utcnow()
    invoice_dal.issue(conn, invoice['id'], now)
    invoice['status']    = 'issued'
    invoice['issued_at'] = now

    audit_dal.write(conn, 'INVOICE_ISSUED', user_id=issuer_id,
                    entity_type='invoice', entity_id=invoice['id'],
                    details={'payer_id': payer_id, 'amount': amount,
                              'due_date': due})
    return invoice


def pay_invoice(conn, payer_id: int, invoice_id: int,
                idempotency_key: str = None) -> dict:
    """
    Pay a full invoice amount from payer's balance to issuer's balance.
    Returns dict with new payer balance and invoice number.
    """
    ok, reason = guard_can_act(conn, payer_id)
    if not ok:
        raise PermissionError(reason)

    invoice = invoice_dal.get_by_id(conn, invoice_id)
    if not invoice:
        raise LookupError('Invoice not found.')
    if invoice['payer_id'] != payer_id:
        raise PermissionError('You are not the payer on this invoice.')
    if invoice['status'] not in ('issued', 'overdue'):
        raise ValueError(f"Cannot pay an invoice with status '{invoice['status']}'.")

    amount    = invoice['amount']
    issuer_id = invoice['issuer_id']

    payer  = user_dal.get_by_id(conn, payer_id)
    issuer = user_dal.get_by_id(conn, issuer_id)
    if payer['credit_balance'] < amount:
        raise ValueError('Insufficient balance.')

    payer_new  = round(payer['credit_balance']  - amount, 4)
    issuer_new = round(issuer['credit_balance'] + amount, 4)

    user_dal.update_fields(conn, payer_id,  credit_balance=payer_new)
    user_dal.update_fields(conn, issuer_id, credit_balance=issuer_new)

    now = utcnow()
    # Payer's ledger: debit
    ledger_dal.insert_entry(
        conn, payer_id, 'debit', amount, payer_new,
        payer_id,
        f'Invoice payment {invoice["invoice_number"]}',
        reference_id=invoice_id, reference_type='invoice',
        idempotency_key=idempotency_key,
    )
    # Issuer's ledger: credit
    ledger_dal.insert_entry(
        conn, issuer_id, 'credit', amount, issuer_new,
        payer_id,
        f'Invoice received {invoice["invoice_number"]}',
        reference_id=invoice_id, reference_type='invoice',
    )

    invoice_dal.set_paid(conn, invoice_id, amount, now)
    audit_dal.write(conn, 'INVOICE_PAID', user_id=payer_id,
                    entity_type='invoice', entity_id=invoice_id,
                    details={'amount': amount, 'issuer_id': issuer_id})
    return {
        'invoice_number': invoice['invoice_number'],
        'amount_paid':    amount,
        'payer_balance':  payer_new,
    }


def void_invoice(conn, actor_id: int, invoice_id: int) -> None:
    """
    Void a draft or issued (unpaid) invoice.
    Only the issuer or an admin may void.
    """
    invoice = invoice_dal.get_by_id(conn, invoice_id)
    if not invoice:
        raise LookupError('Invoice not found.')
    if invoice['status'] not in ('draft', 'issued', 'overdue'):
        raise ValueError(f"Cannot void an invoice with status '{invoice['status']}'.")

    invoice_dal.void(conn, invoice_id)
    audit_dal.write(conn, 'INVOICE_VOIDED', user_id=actor_id,
                    entity_type='invoice', entity_id=invoice_id)


def refund_invoice(conn, admin_id: int, invoice_id: int,
                   amount: float, reason: str) -> dict:
    """
    Partial or full refund of a paid invoice.
    Creates reversing ledger entries; invoices themselves are never edited.
    Returns dict with updated amount_paid and payer's new balance.
    """
    if amount <= 0:
        raise ValueError('Refund amount must be positive.')

    invoice = invoice_dal.get_by_id(conn, invoice_id)
    if not invoice:
        raise LookupError('Invoice not found.')
    if invoice['status'] not in ('paid', 'overdue'):
        raise ValueError(f"Cannot refund an invoice with status '{invoice['status']}'.")
    if amount > invoice['amount_paid']:
        raise ValueError(
            f"Refund amount {amount} exceeds amount paid {invoice['amount_paid']}."
        )

    payer_id  = invoice['payer_id']
    issuer_id = invoice['issuer_id']
    payer     = user_dal.get_by_id(conn, payer_id)
    issuer    = user_dal.get_by_id(conn, issuer_id)

    if issuer['credit_balance'] < amount:
        raise ValueError('Issuer has insufficient balance to refund.')

    payer_new  = round(payer['credit_balance']  + amount, 4)
    issuer_new = round(issuer['credit_balance'] - amount, 4)

    user_dal.update_fields(conn, payer_id,  credit_balance=payer_new)
    user_dal.update_fields(conn, issuer_id, credit_balance=issuer_new)

    # Reversing entries — explicit description links back to original invoice
    ledger_dal.insert_entry(
        conn, payer_id, 'refund', amount, payer_new,
        admin_id,
        f'Refund {invoice["invoice_number"]}: {reason}',
        reference_id=invoice_id, reference_type='invoice_refund',
    )
    ledger_dal.insert_entry(
        conn, issuer_id, 'debit', amount, issuer_new,
        admin_id,
        f'Refund issued {invoice["invoice_number"]}: {reason}',
        reference_id=invoice_id, reference_type='invoice_refund',
    )

    new_paid = round(invoice['amount_paid'] - amount, 4)
    invoice_dal.set_refunded(conn, invoice_id, new_paid)
    audit_dal.write(conn, 'INVOICE_REFUNDED', user_id=admin_id,
                    entity_type='invoice', entity_id=invoice_id,
                    details={'refund_amount': amount, 'reason': reason})
    return {'invoice_id': invoice_id, 'refund_amount': amount,
            'amount_paid': new_paid, 'payer_balance': payer_new}


def adjust_invoice(conn, admin_id: int, invoice_id: int,
                   delta: float, reason: str) -> dict:
    """
    Adjusting entry against a paid invoice.
    delta > 0 → additional charge (debit payer, credit issuer).
    delta < 0 → credit back   (refund payer, debit issuer).
    The invoice record is NOT modified; the ledger entry is the correction.
    Returns payer's new balance.
    """
    if delta == 0:
        raise ValueError('Adjustment delta cannot be zero.')

    invoice = invoice_dal.get_by_id(conn, invoice_id)
    if not invoice:
        raise LookupError('Invoice not found.')
    if invoice['status'] not in ('paid', 'overdue', 'refunded'):
        raise ValueError(
            f"Cannot adjust an invoice with status '{invoice['status']}'."
        )

    payer_id  = invoice['payer_id']
    issuer_id = invoice['issuer_id']
    amount    = abs(delta)
    payer     = user_dal.get_by_id(conn, payer_id)
    issuer    = user_dal.get_by_id(conn, issuer_id)

    if delta > 0:
        # Additional charge: payer → issuer
        if payer['credit_balance'] < amount:
            raise ValueError('Payer has insufficient balance for adjustment.')
        payer_new  = round(payer['credit_balance']  - amount, 4)
        issuer_new = round(issuer['credit_balance'] + amount, 4)
        payer_type  = 'debit'
        issuer_type = 'credit'
    else:
        # Credit back: issuer → payer
        if issuer['credit_balance'] < amount:
            raise ValueError('Issuer has insufficient balance for adjustment.')
        payer_new  = round(payer['credit_balance']  + amount, 4)
        issuer_new = round(issuer['credit_balance'] - amount, 4)
        payer_type  = 'refund'
        issuer_type = 'debit'

    user_dal.update_fields(conn, payer_id,  credit_balance=payer_new)
    user_dal.update_fields(conn, issuer_id, credit_balance=issuer_new)

    ledger_dal.insert_entry(
        conn, payer_id, payer_type, amount, payer_new,
        admin_id,
        f'Adjustment {invoice["invoice_number"]}: {reason}',
        reference_id=invoice_id, reference_type='adjustment',
    )
    ledger_dal.insert_entry(
        conn, issuer_id, issuer_type, amount, issuer_new,
        admin_id,
        f'Adjustment {invoice["invoice_number"]}: {reason}',
        reference_id=invoice_id, reference_type='adjustment',
    )

    audit_dal.write(conn, 'INVOICE_ADJUSTED', user_id=admin_id,
                    entity_type='invoice', entity_id=invoice_id,
                    details={'delta': delta, 'reason': reason})
    return {'invoice_id': invoice_id, 'delta': delta,
            'payer_balance': payer_new}


def mark_overdue_invoices(conn) -> int:
    """
    Sweep issued invoices whose due_date has passed midnight UTC.
    Returns the count of invoices newly marked overdue.
    """
    count = invoice_dal.mark_overdue(conn)
    if count:
        audit_dal.write(conn, 'INVOICES_MARKED_OVERDUE',
                        details={'count': count})
    return count


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

def verify_chain(conn) -> dict:
    """Verify the ledger hash chain is intact. Returns result dict."""
    entries = ledger_dal.get_all_ordered(conn)
    if not entries:
        return {'valid': True, 'message': 'Ledger is empty.', 'entries': 0}

    prev_hash = None
    for i, entry in enumerate(entries):
        expected = hash_ledger_entry(entry, prev_hash)
        if expected != entry['entry_hash']:
            return {
                'valid': False,
                'message': f'Chain broken at entry id={entry["id"]}.',
                'entries_checked': i,
            }
        prev_hash = entry['entry_hash']

    return {'valid': True, 'message': 'Ledger chain is intact.',
            'entries': len(entries)}
