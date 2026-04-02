"""
Unit tests for payment_service:
  submit, confirm (incl. signature verification), refund, callback simulation.
"""

import pytest
from app.services import payment_service, ledger_service
from app.dal import payment_dal, user_dal
from app.utils import sign_payment_payload


class TestSubmitPayment:
    def test_submit_creates_pending_record(self, conn, user_id, admin_id):
        result = payment_service.submit_payment(
            conn, user_id, 100.0, 'cash', 'CASH-001'
        )
        assert 'payment_id' in result
        assert 'signature' in result

        rec = payment_dal.get_by_id(conn, result['payment_id'])
        assert rec['status'] == 'pending'
        assert rec['amount'] == 100.0
        assert rec['payment_type'] == 'cash'

    def test_submit_stores_valid_signature(self, conn, user_id):
        result = payment_service.submit_payment(
            conn, user_id, 50.0, 'check', 'CHK-123'
        )
        rec = payment_dal.get_by_id(conn, result['payment_id'])
        canonical = {
            'user_id':          rec['user_id'],
            'amount':           rec['amount'],
            'payment_type':     rec['payment_type'],
            'reference_number': rec['reference_number'],
            'created_at':       rec['created_at'],
        }
        expected = sign_payment_payload(canonical)
        assert rec['signature'] == expected

    def test_invalid_payment_type_rejected(self, conn, user_id):
        with pytest.raises(ValueError, match="payment_type"):
            payment_service.submit_payment(
                conn, user_id, 100.0, 'bitcoin', 'TX-999'
            )

    def test_negative_amount_rejected(self, conn, user_id):
        with pytest.raises(ValueError, match='positive'):
            payment_service.submit_payment(
                conn, user_id, -50.0, 'cash', 'REF-001'
            )

    def test_zero_amount_rejected(self, conn, user_id):
        with pytest.raises(ValueError, match='positive'):
            payment_service.submit_payment(
                conn, user_id, 0, 'cash', 'REF-001'
            )

    def test_missing_reference_number_rejected(self, conn, user_id):
        with pytest.raises(ValueError, match='reference_number'):
            payment_service.submit_payment(
                conn, user_id, 100.0, 'ach', ''
            )

    def test_all_types_accepted(self, conn, user_id):
        for ptype in ('cash', 'check', 'ach'):
            result = payment_service.submit_payment(
                conn, user_id, 10.0, ptype, f'REF-{ptype}'
            )
            assert result['payment_id'] > 0

    def test_audit_log_written(self, conn, user_id):
        payment_service.submit_payment(conn, user_id, 75.0, 'ach', 'ACH-001')
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'PAYMENT_SUBMITTED'"
        ).fetchone()
        assert row is not None


class TestConfirmPayment:
    def _submit(self, conn, user_id, amount=100.0, ptype='cash', ref='REF-001'):
        return payment_service.submit_payment(conn, user_id, amount, ptype, ref)

    def test_confirm_credits_user_balance(self, conn, user_id, admin_id):
        before = user_dal.get_by_id(conn, user_id)['credit_balance']
        result = self._submit(conn, user_id)
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        after = user_dal.get_by_id(conn, user_id)['credit_balance']
        assert after == before + 100.0

    def test_confirm_updates_status(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-002')
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        rec = payment_dal.get_by_id(conn, result['payment_id'])
        assert rec['status'] == 'confirmed'
        assert rec['confirmed_by'] == admin_id

    def test_confirm_creates_ledger_entry(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-003')
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE reference_type = 'offline_payment'"
        ).fetchone()
        assert row is not None
        assert row['transaction_type'] == 'credit'

    def test_confirm_fires_callback_audit(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-004')
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'PAYMENT_CALLBACK_FIRED'"
        ).fetchone()
        assert row is not None

    def test_confirm_nonexistent_payment(self, conn, admin_id):
        with pytest.raises(LookupError):
            payment_service.confirm_payment(conn, 99999, admin_id)

    def test_confirm_already_confirmed_rejected(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-005')
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        with pytest.raises(ValueError, match='already confirmed'):
            payment_service.confirm_payment(conn, result['payment_id'], admin_id)

    def test_tampered_signature_causes_failure(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-006')
        pid = result['payment_id']
        # Tamper the stored signature
        conn.execute(
            'UPDATE offline_payments SET signature = ? WHERE id = ?',
            ('a' * 64, pid)
        )
        conn.commit()
        with pytest.raises(ValueError, match='signature'):
            payment_service.confirm_payment(conn, pid, admin_id)

    def test_failed_after_tamper_audit_logged(self, conn, user_id, admin_id):
        result = self._submit(conn, user_id, ref='REF-007')
        pid = result['payment_id']
        conn.execute(
            'UPDATE offline_payments SET signature = ? WHERE id = ?',
            ('b' * 64, pid)
        )
        conn.commit()
        try:
            payment_service.confirm_payment(conn, pid, admin_id)
        except ValueError:
            pass
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'PAYMENT_FAILED'"
        ).fetchone()
        assert row is not None
        rec = payment_dal.get_by_id(conn, pid)
        assert rec['status'] == 'failed'


class TestRefundPayment:
    def _confirmed_payment(self, conn, user_id, admin_id, amount=100.0, ref='REF-X'):
        result = payment_service.submit_payment(conn, user_id, amount, 'cash', ref)
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)
        return result['payment_id']

    def test_refund_debits_balance(self, conn, user_id, admin_id):
        pid = self._confirmed_payment(conn, user_id, admin_id, ref='REF-R1')
        after_confirm = user_dal.get_by_id(conn, user_id)['credit_balance']
        payment_service.refund_payment(conn, pid, admin_id, 'Duplicate')
        after_refund = user_dal.get_by_id(conn, user_id)['credit_balance']
        assert after_refund == after_confirm - 100.0

    def test_refund_updates_status(self, conn, user_id, admin_id):
        pid = self._confirmed_payment(conn, user_id, admin_id, ref='REF-R2')
        payment_service.refund_payment(conn, pid, admin_id)
        rec = payment_dal.get_by_id(conn, pid)
        assert rec['status'] == 'refunded'

    def test_refund_creates_reversing_ledger_entry(self, conn, user_id, admin_id):
        pid = self._confirmed_payment(conn, user_id, admin_id, ref='REF-R3')
        payment_service.refund_payment(conn, pid, admin_id)
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE reference_type = 'offline_payment_refund'"
        ).fetchone()
        assert row is not None
        assert row['transaction_type'] == 'debit'

    def test_refund_pending_payment_rejected(self, conn, user_id, admin_id):
        result = payment_service.submit_payment(
            conn, user_id, 100.0, 'cash', 'REF-PEND'
        )
        with pytest.raises(ValueError, match='confirmed payments'):
            payment_service.refund_payment(conn, result['payment_id'], admin_id)

    def test_refund_nonexistent_payment(self, conn, admin_id):
        with pytest.raises(LookupError):
            payment_service.refund_payment(conn, 99999, admin_id)

    def test_refund_audit_logged(self, conn, user_id, admin_id):
        pid = self._confirmed_payment(conn, user_id, admin_id, ref='REF-R4')
        payment_service.refund_payment(conn, pid, admin_id, 'Test refund')
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'PAYMENT_REFUNDED'"
        ).fetchone()
        assert row is not None


class TestListPayments:
    def test_list_returns_records(self, conn, user_id, admin_id):
        payment_service.submit_payment(conn, user_id, 25.0, 'ach', 'LIST-001')
        payment_service.submit_payment(conn, user_id, 30.0, 'cash', 'LIST-002')
        rows, total = payment_dal.list_payments(conn, user_id=user_id)
        assert total >= 2

    def test_filter_by_status(self, conn, user_id, admin_id):
        result = payment_service.submit_payment(
            conn, user_id, 40.0, 'check', 'FILT-001'
        )
        payment_service.confirm_payment(conn, result['payment_id'], admin_id)

        confirmed, _ = payment_dal.list_payments(conn, user_id=user_id,
                                                  status='confirmed')
        pending, _   = payment_dal.list_payments(conn, user_id=user_id,
                                                  status='pending')
        assert all(r['status'] == 'confirmed' for r in confirmed)
        assert all(r['status'] == 'pending'   for r in pending)
