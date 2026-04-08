"""
Unit tests for:
  A) AR/AP/Reconciliation service + DAL
  B) DB-level immutability triggers on ledger_entries and audit_logs
"""

import sqlite3
import pytest

from app.services import ledger_service, financial_summary_service
from app.dal import financial_summary_dal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_invoice_and_pay(conn, issuer_id, payer_id, admin_id, amount):
    """Create an invoice and fully pay it. Returns invoice dict."""
    from app.services.ledger_service import create_invoice, pay_invoice
    from app.services.ledger_service import credit
    # Ensure payer has enough balance
    credit(conn, payer_id, amount + 100, 'Test top-up', admin_id)
    invoice = create_invoice(conn, issuer_id, payer_id, amount)
    conn.commit()
    pay_invoice(conn, payer_id, invoice['id'])
    conn.commit()
    return invoice


def _create_outstanding_invoice(conn, issuer_id, payer_id, amount):
    """Create an issued (unpaid) invoice."""
    from app.services.ledger_service import create_invoice
    inv = create_invoice(conn, issuer_id, payer_id, amount)
    conn.commit()
    return inv


# ---------------------------------------------------------------------------
# AR Summary
# ---------------------------------------------------------------------------

class TestARSummary:
    def test_empty_ar_returns_zero_totals(self, conn, admin_id, user_id):
        result = financial_summary_dal.ar_summary(conn)
        assert result['totals']['invoice_count'] == 0
        assert result['totals']['total_outstanding'] == 0.0
        assert result['by_issuer'] == []

    def test_outstanding_invoice_appears_in_ar(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 200.0)
        result = financial_summary_dal.ar_summary(conn)
        assert result['totals']['invoice_count'] == 1
        assert result['totals']['total_outstanding'] == 200.0
        assert result['totals']['total_invoiced'] == 200.0
        assert len(result['by_issuer']) == 1
        assert result['by_issuer'][0]['issuer_id'] == user_id

    def test_paid_invoice_excluded_from_ar(self, conn, admin_id, user_id, user2_id):
        _create_invoice_and_pay(conn, user_id, user2_id, admin_id, 100.0)
        result = financial_summary_dal.ar_summary(conn)
        # Paid invoice must NOT appear in AR
        assert result['totals']['invoice_count'] == 0
        assert result['totals']['total_outstanding'] == 0.0

    def test_ar_filter_by_issuer_id(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 150.0)
        _create_outstanding_invoice(conn, user2_id, user_id, 80.0)
        result = financial_summary_dal.ar_summary(conn, issuer_id=user_id)
        assert result['totals']['invoice_count'] == 1
        assert result['by_issuer'][0]['issuer_id'] == user_id

    def test_ar_service_adds_generated_at_and_filters(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 50.0)
        result = financial_summary_service.get_ar_summary(
            conn, actor_id=admin_id, issuer_id=user_id
        )
        assert 'generated_at' in result
        assert 'filters' in result
        assert result['filters']['issuer_id'] == user_id

    def test_ar_service_writes_audit_log(self, conn, admin_id, user_id):
        financial_summary_service.get_ar_summary(conn, actor_id=admin_id)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'AR_SUMMARY_ACCESSED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row['user_id'] == admin_id

    def test_ar_by_status_breakdown(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 300.0)
        result = financial_summary_dal.ar_summary(conn)
        # All invoices start as 'issued'
        assert 'issued' in result['by_status']
        assert result['by_status']['issued']['count'] == 1


# ---------------------------------------------------------------------------
# AP Summary
# ---------------------------------------------------------------------------

class TestAPSummary:
    def test_empty_ap_returns_zero_totals(self, conn, admin_id, user_id):
        result = financial_summary_dal.ap_summary(conn)
        assert result['totals']['invoice_count'] == 0
        assert result['totals']['total_owed'] == 0.0
        assert result['by_payer'] == []

    def test_outstanding_invoice_appears_in_ap(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 250.0)
        result = financial_summary_dal.ap_summary(conn)
        assert result['totals']['invoice_count'] == 1
        assert result['totals']['total_owed'] == 250.0
        assert len(result['by_payer']) == 1
        assert result['by_payer'][0]['payer_id'] == user2_id

    def test_paid_invoice_excluded_from_ap(self, conn, admin_id, user_id, user2_id):
        _create_invoice_and_pay(conn, user_id, user2_id, admin_id, 100.0)
        result = financial_summary_dal.ap_summary(conn)
        assert result['totals']['invoice_count'] == 0
        assert result['totals']['total_owed'] == 0.0

    def test_ap_filter_by_payer_id(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 120.0)
        _create_outstanding_invoice(conn, user2_id, user_id, 60.0)
        result = financial_summary_dal.ap_summary(conn, payer_id=user2_id)
        assert result['totals']['invoice_count'] == 1
        assert result['by_payer'][0]['payer_id'] == user2_id

    def test_ap_service_adds_generated_at_and_filters(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 90.0)
        result = financial_summary_service.get_ap_summary(
            conn, actor_id=admin_id, payer_id=user2_id
        )
        assert 'generated_at' in result
        assert result['filters']['payer_id'] == user2_id

    def test_ap_service_writes_audit_log(self, conn, admin_id, user_id):
        financial_summary_service.get_ap_summary(conn, actor_id=admin_id)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'AP_SUMMARY_ACCESSED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row['user_id'] == admin_id

    def test_ap_by_status_breakdown(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 100.0)
        result = financial_summary_dal.ap_summary(conn)
        assert 'issued' in result['by_status']
        assert result['by_status']['issued']['count'] == 1


# ---------------------------------------------------------------------------
# Reconciliation Summary
# ---------------------------------------------------------------------------

class TestReconciliationSummary:
    def test_empty_reconciliation(self, conn, admin_id):
        result = financial_summary_dal.reconciliation_summary(conn)
        assert result['totals']['invoices_examined'] == 0
        assert result['reconciliation']['reconciled'] == 0
        assert result['discrepancies'] == []

    def test_paid_invoice_reconciles_correctly(self, conn, admin_id, user_id, user2_id):
        _create_invoice_and_pay(conn, user_id, user2_id, admin_id, 150.0)
        result = financial_summary_dal.reconciliation_summary(conn)
        assert result['totals']['invoices_examined'] == 1
        assert result['reconciliation']['reconciled'] == 1
        assert result['reconciliation']['discrepant'] == 0
        assert result['discrepancies'] == []

    def test_unpaid_invoice_not_in_reconciliation(self, conn, admin_id, user_id, user2_id):
        _create_outstanding_invoice(conn, user_id, user2_id, 200.0)
        result = financial_summary_dal.reconciliation_summary(conn)
        # Only paid/refunded invoices appear in reconciliation
        assert result['totals']['invoices_examined'] == 0

    def test_reconciliation_totals_match(self, conn, admin_id, user_id, user2_id):
        _create_invoice_and_pay(conn, user_id, user2_id, admin_id, 100.0)
        _create_invoice_and_pay(conn, user_id, user2_id, admin_id, 200.0)
        result = financial_summary_dal.reconciliation_summary(conn)
        assert result['totals']['invoices_examined'] == 2
        assert result['totals']['total_invoiced'] == 300.0
        assert result['totals']['total_collected'] == 300.0
        assert result['reconciliation']['reconciled'] == 2

    def test_reconciliation_service_adds_audit_log(self, conn, admin_id):
        financial_summary_service.get_reconciliation_summary(conn, actor_id=admin_id)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'RECONCILIATION_ACCESSED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row['user_id'] == admin_id

    def test_reconciliation_service_adds_generated_at(self, conn, admin_id):
        result = financial_summary_service.get_reconciliation_summary(
            conn, actor_id=admin_id
        )
        assert 'generated_at' in result
        assert 'filters' in result


# ---------------------------------------------------------------------------
# DB-Level Immutability Triggers
# ---------------------------------------------------------------------------

class TestImmutabilityTriggers:
    """
    Verify that the SQLite triggers block UPDATE and DELETE on
    ledger_entries and audit_logs at the database layer.
    These tests operate directly on raw SQL to confirm the constraint
    cannot be bypassed by any application-layer path.

    SQLite RAISE(ABORT, ...) surfaces as sqlite3.IntegrityError in Python.
    """

    # sqlite3.IntegrityError is what Python raises for trigger RAISE(ABORT, ...)
    _TRIGGER_EXC = sqlite3.IntegrityError

    def test_update_ledger_entry_raises(self, conn, admin_id, user_id):
        """BEFORE UPDATE trigger on ledger_entries must abort the statement."""
        ledger_service.credit(conn, user_id, 50.0, 'Trigger test credit', admin_id)
        conn.commit()
        entry_id = conn.execute(
            'SELECT id FROM ledger_entries ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        with pytest.raises(self._TRIGGER_EXC) as exc_info:
            conn.execute(
                'UPDATE ledger_entries SET amount = 9999 WHERE id = ?',
                (entry_id,)
            )
        assert 'IMMUTABLE VIOLATION' in str(exc_info.value)
        assert 'ledger_entries' in str(exc_info.value)

    def test_delete_ledger_entry_raises(self, conn, admin_id, user_id):
        """BEFORE DELETE trigger on ledger_entries must abort the statement."""
        ledger_service.credit(conn, user_id, 50.0, 'Trigger test credit', admin_id)
        conn.commit()
        entry_id = conn.execute(
            'SELECT id FROM ledger_entries ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        with pytest.raises(self._TRIGGER_EXC) as exc_info:
            conn.execute(
                'DELETE FROM ledger_entries WHERE id = ?',
                (entry_id,)
            )
        assert 'IMMUTABLE VIOLATION' in str(exc_info.value)
        assert 'ledger_entries' in str(exc_info.value)

    def test_update_audit_log_raises(self, conn, admin_id, user_id):
        """BEFORE UPDATE trigger on audit_logs must abort the statement."""
        from app.dal import audit_dal
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        conn.commit()
        log_id = conn.execute(
            'SELECT id FROM audit_logs ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        with pytest.raises(self._TRIGGER_EXC) as exc_info:
            conn.execute(
                "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = ?",
                (log_id,)
            )
        assert 'IMMUTABLE VIOLATION' in str(exc_info.value)
        assert 'audit_logs' in str(exc_info.value)

    def test_delete_audit_log_raises(self, conn, admin_id, user_id):
        """BEFORE DELETE trigger on audit_logs must abort the statement."""
        from app.dal import audit_dal
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        conn.commit()
        log_id = conn.execute(
            'SELECT id FROM audit_logs ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        with pytest.raises(self._TRIGGER_EXC) as exc_info:
            conn.execute(
                'DELETE FROM audit_logs WHERE id = ?',
                (log_id,)
            )
        assert 'IMMUTABLE VIOLATION' in str(exc_info.value)
        assert 'audit_logs' in str(exc_info.value)

    def test_insert_ledger_entry_still_works(self, conn, admin_id, user_id):
        """INSERT on ledger_entries must NOT be blocked by the triggers."""
        new_bal = ledger_service.credit(conn, user_id, 10.0, 'Allowed insert', admin_id)
        assert new_bal > 0

    def test_insert_audit_log_still_works(self, conn, user_id):
        """INSERT on audit_logs must NOT be blocked by the triggers."""
        from app.dal import audit_dal
        # Should not raise
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        conn.commit()
        count = conn.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0]
        assert count >= 1

    def test_ledger_trigger_error_message_is_explicit(self, conn, admin_id, user_id):
        """The trigger error message must be descriptive enough to aid debugging."""
        ledger_service.credit(conn, user_id, 20.0, 'Test', admin_id)
        conn.commit()
        entry_id = conn.execute(
            'SELECT id FROM ledger_entries ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        try:
            conn.execute('DELETE FROM ledger_entries WHERE id = ?', (entry_id,))
            pytest.fail('Expected IntegrityError was not raised')
        except self._TRIGGER_EXC as e:
            msg = str(e)
            assert 'append-only' in msg or 'IMMUTABLE' in msg

    def test_audit_trigger_error_message_is_explicit(self, conn, user_id):
        """The audit trigger error message must be descriptive enough to aid debugging."""
        from app.dal import audit_dal
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        conn.commit()
        log_id = conn.execute(
            'SELECT id FROM audit_logs ORDER BY id DESC LIMIT 1'
        ).fetchone()['id']

        try:
            conn.execute("UPDATE audit_logs SET action = 'X' WHERE id = ?", (log_id,))
            pytest.fail('Expected IntegrityError was not raised')
        except self._TRIGGER_EXC as e:
            msg = str(e)
            assert 'append-only' in msg or 'IMMUTABLE' in msg
