"""API tests for /api/payments: submit, confirm, refund, list, get."""

import pytest


class TestSubmitPayment:
    def test_submit_cash_payment(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 100.0,
                               'payment_type': 'cash',
                               'reference_number': 'CASH-PAY-001',
                               'notes': 'Cash at office'
                           })
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'payment_id' in data
        assert 'signature' in data
        assert len(data['signature']) == 64  # HMAC-SHA256 hex

    def test_submit_check_payment(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 250.0,
                               'payment_type': 'check',
                               'reference_number': 'CHK-12345'
                           })
        assert resp.status_code == 201

    def test_submit_ach_payment(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 75.0,
                               'payment_type': 'ach',
                               'reference_number': 'ACH-TRACE-001'
                           })
        assert resp.status_code == 201

    def test_submit_invalid_type(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 100.0,
                               'payment_type': 'bitcoin',
                               'reference_number': 'TX-001'
                           })
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_submit_negative_amount(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': -50.0,
                               'payment_type': 'cash',
                               'reference_number': 'BAD-001'
                           })
        assert resp.status_code == 400

    def test_submit_zero_amount(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 0,
                               'payment_type': 'cash',
                               'reference_number': 'BAD-002'
                           })
        assert resp.status_code == 400

    def test_submit_missing_reference_number(self, client, user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={
                               'amount': 100.0,
                               'payment_type': 'ach',
                               'reference_number': ''
                           })
        assert resp.status_code == 400

    def test_submit_unauthenticated_rejected(self, client):
        resp = client.post('/api/payments/submit',
                           json={'amount': 100.0, 'payment_type': 'cash',
                                 'reference_number': 'X-001'})
        assert resp.status_code == 401


class TestConfirmPayment:
    def _submit(self, client, user_headers, ref, amount=100.0):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={'amount': amount, 'payment_type': 'cash',
                                 'reference_number': ref})
        assert resp.status_code == 201
        return resp.get_json()['payment_id']

    def test_admin_confirms_payment(self, client, admin_headers,
                                     user_headers_with_id):
        # Submit using user_headers_with_id so we can check the right user's balance
        headers, uid = user_headers_with_id
        pid = self._submit(client, headers, 'CONF-001')

        # Check balance before
        bal_before = client.get(f'/api/ledger/balance?user_id={uid}',
                                 headers=admin_headers).get_json()['balance']

        resp = client.post(f'/api/payments/{pid}/confirm',
                           headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['new_balance'] == bal_before + 100.0

    def test_confirm_updates_status(self, client, admin_headers,
                                     user_headers_with_id):
        headers, uid = user_headers_with_id
        pid = self._submit(client, headers, 'CONF-002')
        client.post(f'/api/payments/{pid}/confirm', headers=admin_headers)

        resp = client.get(f'/api/payments/{pid}', headers=admin_headers)
        assert resp.get_json()['payment']['status'] == 'confirmed'

    def test_non_admin_confirm_forbidden(self, client, user_headers):
        pid = self._submit(client, user_headers, 'CONF-003')
        resp = client.post(f'/api/payments/{pid}/confirm', headers=user_headers)
        assert resp.status_code == 403

    def test_confirm_nonexistent_payment(self, client, admin_headers):
        resp = client.post('/api/payments/999999/confirm',
                           headers=admin_headers)
        assert resp.status_code == 404

    def test_double_confirm_rejected(self, client, admin_headers,
                                      user_headers_with_id):
        headers, uid = user_headers_with_id
        pid = self._submit(client, headers, 'CONF-004')
        client.post(f'/api/payments/{pid}/confirm', headers=admin_headers)
        resp = client.post(f'/api/payments/{pid}/confirm', headers=admin_headers)
        assert resp.status_code == 400
        assert 'already' in resp.get_json()['error'].lower()


class TestRefundPayment:
    def _confirmed_payment(self, client, admin_headers, headers, uid, ref,
                            amount=100.0):
        """Submit and confirm a payment for the user identified by (headers, uid)."""
        resp = client.post('/api/payments/submit', headers=headers,
                           json={'amount': amount, 'payment_type': 'cash',
                                 'reference_number': ref})
        pid = resp.get_json()['payment_id']
        client.post(f'/api/payments/{pid}/confirm', headers=admin_headers)
        return pid

    def test_admin_refunds_payment(self, client, admin_headers,
                                    user_headers_with_id):
        headers, uid = user_headers_with_id
        pid = self._confirmed_payment(client, admin_headers, headers, uid, 'REF-001')

        bal_before = client.get(f'/api/ledger/balance?user_id={uid}',
                                 headers=admin_headers).get_json()['balance']
        resp = client.post(f'/api/payments/{pid}/refund', headers=admin_headers,
                           json={'reason': 'Duplicate charge'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['new_balance'] == bal_before - 100.0

    def test_refund_updates_status(self, client, admin_headers,
                                    user_headers_with_id):
        headers, uid = user_headers_with_id
        pid = self._confirmed_payment(client, admin_headers, headers, uid, 'REF-STATUS')
        refund_resp = client.post(f'/api/payments/{pid}/refund', headers=admin_headers)
        assert refund_resp.status_code == 200, (
            f'Refund failed: {refund_resp.get_json()}'
        )
        resp = client.get(f'/api/payments/{pid}', headers=admin_headers)
        assert resp.get_json()['payment']['status'] == 'refunded'

    def test_refund_pending_payment_rejected(self, client, admin_headers,
                                              user_headers):
        resp = client.post('/api/payments/submit', headers=user_headers,
                           json={'amount': 50.0, 'payment_type': 'ach',
                                 'reference_number': 'REF-PEND'})
        pid = resp.get_json()['payment_id']
        resp = client.post(f'/api/payments/{pid}/refund', headers=admin_headers)
        assert resp.status_code == 400

    def test_non_admin_refund_forbidden(self, client, admin_headers,
                                         user_headers, user_headers_with_id):
        headers2, uid2 = user_headers_with_id
        pid = self._confirmed_payment(client, admin_headers, headers2, uid2, 'REF-003')
        resp = client.post(f'/api/payments/{pid}/refund', headers=user_headers)
        assert resp.status_code == 403


class TestListAndGetPayments:
    def test_admin_sees_all_payments(self, client, admin_headers):
        resp = client.get('/api/payments/', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'payments' in data
        assert 'total' in data

    def test_user_sees_only_own(self, client, user_headers,
                                 user2_headers_with_id, admin_headers):
        # user2 submits a payment
        h2, _ = user2_headers_with_id
        client.post('/api/payments/submit', headers=h2,
                    json={'amount': 20.0, 'payment_type': 'cash',
                          'reference_number': 'OWN-001'})

        # user1 submits a payment
        client.post('/api/payments/submit', headers=user_headers,
                    json={'amount': 30.0, 'payment_type': 'cash',
                          'reference_number': 'OWN-002'})

        resp1 = client.get('/api/payments/', headers=user_headers)
        resp2 = client.get('/api/payments/', headers=h2)

        # Each user sees only their own payments
        ids1 = {p['user_id'] for p in resp1.get_json()['payments']}
        ids2 = {p['user_id'] for p in resp2.get_json()['payments']}
        assert len(ids1) == 1
        assert len(ids2) == 1
        assert ids1 != ids2

    def test_filter_by_status(self, client, admin_headers):
        resp = client.get('/api/payments/?status=pending',
                          headers=admin_headers)
        assert resp.status_code == 200
        payments = resp.get_json()['payments']
        assert all(p['status'] == 'pending' for p in payments)

    def test_invalid_status_filter(self, client, admin_headers):
        resp = client.get('/api/payments/?status=invalid_status',
                          headers=admin_headers)
        assert resp.status_code == 400

    def test_get_own_payment(self, client, user_headers):
        submit = client.post('/api/payments/submit', headers=user_headers,
                             json={'amount': 10.0, 'payment_type': 'cash',
                                   'reference_number': 'GET-001'})
        pid = submit.get_json()['payment_id']
        resp = client.get(f'/api/payments/{pid}', headers=user_headers)
        assert resp.status_code == 200
        assert resp.get_json()['payment']['id'] == pid

    def test_get_other_users_payment_forbidden(self, client, user_headers,
                                                user2_headers_with_id):
        h2, _ = user2_headers_with_id
        submit = client.post('/api/payments/submit', headers=h2,
                             json={'amount': 15.0, 'payment_type': 'check',
                                   'reference_number': 'GET-002'})
        pid = submit.get_json()['payment_id']
        # user1 tries to access user2's payment
        resp = client.get(f'/api/payments/{pid}', headers=user_headers)
        assert resp.status_code == 403

    def test_get_nonexistent_payment(self, client, admin_headers):
        resp = client.get('/api/payments/999999', headers=admin_headers)
        assert resp.status_code == 404

    def test_list_unauthenticated_rejected(self, client):
        resp = client.get('/api/payments/')
        assert resp.status_code == 401
