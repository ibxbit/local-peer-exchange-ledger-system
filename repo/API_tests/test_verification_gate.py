"""
API tests: real-name verification gate for matching actions.

Verified vs unverified user behavior:
  - Unverified users receive 403 with a clear message when they attempt to:
      * Join the matching queue
      * Request a session
      * Trigger a manual match
  - Verified users can perform all of the above.
  - The check lives in the shared guard path, not the route layer.
"""

import io
import pytest
from API_tests.conftest import register_and_login


# ---- Helpers ----------------------------------------------------------------

def _credit(client, admin_headers, uid, amount=500.0):
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount, 'description': 'test'})


def _submit_verification(client, headers):
    """Submit a minimal valid PDF for verification. Returns verification_id."""
    pdf_bytes = b'%PDF-1.4 minimal'
    resp = client.post(
        '/api/verification/submit',
        headers={k: v for k, v in headers.items()
                 if k.lower() != 'content-type'},
        data={
            'document_type': 'passport',
            'document': (io.BytesIO(pdf_bytes), 'test.pdf', 'application/pdf'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 201, f'Verification submit failed: {resp.data}'
    return resp.get_json()['verification_id']


def _approve_verification(client, admin_headers, vid):
    resp = client.put(
        f'/api/verification/{vid}/review',
        headers=admin_headers,
        json={'decision': 'verified', 'notes': 'auto-approved in test'},
    )
    assert resp.status_code == 200, f'Verification approve failed: {resp.data}'


def verify_user(client, admin_headers, user_headers):
    """Full submit + approve flow for a user."""
    vid = _submit_verification(client, user_headers)
    _approve_verification(client, admin_headers, vid)


# ---- Tests ------------------------------------------------------------------

class TestVerificationGateQueue:
    def test_unverified_cannot_join_queue(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_q1', 'vgate_q1@test.com')
        _credit(client, admin_headers, uid)
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code == 403
        msg = resp.get_json()['error'].lower()
        assert 'verification' in msg

    def test_verified_can_join_queue(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_q2', 'vgate_q2@test.com')
        _credit(client, admin_headers, uid)
        verify_user(client, admin_headers, h)
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code == 201

    def test_pending_verification_still_blocked(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_pend', 'vgate_pend@test.com')
        _credit(client, admin_headers, uid)
        _submit_verification(client, h)   # submitted but not approved
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code == 403

    def test_rejected_verification_still_blocked(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_rej', 'vgate_rej@test.com')
        _credit(client, admin_headers, uid)
        vid = _submit_verification(client, h)
        client.put(f'/api/verification/{vid}/review',
                   headers=admin_headers,
                   json={'decision': 'rejected', 'notes': 'failed check'})
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code == 403


class TestVerificationGateSession:
    def test_unverified_cannot_request_session(self, client, admin_headers,
                                                user2_headers_with_id):
        h, uid = register_and_login(client, 'vgate_s1', 'vgate_s1@test.com')
        _, uid2 = user2_headers_with_id
        _credit(client, admin_headers, uid)
        resp = client.post('/api/matching/sessions', headers=h,
                           json={'participant_id': uid2, 'description': 'test',
                                 'credit_amount': 0})
        assert resp.status_code == 403
        assert 'verification' in resp.get_json()['error'].lower()

    def test_verified_can_request_session(self, client, admin_headers,
                                           user2_headers_with_id):
        h, uid = register_and_login(client, 'vgate_s2', 'vgate_s2@test.com')
        _, uid2 = user2_headers_with_id
        _credit(client, admin_headers, uid)
        _credit(client, admin_headers, uid2)
        verify_user(client, admin_headers, h)
        resp = client.post('/api/matching/sessions', headers=h,
                           json={'participant_id': uid2, 'description': 'test',
                                 'credit_amount': 0})
        assert resp.status_code == 201


class TestVerificationGateManualMatch:
    def test_unverified_cannot_trigger_manual_match(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_m1', 'vgate_m1@test.com')
        _credit(client, admin_headers, uid)
        resp = client.post('/api/matching/queue/match', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code == 403
        assert 'verification' in resp.get_json()['error'].lower()

    def test_verified_can_trigger_manual_match(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_m2', 'vgate_m2@test.com')
        _credit(client, admin_headers, uid)
        verify_user(client, admin_headers, h)
        # Manual match returns 200/201 (no match found) — just confirm no 403
        resp = client.post('/api/matching/queue/match', headers=h,
                           json={'skill': 'python'})
        assert resp.status_code in (200, 201)


class TestVerificationGateReturns403NotOther:
    """Ensure verification gate is 403, not 401/400/500."""

    def test_queue_returns_403_not_400_or_401(self, client, admin_headers):
        h, uid = register_and_login(client, 'vgate_code', 'vgate_code@test.com')
        _credit(client, admin_headers, uid)
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'test'})
        assert resp.status_code == 403

    def test_session_returns_403_not_400(self, client, admin_headers,
                                          user_headers_with_id):
        h, uid = register_and_login(client, 'vgate_sess_c', 'vgate_sessc@test.com')
        _, uid2 = user_headers_with_id
        _credit(client, admin_headers, uid)
        resp = client.post('/api/matching/sessions', headers=h,
                           json={'participant_id': uid2, 'description': '',
                                 'credit_amount': 0})
        assert resp.status_code == 403
