"""
Financial Summary Service — AR, AP, and Reconciliation.

Business logic layer. Enforces authorization (admin/auditor only),
applies audit logging, and delegates data retrieval to the DAL.

Endpoints that call these functions:
  GET /api/ledger/ar-summary
  GET /api/ledger/ap-summary
  GET /api/ledger/reconciliation-summary
"""

from app.dal import financial_summary_dal, audit_dal
from app.utils import utcnow


def get_ar_summary(conn, actor_id: int,
                   from_date: str = None,
                   to_date:   str = None,
                   issuer_id: int = None) -> dict:
    """
    Return Accounts Receivable summary.
    actor_id is the admin/auditor requesting the report (for audit log).
    """
    data = financial_summary_dal.ar_summary(
        conn,
        from_date=from_date,
        to_date=to_date,
        issuer_id=issuer_id,
    )
    audit_dal.write(
        conn, 'AR_SUMMARY_ACCESSED',
        user_id=actor_id,
        entity_type='financial_summary',
        details={
            'from_date': from_date,
            'to_date':   to_date,
            'issuer_id': issuer_id,
        },
    )
    return {
        'generated_at': utcnow(),
        'filters': {
            'from_date': from_date,
            'to_date':   to_date,
            'issuer_id': issuer_id,
        },
        **data,
    }


def get_ap_summary(conn, actor_id: int,
                   from_date: str = None,
                   to_date:   str = None,
                   payer_id:  int = None) -> dict:
    """
    Return Accounts Payable summary.
    actor_id is the admin/auditor requesting the report (for audit log).
    """
    data = financial_summary_dal.ap_summary(
        conn,
        from_date=from_date,
        to_date=to_date,
        payer_id=payer_id,
    )
    audit_dal.write(
        conn, 'AP_SUMMARY_ACCESSED',
        user_id=actor_id,
        entity_type='financial_summary',
        details={
            'from_date': from_date,
            'to_date':   to_date,
            'payer_id':  payer_id,
        },
    )
    return {
        'generated_at': utcnow(),
        'filters': {
            'from_date': from_date,
            'to_date':   to_date,
            'payer_id':  payer_id,
        },
        **data,
    }


def get_reconciliation_summary(conn, actor_id: int,
                               from_date: str = None,
                               to_date:   str = None) -> dict:
    """
    Return reconciliation summary comparing paid invoices against ledger entries.
    actor_id is the admin/auditor requesting the report (for audit log).
    """
    data = financial_summary_dal.reconciliation_summary(
        conn,
        from_date=from_date,
        to_date=to_date,
    )
    audit_dal.write(
        conn, 'RECONCILIATION_ACCESSED',
        user_id=actor_id,
        entity_type='financial_summary',
        details={
            'from_date': from_date,
            'to_date':   to_date,
        },
    )
    return {
        'generated_at': utcnow(),
        'filters': {
            'from_date': from_date,
            'to_date':   to_date,
        },
        **data,
    }
