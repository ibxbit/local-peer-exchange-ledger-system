"""
API tests for financial summary endpoints:
  GET /api/ledger/ar-summary
  GET /api/ledger/ap-summary
  GET /api/ledger/reconciliation-summary

Covers:
  - Happy-path responses for admin and auditor roles
  - Authorization enforcement (regular user → 403, unauthenticated → 401)
  - Response schema validation (required fields present)
  - Audit log records are written on access
"""

import pytest
from API_tests.conftest import register_and_login, create_admin_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def auditor_headers(client):
    """Create an auditor user and return auth headers."""
    from app.models import db as _db
    from app.dal import user_dal as _udal
    from app.utils import generate_token as _gen

    username = 'fin_auditor'
    email    = 'fin_auditor@test.com'
    password = 'Auditor@Test123456!'

    with _db() as conn:
        existing = _udal.get_by_username(conn, username)
        if existing:
            uid = existing['id']
        else:
            uid = _udal.create(conn, username, email, password, role='auditor')
            conn.execute('UPDATE users SET role=? WHERE id=?', ('auditor', uid))

    token = _gen(uid, username, 'auditor')
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='module')
def funded_invoice(client, admin_headers, user_headers_with_id, user2_headers_with_id):
    """Create an issued invoice and a paid invoice for use across tests."""
    headers1, uid1 = user_headers_with_id
    headers2, uid2 = user2_headers_with_id

    # Top up payer balance
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid2, 'amount': 1000.0, 'description': 'API test top-up'})

    # Outstanding invoice (will appear in AR/AP)
    outstanding = client.post('/api/ledger/invoices', headers=headers1,
                              json={'payer_id': uid2, 'amount': 200.0,
                                    'notes': 'AR/AP test invoice'})

    # Paid invoice (will appear in reconciliation)
    paid_inv = client.post('/api/ledger/invoices', headers=headers1,
                           json={'payer_id': uid2, 'amount': 100.0,
                                 'notes': 'Paid for reconciliation'})
    paid_inv_id = paid_inv.get_json()['id']
    client.post(f'/api/ledger/invoices/{paid_inv_id}/pay', headers=headers2)

    return {
        'issuer_id': uid1,
        'payer_id':  uid2,
        'outstanding_id': outstanding.get_json()['id'],
        'paid_invoice_id': paid_inv_id,
    }


# ---------------------------------------------------------------------------
# AR Summary
# ---------------------------------------------------------------------------

class TestARSummaryAPI:
    def test_admin_can_access_ar_summary(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ar-summary', headers=admin_headers)
        assert resp.status_code == 200

    def test_auditor_can_access_ar_summary(self, client, auditor_headers, funded_invoice):
        resp = client.get('/api/ledger/ar-summary', headers=auditor_headers)
        assert resp.status_code == 200

    def test_regular_user_forbidden_from_ar_summary(self, client, user_headers):
        resp = client.get('/api/ledger/ar-summary', headers=user_headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected_from_ar_summary(self, client):
        resp = client.get('/api/ledger/ar-summary')
        assert resp.status_code == 401

    def test_ar_summary_response_schema(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ar-summary', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'generated_at' in data
        assert 'filters'      in data
        assert 'totals'       in data
        assert 'by_status'    in data
        assert 'by_issuer'    in data

    def test_ar_summary_totals_have_required_fields(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ar-summary', headers=admin_headers)
        totals = resp.get_json()['totals']
        for field in ('invoice_count', 'total_invoiced', 'total_outstanding',
                      'overdue_amount', 'overdue_count'):
            assert field in totals, f'Missing field: {field}'

    def test_ar_summary_contains_outstanding_invoice(self, client, admin_headers, funded_invoice):
        issuer_id = funded_invoice['issuer_id']
        resp = client.get(f'/api/ledger/ar-summary?issuer_id={issuer_id}',
                          headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['totals']['invoice_count'] >= 1
        assert data['totals']['total_outstanding'] > 0

    def test_ar_summary_issuer_id_filter_bad_value(self, client, admin_headers):
        resp = client.get('/api/ledger/ar-summary?issuer_id=abc', headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_summary_by_issuer_list(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ar-summary', headers=admin_headers)
        by_issuer = resp.get_json()['by_issuer']
        assert isinstance(by_issuer, list)
        if by_issuer:
            row = by_issuer[0]
            for field in ('issuer_id', 'issuer_name', 'invoice_count',
                          'total_invoiced', 'total_outstanding'):
                assert field in row, f'Missing field in by_issuer: {field}'

    def test_ar_summary_access_is_audit_logged(self, client, admin_headers):
        client.get('/api/ledger/ar-summary', headers=admin_headers)
        # Verify via audit log endpoint
        audit_resp = client.get('/api/audit/logs?action=AR_SUMMARY_ACCESSED',
                                headers=admin_headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.get_json().get('logs', [])
        assert any(l['action'] == 'AR_SUMMARY_ACCESSED' for l in logs)


# ---------------------------------------------------------------------------
# AP Summary
# ---------------------------------------------------------------------------

class TestAPSummaryAPI:
    def test_admin_can_access_ap_summary(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ap-summary', headers=admin_headers)
        assert resp.status_code == 200

    def test_auditor_can_access_ap_summary(self, client, auditor_headers, funded_invoice):
        resp = client.get('/api/ledger/ap-summary', headers=auditor_headers)
        assert resp.status_code == 200

    def test_regular_user_forbidden_from_ap_summary(self, client, user_headers):
        resp = client.get('/api/ledger/ap-summary', headers=user_headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected_from_ap_summary(self, client):
        resp = client.get('/api/ledger/ap-summary')
        assert resp.status_code == 401

    def test_ap_summary_response_schema(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ap-summary', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'generated_at' in data
        assert 'filters'      in data
        assert 'totals'       in data
        assert 'by_status'    in data
        assert 'by_payer'     in data

    def test_ap_summary_totals_have_required_fields(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ap-summary', headers=admin_headers)
        totals = resp.get_json()['totals']
        for field in ('invoice_count', 'total_owed', 'overdue_amount', 'overdue_count'):
            assert field in totals, f'Missing field: {field}'

    def test_ap_summary_contains_outstanding_invoice(self, client, admin_headers, funded_invoice):
        payer_id = funded_invoice['payer_id']
        resp = client.get(f'/api/ledger/ap-summary?payer_id={payer_id}',
                          headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['totals']['invoice_count'] >= 1
        assert data['totals']['total_owed'] > 0

    def test_ap_summary_payer_id_filter_bad_value(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?payer_id=xyz', headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ap_summary_by_payer_list(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/ap-summary', headers=admin_headers)
        by_payer = resp.get_json()['by_payer']
        assert isinstance(by_payer, list)
        if by_payer:
            row = by_payer[0]
            for field in ('payer_id', 'payer_name', 'invoice_count', 'total_owed'):
                assert field in row, f'Missing field in by_payer: {field}'

    def test_ap_summary_access_is_audit_logged(self, client, admin_headers):
        client.get('/api/ledger/ap-summary', headers=admin_headers)
        audit_resp = client.get('/api/audit/logs?action=AP_SUMMARY_ACCESSED',
                                headers=admin_headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.get_json().get('logs', [])
        assert any(l['action'] == 'AP_SUMMARY_ACCESSED' for l in logs)


# ---------------------------------------------------------------------------
# Reconciliation Summary
# ---------------------------------------------------------------------------

class TestReconciliationSummaryAPI:
    def test_admin_can_access_reconciliation(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        assert resp.status_code == 200

    def test_auditor_can_access_reconciliation(self, client, auditor_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=auditor_headers)
        assert resp.status_code == 200

    def test_regular_user_forbidden_from_reconciliation(self, client, user_headers):
        resp = client.get('/api/ledger/reconciliation-summary', headers=user_headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected_from_reconciliation(self, client):
        resp = client.get('/api/ledger/reconciliation-summary')
        assert resp.status_code == 401

    def test_reconciliation_response_schema(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'generated_at'    in data
        assert 'filters'         in data
        assert 'totals'          in data
        assert 'reconciliation'  in data
        assert 'discrepancies'   in data

    def test_reconciliation_totals_have_required_fields(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        totals = resp.get_json()['totals']
        for field in ('invoices_examined', 'total_invoiced', 'total_collected'):
            assert field in totals, f'Missing field: {field}'

    def test_reconciliation_counts_have_required_fields(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        rec = resp.get_json()['reconciliation']
        for field in ('reconciled', 'discrepant', 'unmatched'):
            assert field in rec, f'Missing field: {field}'

    def test_paid_invoice_appears_in_reconciliation(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        data = resp.get_json()
        assert data['totals']['invoices_examined'] >= 1

    def test_paid_invoice_is_reconciled(self, client, admin_headers, funded_invoice):
        resp = client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        data = resp.get_json()
        assert data['reconciliation']['reconciled'] >= 1
        assert data['reconciliation']['discrepant'] == 0

    def test_reconciliation_access_is_audit_logged(self, client, admin_headers):
        client.get('/api/ledger/reconciliation-summary', headers=admin_headers)
        audit_resp = client.get('/api/audit/logs?action=RECONCILIATION_ACCESSED',
                                headers=admin_headers)
        assert audit_resp.status_code == 200
        logs = audit_resp.get_json().get('logs', [])
        assert any(l['action'] == 'RECONCILIATION_ACCESSED' for l in logs)

    def test_reconciliation_date_filter_accepted(self, client, admin_headers):
        resp = client.get(
            '/api/ledger/reconciliation-summary?from_date=2020-01-01&to_date=2099-12-31',
            headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['filters']['from_date'] == '2020-01-01'
        assert data['filters']['to_date']   == '2099-12-31'


# ---------------------------------------------------------------------------
# Date Filter Validation (AR and AP endpoints)
# Malformed date params must return 400 with an 'error' key.
# ---------------------------------------------------------------------------

class TestDateFilterValidation:
    """
    Verify that non-YYYY-MM-DD values for from_date / to_date are rejected
    at the route layer before any DB query runs.
    """

    # --- AR summary ---

    def test_ar_invalid_from_date_plain_text(self, client, admin_headers):
        resp = client.get('/api/ledger/ar-summary?from_date=notadate',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_invalid_from_date_slash_separator(self, client, admin_headers):
        resp = client.get('/api/ledger/ar-summary?from_date=2024/01/01',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_invalid_from_date_reversed_format(self, client, admin_headers):
        resp = client.get('/api/ledger/ar-summary?from_date=01-01-2024',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_invalid_to_date(self, client, admin_headers):
        resp = client.get('/api/ledger/ar-summary?to_date=tomorrow',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_invalid_to_date_partial(self, client, admin_headers):
        """Partial ISO string (YYYY-MM) is not a valid YYYY-MM-DD date."""
        resp = client.get('/api/ledger/ar-summary?to_date=2024-01',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ar_error_message_names_the_param(self, client, admin_headers):
        """The error message should identify which param was malformed."""
        resp = client.get('/api/ledger/ar-summary?from_date=bad',
                          headers=admin_headers)
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'from_date' in body['error']

    def test_ar_valid_date_still_accepted(self, client, admin_headers):
        """Sanity-check: a correct YYYY-MM-DD date must still return 200."""
        resp = client.get('/api/ledger/ar-summary?from_date=2020-01-01',
                          headers=admin_headers)
        assert resp.status_code == 200

    # --- AP summary ---

    def test_ap_invalid_from_date_plain_text(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?from_date=notadate',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ap_invalid_from_date_slash_separator(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?from_date=2024/06/15',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ap_invalid_to_date(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?to_date=next-week',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ap_invalid_to_date_partial(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?to_date=2024-12',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_ap_error_message_names_the_param(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?to_date=bad',
                          headers=admin_headers)
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'to_date' in body['error']

    def test_ap_valid_date_still_accepted(self, client, admin_headers):
        resp = client.get('/api/ledger/ap-summary?to_date=2099-12-31',
                          headers=admin_headers)
        assert resp.status_code == 200
