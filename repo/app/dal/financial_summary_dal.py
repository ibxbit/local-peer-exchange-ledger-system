"""
Financial Summary DAL — Accounts Receivable, Accounts Payable, Reconciliation.
Read-only queries only.  No INSERT/UPDATE/DELETE.

AR  = amounts owed TO users as invoice issuers (status: issued/overdue).
AP  = amounts owed BY users as invoice payers  (status: issued/overdue).
Rec = cross-check of paid/refunded invoices against ledger entries.
"""

from app.models import rows_to_list


def _outstanding_where(from_date, to_date, extra_id_col, extra_id_val):
    """
    Build WHERE clause and params for outstanding-invoice queries.
    Date filter is applied on issued_at.
    """
    clauses = ["i.status IN ('issued', 'overdue')"]
    params = []
    if from_date:
        clauses.append('i.issued_at >= ?')
        params.append(from_date)
    if to_date:
        clauses.append('i.issued_at <= ?')
        params.append(to_date)
    if extra_id_val is not None:
        clauses.append(f'{extra_id_col} = ?')
        params.append(int(extra_id_val))
    return ' AND '.join(clauses), params


def ar_summary(conn, from_date=None, to_date=None, issuer_id=None):
    """
    Accounts Receivable summary.

    Returns:
      totals        — aggregate counts/amounts across all matching invoices
      by_status     — breakdown keyed by status ('issued' | 'overdue')
      by_issuer     — per-issuer breakdown sorted by outstanding desc
    """
    where, params = _outstanding_where(from_date, to_date, 'i.issuer_id', issuer_id)

    total_row = conn.execute(f'''
        SELECT
            COUNT(*)                                                                              AS invoice_count,
            COALESCE(SUM(i.amount), 0.0)                                                         AS total_invoiced,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)                                         AS total_outstanding,
            COALESCE(SUM(CASE WHEN i.status = 'overdue'
                              THEN i.amount - i.amount_paid ELSE 0 END), 0.0)                    AS overdue_amount,
            COALESCE(SUM(CASE WHEN i.status = 'overdue' THEN 1 ELSE 0 END), 0)                  AS overdue_count
        FROM invoices i
        WHERE {where}
    ''', params).fetchone()
    totals = dict(total_row)

    status_rows = conn.execute(f'''
        SELECT
            i.status,
            COUNT(*)                                                  AS invoice_count,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)             AS outstanding_amount
        FROM invoices i
        WHERE {where}
        GROUP BY i.status
    ''', params).fetchall()
    by_status = {
        r['status']: {'count': r['invoice_count'],
                      'outstanding_amount': round(r['outstanding_amount'], 4)}
        for r in status_rows
    }

    issuer_rows = rows_to_list(conn.execute(f'''
        SELECT
            i.issuer_id,
            u.username                                                             AS issuer_name,
            COUNT(*)                                                               AS invoice_count,
            COALESCE(SUM(i.amount), 0.0)                                           AS total_invoiced,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)                           AS total_outstanding,
            COALESCE(SUM(CASE WHEN i.status = 'overdue' THEN 1 ELSE 0 END), 0)   AS overdue_count,
            COALESCE(SUM(CASE WHEN i.status = 'overdue'
                              THEN i.amount - i.amount_paid ELSE 0 END), 0.0)     AS overdue_amount
        FROM invoices i
        JOIN users u ON i.issuer_id = u.id
        WHERE {where}
        GROUP BY i.issuer_id, u.username
        ORDER BY total_outstanding DESC
    ''', params).fetchall())

    return {
        'totals':    totals,
        'by_status': by_status,
        'by_issuer': issuer_rows,
    }


def ap_summary(conn, from_date=None, to_date=None, payer_id=None):
    """
    Accounts Payable summary.

    Returns:
      totals      — aggregate counts/amounts across all matching invoices
      by_status   — breakdown keyed by status ('issued' | 'overdue')
      by_payer    — per-payer breakdown sorted by amount owed desc
    """
    where, params = _outstanding_where(from_date, to_date, 'i.payer_id', payer_id)

    total_row = conn.execute(f'''
        SELECT
            COUNT(*)                                                                              AS invoice_count,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)                                         AS total_owed,
            COALESCE(SUM(CASE WHEN i.status = 'overdue'
                              THEN i.amount - i.amount_paid ELSE 0 END), 0.0)                    AS overdue_amount,
            COALESCE(SUM(CASE WHEN i.status = 'overdue' THEN 1 ELSE 0 END), 0)                  AS overdue_count
        FROM invoices i
        WHERE {where}
    ''', params).fetchone()
    totals = dict(total_row)

    status_rows = conn.execute(f'''
        SELECT
            i.status,
            COUNT(*)                                                  AS invoice_count,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)             AS amount_owed
        FROM invoices i
        WHERE {where}
        GROUP BY i.status
    ''', params).fetchall()
    by_status = {
        r['status']: {'count': r['invoice_count'],
                      'amount_owed': round(r['amount_owed'], 4)}
        for r in status_rows
    }

    payer_rows = rows_to_list(conn.execute(f'''
        SELECT
            i.payer_id,
            u.username                                                             AS payer_name,
            COUNT(*)                                                               AS invoice_count,
            COALESCE(SUM(i.amount - i.amount_paid), 0.0)                           AS total_owed,
            COALESCE(SUM(CASE WHEN i.status = 'overdue' THEN 1 ELSE 0 END), 0)   AS overdue_count,
            COALESCE(SUM(CASE WHEN i.status = 'overdue'
                              THEN i.amount - i.amount_paid ELSE 0 END), 0.0)     AS overdue_amount
        FROM invoices i
        JOIN users u ON i.payer_id = u.id
        WHERE {where}
        GROUP BY i.payer_id, u.username
        ORDER BY total_owed DESC
    ''', params).fetchall())

    return {
        'totals':    totals,
        'by_status': by_status,
        'by_payer':  payer_rows,
    }


def reconciliation_summary(conn, from_date=None, to_date=None):
    """
    Reconciliation summary for paid and refunded invoices.

    For each paid/refunded invoice, checks whether matching ledger entries
    exist (reference_type='invoice') and whether the amounts tally.

    A record is 'reconciled' when:
      - the payer's debit ledger entry sums to invoice.amount, AND
      - the issuer's credit ledger entry sums to invoice.amount.

    Returns:
      totals             — aggregate invoice stats
      reconciliation     — counts of reconciled / discrepant / unmatched records
      discrepancies      — list of invoice records where amounts do not tally
    """
    clauses = ["i.status IN ('paid', 'refunded')"]
    params = []
    if from_date:
        clauses.append('i.paid_at >= ?')
        params.append(from_date)
    if to_date:
        clauses.append('i.paid_at <= ?')
        params.append(to_date)
    where = ' AND '.join(clauses)

    # Per-invoice ledger cross-check
    rows = conn.execute(f'''
        SELECT
            i.id                                AS invoice_id,
            i.invoice_number,
            i.issuer_id,
            ui.username                         AS issuer_name,
            i.payer_id,
            up.username                         AS payer_name,
            i.amount,
            i.amount_paid,
            i.status,
            i.paid_at,
            COALESCE(SUM(CASE
                WHEN le.user_id = i.payer_id
                     AND le.transaction_type = 'debit'
                     AND le.reference_type    = 'invoice'
                THEN le.amount ELSE 0 END), 0.0) AS ledger_payer_debits,
            COALESCE(SUM(CASE
                WHEN le.user_id = i.issuer_id
                     AND le.transaction_type = 'credit'
                     AND le.reference_type    = 'invoice'
                THEN le.amount ELSE 0 END), 0.0) AS ledger_issuer_credits,
            COUNT(CASE WHEN le.reference_type = 'invoice' THEN 1 END)
                                                AS ledger_entry_count
        FROM invoices i
        JOIN users ui ON i.issuer_id = ui.id
        JOIN users up ON i.payer_id  = up.id
        LEFT JOIN ledger_entries le ON le.reference_id = i.id
        WHERE {where}
        GROUP BY i.id
        ORDER BY i.id DESC
    ''', params).fetchall()

    # Aggregate totals
    total_invoiced   = 0.0
    total_collected  = 0.0
    reconciled_count = 0
    discrepant_count = 0
    unmatched_count  = 0
    discrepancies    = []

    for r in rows:
        r = dict(r)
        amount     = r['amount']
        payer_deb  = round(r['ledger_payer_debits'], 4)
        issuer_crd = round(r['ledger_issuer_credits'], 4)
        total_invoiced  += amount
        total_collected += r['amount_paid']

        if r['ledger_entry_count'] == 0:
            # No ledger entries found at all — payment recorded but ledger missing
            unmatched_count += 1
            discrepancies.append({
                'invoice_id':     r['invoice_id'],
                'invoice_number': r['invoice_number'],
                'invoice_amount': amount,
                'amount_paid':    r['amount_paid'],
                'status':         r['status'],
                'ledger_payer_debits':   payer_deb,
                'ledger_issuer_credits': issuer_crd,
                'issue': 'No ledger entries found for this paid invoice.',
            })
        elif round(payer_deb, 4) != round(amount, 4) or \
                round(issuer_crd, 4) != round(amount, 4):
            discrepant_count += 1
            issues = []
            if round(payer_deb, 4) != round(amount, 4):
                issues.append(
                    f'Payer debit {payer_deb} != invoice amount {amount}.'
                )
            if round(issuer_crd, 4) != round(amount, 4):
                issues.append(
                    f'Issuer credit {issuer_crd} != invoice amount {amount}.'
                )
            discrepancies.append({
                'invoice_id':     r['invoice_id'],
                'invoice_number': r['invoice_number'],
                'invoice_amount': amount,
                'amount_paid':    r['amount_paid'],
                'status':         r['status'],
                'ledger_payer_debits':   payer_deb,
                'ledger_issuer_credits': issuer_crd,
                'issue': ' '.join(issues),
            })
        else:
            reconciled_count += 1

    return {
        'totals': {
            'invoices_examined': len(rows),
            'total_invoiced':    round(total_invoiced, 4),
            'total_collected':   round(total_collected, 4),
        },
        'reconciliation': {
            'reconciled':  reconciled_count,
            'discrepant':  discrepant_count,
            'unmatched':   unmatched_count,
        },
        'discrepancies': discrepancies,
    }
