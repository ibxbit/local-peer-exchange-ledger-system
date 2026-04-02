"""Unit tests for ledger_service: credit, debit, transfer, hash chain."""

import pytest
from app.services import ledger_service
from app.dal import ledger_dal, user_dal
from app.utils import hash_ledger_entry


class TestCredit:
    def test_credit_increases_balance(self, conn, admin_id, user_id):
        before = user_dal.get_by_id(conn, user_id)['credit_balance']
        new_bal = ledger_service.credit(conn, user_id, 100.0, 'Test credit', admin_id)
        assert new_bal == before + 100.0
        stored = user_dal.get_by_id(conn, user_id)['credit_balance']
        assert stored == new_bal

    def test_credit_writes_ledger_entry(self, conn, admin_id, user_id):
        ledger_service.credit(conn, user_id, 50.0, 'Credit entry', admin_id)
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE user_id = ? AND transaction_type = 'credit'",
            (user_id,)
        ).fetchone()
        assert row is not None
        assert row['amount'] == 50.0

    def test_credit_zero_amount_rejected(self, conn, admin_id, user_id):
        with pytest.raises(ValueError, match='positive'):
            ledger_service.credit(conn, user_id, 0, 'Bad', admin_id)

    def test_credit_negative_amount_rejected(self, conn, admin_id, user_id):
        with pytest.raises(ValueError, match='positive'):
            ledger_service.credit(conn, user_id, -10, 'Bad', admin_id)

    def test_credit_nonexistent_user(self, conn, admin_id):
        with pytest.raises(LookupError):
            ledger_service.credit(conn, 99999, 100.0, 'Bad', admin_id)


class TestDebit:
    def test_debit_decreases_balance(self, conn, admin_id, user_id):
        before = user_dal.get_by_id(conn, user_id)['credit_balance']
        new_bal = ledger_service.debit(conn, user_id, 50.0, 'Test debit', admin_id)
        assert new_bal == before - 50.0
        assert user_dal.get_by_id(conn, user_id)['credit_balance'] == new_bal

    def test_debit_insufficient_balance(self, conn, admin_id, user_id):
        balance = user_dal.get_by_id(conn, user_id)['credit_balance']
        with pytest.raises(ValueError, match='Insufficient'):
            ledger_service.debit(conn, user_id, balance + 1, 'Too much', admin_id)

    def test_debit_writes_ledger_entry(self, conn, admin_id, user_id):
        ledger_service.debit(conn, user_id, 10.0, 'Debit entry', admin_id)
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE user_id = ? AND transaction_type = 'debit'",
            (user_id,)
        ).fetchone()
        assert row is not None


class TestTransfer:
    def test_transfer_moves_balance(self, conn, user_id, user2_id, admin_id):
        # Give user_id enough credits (guard requires >= 60)
        ledger_service.credit(conn, user_id, 200.0, 'Top up', admin_id)
        sender_before = user_dal.get_by_id(conn, user_id)['credit_balance']
        recv_before   = user_dal.get_by_id(conn, user2_id)['credit_balance']

        ledger_service.transfer(conn, user_id, user2_id, 50.0, 'Peer transfer')

        sender_after = user_dal.get_by_id(conn, user_id)['credit_balance']
        recv_after   = user_dal.get_by_id(conn, user2_id)['credit_balance']
        assert sender_after == sender_before - 50.0
        assert recv_after   == recv_before + 50.0

    def test_transfer_to_self_rejected(self, conn, user_id, admin_id):
        ledger_service.credit(conn, user_id, 200.0, 'Top up', admin_id)
        with pytest.raises(ValueError, match='yourself'):
            ledger_service.transfer(conn, user_id, user_id, 10.0, 'Bad')

    def test_transfer_insufficient_balance_rejected(self, conn, user_id, user2_id, admin_id):
        balance = user_dal.get_by_id(conn, user_id)['credit_balance']
        with pytest.raises((ValueError, PermissionError)):
            ledger_service.transfer(conn, user_id, user2_id,
                                    balance + 1000, 'Too much')


class TestHashChainIntegrity:
    def test_entries_form_valid_chain(self, conn, admin_id, user_id):
        ledger_service.credit(conn, user_id, 10.0, 'First', admin_id)
        ledger_service.credit(conn, user_id, 20.0, 'Second', admin_id)
        ledger_service.debit(conn, user_id, 5.0,  'Third', admin_id)

        entries = conn.execute(
            'SELECT * FROM ledger_entries ORDER BY id ASC'
        ).fetchall()

        prev_hash = None
        for entry in entries:
            entry_dict = dict(entry)
            expected = hash_ledger_entry(entry_dict, prev_hash)
            assert expected == entry_dict['entry_hash'], (
                f"Hash mismatch at entry id={entry_dict['id']}"
            )
            prev_hash = entry_dict['entry_hash']

    def test_verify_chain_returns_valid(self, conn, admin_id, user_id):
        ledger_service.credit(conn, user_id, 100.0, 'Chain credit', admin_id)
        result = ledger_service.verify_chain(conn)
        assert result['valid'] is True

    def test_empty_chain_is_valid(self, conn):
        result = ledger_service.verify_chain(conn)
        assert result['valid'] is True

    def test_previous_hash_links_entries(self, conn, admin_id, user_id):
        ledger_service.credit(conn, user_id, 10.0, 'Entry A', admin_id)
        ledger_service.credit(conn, user_id, 20.0, 'Entry B', admin_id)

        entries = conn.execute(
            'SELECT * FROM ledger_entries ORDER BY id ASC'
        ).fetchall()
        assert len(entries) == 2
        assert entries[1]['previous_hash'] == entries[0]['entry_hash']
