"""API tests for /api/ledger: balance, credit, debit, transfer, verify, invoices."""

import pytest


def _get_user_id(client, admin_headers, username):
    """Helper: look up a user's ID via the admin users list."""
    resp = client.get(f'/api/users?search={username}', headers=admin_headers)
    users = resp.get_json().get('users', [])
    for u in users:
        if u['username'] == username:
            return u['id']
    return None


class TestBalance:
    def test_own_balance(self, client, user_headers):
        resp = client.get('/api/ledger/balance', headers=user_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'balance' in data
        assert 'user_id' in data

    def test_unauthenticated_rejected(self, client):
        resp = client.get('/api/ledger/balance')
        assert resp.status_code == 401

    def test_admin_can_check_other_user_balance(self, client, admin_headers,
                                                  user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.get(f'/api/ledger/balance?user_id={uid}',
                          headers=admin_headers)
        assert resp.status_code == 200


class TestCreditDebit:
    def test_admin_can_credit_user(self, client, admin_headers,
                                    user_headers_with_id):
        _, uid = user_headers_with_id
        before = client.get(f'/api/ledger/balance?user_id={uid}',
                            headers=admin_headers).get_json()['balance']

        resp = client.post('/api/ledger/credit', headers=admin_headers,
                           json={'user_id': uid, 'amount': 100.0,
                                 'description': 'Test credit'})
        assert resp.status_code == 200
        assert resp.get_json()['new_balance'] == before + 100.0

    def test_non_admin_credit_forbidden(self, client, user_headers,
                                         user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.post('/api/ledger/credit', headers=user_headers,
                           json={'user_id': uid, 'amount': 50.0})
        assert resp.status_code == 403

    def test_admin_can_debit_user(self, client, admin_headers,
                                   user_headers_with_id):
        _, uid = user_headers_with_id
        # Ensure enough balance
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid, 'amount': 200.0, 'description': 'Pre-debit credit'})

        before = client.get(f'/api/ledger/balance?user_id={uid}',
                            headers=admin_headers).get_json()['balance']
        resp = client.post('/api/ledger/debit', headers=admin_headers,
                           json={'user_id': uid, 'amount': 50.0,
                                 'description': 'Test debit'})
        assert resp.status_code == 200
        assert resp.get_json()['new_balance'] == before - 50.0

    def test_debit_insufficient_balance(self, client, admin_headers,
                                         user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.post('/api/ledger/debit', headers=admin_headers,
                           json={'user_id': uid, 'amount': 999999.0,
                                 'description': 'Too much'})
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_zero_amount_rejected(self, client, admin_headers,
                                   user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.post('/api/ledger/credit', headers=admin_headers,
                           json={'user_id': uid, 'amount': 0})
        assert resp.status_code == 400


class TestTransfer:
    def test_transfer_between_users(self, client, admin_headers,
                                     user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        headers2, uid2 = user2_headers_with_id

        # Ensure sender has enough balance (guard: >= 60)
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid1, 'amount': 500.0, 'description': 'Top up'})

        before1 = client.get(f'/api/ledger/balance?user_id={uid1}',
                              headers=admin_headers).get_json()['balance']
        before2 = client.get(f'/api/ledger/balance?user_id={uid2}',
                              headers=admin_headers).get_json()['balance']

        resp = client.post('/api/ledger/transfer', headers=headers1,
                           json={'to_user_id': uid2, 'amount': 50.0,
                                 'description': 'Peer payment'})
        assert resp.status_code == 200

        after1 = client.get(f'/api/ledger/balance?user_id={uid1}',
                             headers=admin_headers).get_json()['balance']
        after2 = client.get(f'/api/ledger/balance?user_id={uid2}',
                             headers=admin_headers).get_json()['balance']
        assert after1 == before1 - 50.0
        assert after2 == before2 + 50.0

    def test_transfer_unauthenticated_rejected(self, client,
                                                user_headers_with_id):
        _, uid2 = user_headers_with_id
        resp = client.post('/api/ledger/transfer',
                           json={'to_user_id': uid2, 'amount': 10.0})
        assert resp.status_code == 401


class TestVerifyChain:
    def test_verify_returns_valid(self, client, admin_headers):
        resp = client.get('/api/ledger/verify', headers=admin_headers)
        assert resp.status_code == 200
        assert resp.get_json()['valid'] is True

    def test_non_admin_verify_forbidden(self, client, user_headers):
        resp = client.get('/api/ledger/verify', headers=user_headers)
        assert resp.status_code == 403


class TestInvoices:
    def test_create_invoice(self, client, admin_headers,
                             user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        resp = client.post('/api/ledger/invoices', headers=headers1,
                           json={'payer_id': uid2, 'amount': 150.0,
                                 'notes': 'Test invoice'})
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'invoice_number' in data
        assert data['status'] == 'issued'

    def test_list_invoices(self, client, user_headers):
        resp = client.get('/api/ledger/invoices', headers=user_headers)
        assert resp.status_code == 200
        assert 'invoices' in resp.get_json()

    def test_get_invoice_own(self, client, user_headers_with_id,
                              user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        create = client.post('/api/ledger/invoices', headers=headers1,
                             json={'payer_id': uid2, 'amount': 75.0})
        inv_id = create.get_json()['id']

        resp = client.get(f'/api/ledger/invoices/{inv_id}', headers=headers1)
        assert resp.status_code == 200

    def test_get_invoice_unauthorized(self, client, admin_headers,
                                       user_headers_with_id,
                                       user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        # Create a third user not involved in the invoice
        from API_tests.conftest import register_and_login
        h3, _ = register_and_login(client, 'inv_stranger',
                                    'inv_stranger@test.com')

        create = client.post('/api/ledger/invoices', headers=headers1,
                             json={'payer_id': uid2, 'amount': 25.0})
        inv_id = create.get_json()['id']

        resp = client.get(f'/api/ledger/invoices/{inv_id}', headers=h3)
        assert resp.status_code == 403

    def test_pay_invoice(self, client, admin_headers,
                          user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        headers2, uid2 = user2_headers_with_id

        # Top up payer balance
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid2, 'amount': 500.0, 'description': 'Pay top-up'})

        create = client.post('/api/ledger/invoices', headers=headers1,
                             json={'payer_id': uid2, 'amount': 50.0})
        inv_id = create.get_json()['id']

        resp = client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=headers2)
        assert resp.status_code == 200

    def test_void_invoice(self, client, user_headers_with_id,
                           user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        create = client.post('/api/ledger/invoices', headers=headers1,
                             json={'payer_id': uid2, 'amount': 30.0})
        inv_id = create.get_json()['id']

        resp = client.post(f'/api/ledger/invoices/{inv_id}/void',
                           headers=headers1)
        assert resp.status_code == 200
