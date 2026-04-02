"""Unit tests for audit_dal: write, hash chain, categories, filtering."""

import pytest
from app.dal import audit_dal
from app.utils import hash_audit_entry


class TestAuditWrite:
    def test_write_creates_entry(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id,
                        entity_type='user', entity_id=user_id)
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'LOGIN_SUCCESS'"
        ).fetchone()
        assert row is not None
        assert row['user_id'] == user_id
        assert row['entity_type'] == 'user'

    def test_write_stores_details_as_json(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_FAILED', user_id=user_id,
                        details={'attempts': 3, 'remaining': 2})
        row = conn.execute(
            "SELECT details FROM audit_logs WHERE action = 'LOGIN_FAILED'"
        ).fetchone()
        assert row is not None
        assert '"attempts": 3' in row['details'] or 'attempts' in row['details']

    def test_first_entry_has_null_previous_hash(self, conn, user_id):
        audit_dal.write(conn, 'USER_REGISTERED', user_id=user_id)
        row = conn.execute(
            'SELECT * FROM audit_logs ORDER BY id ASC LIMIT 1'
        ).fetchone()
        assert row['previous_hash'] is None

    def test_chain_links_entries(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        audit_dal.write(conn, 'PASSWORD_CHANGED', user_id=user_id)

        entries = conn.execute(
            'SELECT * FROM audit_logs ORDER BY id ASC'
        ).fetchall()
        assert len(entries) >= 2
        # Second entry's previous_hash must equal first entry's log_hash
        assert entries[1]['previous_hash'] == entries[0]['log_hash']

    def test_hash_is_deterministic_and_correct(self, conn, user_id):
        audit_dal.write(conn, 'ROLE_CHANGED', user_id=user_id,
                        entity_type='user', entity_id=user_id)
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'ROLE_CHANGED'"
        ).fetchone()
        entry = dict(row)
        expected = hash_audit_entry(entry, entry['previous_hash'])
        assert expected == entry['log_hash']


class TestAuditCategories:
    def test_auth_actions_categorised(self):
        for action in ('LOGIN_SUCCESS', 'LOGIN_FAILED', 'USER_REGISTERED',
                       'PASSWORD_CHANGED', 'LOGIN_LOCKED'):
            assert audit_dal.ACTION_CATEGORIES[action] == 'auth'

    def test_financial_actions_categorised(self):
        for action in ('LEDGER_CREDIT', 'LEDGER_DEBIT', 'INVOICE_ISSUED',
                       'PAYMENT_SUBMITTED', 'PAYMENT_CONFIRMED',
                       'PAYMENT_REFUNDED', 'PAYMENT_CALLBACK_FIRED'):
            assert audit_dal.ACTION_CATEGORIES[action] == 'financial'

    def test_permissions_actions_categorised(self):
        for action in ('ROLE_CHANGED', 'STATUS_CHANGED',
                       'USER_BANNED', 'USER_UNBANNED'):
            assert audit_dal.ACTION_CATEGORIES[action] == 'permissions'

    def test_data_access_actions_categorised(self):
        for action in ('DATA_EXPORTED', 'AUDIT_LOG_ACCESSED',
                       'VERIFICATION_DOCUMENT_ACCESSED'):
            assert audit_dal.ACTION_CATEGORIES[action] == 'data_access'

    def test_admin_actions_categorised(self):
        for action in ('VIOLATION_REPORTED', 'APPEAL_FILED',
                       'SESSION_COMPLETED', 'DAILY_REPORT_GENERATED'):
            assert audit_dal.ACTION_CATEGORIES[action] == 'admin'

    def test_unknown_action_defaults_to_admin(self):
        cat = audit_dal._category_for('COMPLETELY_UNKNOWN_ACTION')
        assert cat == 'admin'


class TestListLogs:
    def _write_several(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS',    user_id=user_id)
        audit_dal.write(conn, 'LOGIN_FAILED',     user_id=user_id)
        audit_dal.write(conn, 'ROLE_CHANGED',     user_id=user_id)
        audit_dal.write(conn, 'LEDGER_CREDIT',    user_id=user_id)
        audit_dal.write(conn, 'DATA_EXPORTED',    user_id=user_id)
        audit_dal.write(conn, 'VIOLATION_REPORTED', user_id=user_id)

    def test_list_all(self, conn, user_id):
        self._write_several(conn, user_id)
        rows, total = audit_dal.list_logs(conn)
        assert total == 6
        assert len(rows) == 6

    def test_filter_by_user(self, conn, user_id, user2_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user2_id)
        rows, total = audit_dal.list_logs(conn, user_id=user_id)
        assert total == 1
        assert rows[0]['user_id'] == user_id

    def test_filter_by_category_auth(self, conn, user_id):
        self._write_several(conn, user_id)
        rows, total = audit_dal.list_logs(conn, category='auth')
        assert all(r['category'] == 'auth' for r in rows)
        assert total == 2  # LOGIN_SUCCESS + LOGIN_FAILED

    def test_filter_by_category_financial(self, conn, user_id):
        self._write_several(conn, user_id)
        rows, total = audit_dal.list_logs(conn, category='financial')
        assert total == 1  # LEDGER_CREDIT
        assert rows[0]['action'] == 'LEDGER_CREDIT'

    def test_rows_include_category_field(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        rows, _ = audit_dal.list_logs(conn)
        assert 'category' in rows[0]

    def test_pagination(self, conn, user_id):
        for _ in range(5):
            audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        rows, total = audit_dal.list_logs(conn, limit=2, offset=0)
        assert len(rows) == 2
        assert total == 5

    def test_action_substring_filter(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        audit_dal.write(conn, 'ROLE_CHANGED',  user_id=user_id)
        rows, total = audit_dal.list_logs(conn, action='LOGIN')
        assert total == 1
        assert rows[0]['action'] == 'LOGIN_SUCCESS'


class TestSummaryByCategory:
    def test_summary_counts_correctly(self, conn, user_id):
        audit_dal.write(conn, 'LOGIN_SUCCESS', user_id=user_id)
        audit_dal.write(conn, 'LOGIN_FAILED',  user_id=user_id)
        audit_dal.write(conn, 'LEDGER_CREDIT', user_id=user_id)

        data = audit_dal.summary_by_category(conn)
        assert data['by_category']['auth'] == 2
        assert data['by_category']['financial'] == 1
        assert 'LOGIN_SUCCESS' in data['by_action']
        assert data['by_action']['LOGIN_SUCCESS'] == 1

    def test_empty_db_returns_zeros(self, conn):
        data = audit_dal.summary_by_category(conn)
        for cat in ('auth', 'permissions', 'financial', 'data_access', 'admin'):
            assert data['by_category'][cat] == 0


class TestGetAllOrdered:
    def test_returns_in_insertion_order(self, conn, user_id):
        actions = ['LOGIN_SUCCESS', 'ROLE_CHANGED', 'LEDGER_CREDIT']
        for a in actions:
            audit_dal.write(conn, a, user_id=user_id)
        entries = audit_dal.get_all_ordered(conn)
        assert [e['action'] for e in entries] == actions
