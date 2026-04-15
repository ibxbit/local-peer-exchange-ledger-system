"""
API tests covering routes that were under-exercised by the existing suite.

Modules covered:
  1. Admin       — mute/unmute, analytics, violation detail/escalate, appeals
  2. Verification — own status, list queue, decrypt document
  3. Reputation  — rate, ratings, score, violations, appeals (file/list/resolve)
  4. Ledger      — full listing, refund, adjust, mark-overdue
  5. Matching    — single session retrieval (block/expired and /tags do not
                    exist on the current blueprint — see TestMatchingMissing)
  6. Root SPA    — index and SPA fallback

For every route we exercise both the happy path and at least one error /
authorization path.  Where applicable we also cross-check audit-log entries
and credit-balance changes by querying the existing audit and ledger APIs.

Conventions:
  - Usernames are namespaced with the prefix `mr_` ("missing routes") so they
    do not collide with usernames used by the rest of the API_tests suite,
    which shares the session-scoped `client` and SQLite database.
  - Helpers (`_credit`, `_verify`, `_create_completed_session`) match the
    style used elsewhere in the suite (see test_matching_api.py).
"""

import io
from datetime import datetime, timezone, timedelta

from API_tests.conftest import register_and_login


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _credit(client, admin_headers, uid, amount=500.0):
    """Top-up a user's balance via the admin credit endpoint."""
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount,
                      'description': 'mr-test top-up'})


def _verify(client, admin_headers, headers):
    """Submit + admin-approve a verification document for `headers`."""
    pdf = b'%PDF-1.4 mr-test'
    submit = client.post(
        '/api/verification/submit',
        headers={k: v for k, v in headers.items() if k.lower() != 'content-type'},
        data={'document_type': 'passport',
              'document': (io.BytesIO(pdf), 'doc.pdf', 'application/pdf')},
        content_type='multipart/form-data',
    )
    if submit.status_code != 201:
        # already verified by an earlier test in this session — fine.
        return None
    vid = submit.get_json()['verification_id']
    client.put(f'/api/verification/{vid}/review', headers=admin_headers,
               json={'decision': 'verified', 'notes': 'mr-auto'})
    return vid


def _open_violation(client, reporter_h, target_uid,
                    violation_type='spam', severity='low',
                    description='mr-test report'):
    """Report a violation and return its id."""
    resp = client.post('/api/reputation/violations', headers=reporter_h,
                       json={'user_id': target_uid,
                             'violation_type': violation_type,
                             'severity': severity,
                             'description': description})
    assert resp.status_code == 201, resp.data
    return resp.get_json()['violation_id']


def _create_completed_session(client, admin_headers, h1, uid1, h2, uid2,
                              description='mr-test session'):
    """End-to-end flow: pending -> active -> completed.  Returns session_id."""
    _credit(client, admin_headers, uid1)
    _credit(client, admin_headers, uid2)
    _verify(client, admin_headers, h1)
    _verify(client, admin_headers, h2)

    cr = client.post('/api/matching/sessions', headers=h1,
                     json={'participant_id': uid2,
                           'description': description, 'credit_amount': 0})
    assert cr.status_code == 201, cr.data
    sid = cr.get_json()['session_id']
    # participant accepts (pending -> active)
    r1 = client.put(f'/api/matching/sessions/{sid}', headers=h2,
                    json={'status': 'active'})
    assert r1.status_code == 200, r1.data
    # initiator completes
    r2 = client.put(f'/api/matching/sessions/{sid}', headers=h1,
                    json={'status': 'completed'})
    assert r2.status_code == 200, r2.data
    return sid


def _balance(client, admin_headers, uid):
    """Read the absolute credit balance for a user via admin lookup."""
    r = client.get(f'/api/ledger/balance?user_id={uid}', headers=admin_headers)
    return r.get_json()['balance']


def _audit_action_count(client, admin_headers, action):
    """Return how many audit-log entries match `action` substring."""
    r = client.get(f'/api/audit/logs?action={action}&per_page=200',
                   headers=admin_headers)
    if r.status_code != 200:
        return 0
    return r.get_json().get('total', 0)


# ===========================================================================
# 1. Admin Module — /api/admin
# ===========================================================================

class TestAdminMuteUnmute:
    def test_mute_user_sets_muted_until(self, client, admin_headers):
        h, uid = register_and_login(client, 'mr_mute_t1', 'mr_mute_t1@test.com')
        until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        before = _audit_action_count(client, admin_headers, 'USER_MUTED')

        resp = client.put(f'/api/admin/users/{uid}/mute',
                          headers=admin_headers,
                          json={'muted_until': until,
                                'reason': 'noisy'})
        assert resp.status_code == 200
        assert resp.get_json() == {'message': 'User muted.'}

        detail = client.get(f'/api/admin/users/{uid}',
                            headers=admin_headers).get_json()
        assert detail['muted_until'] is not None
        # Audit-log row appended
        after = _audit_action_count(client, admin_headers, 'USER_MUTED')
        assert after == before + 1

    def test_mute_requires_muted_until(self, client, admin_headers):
        h, uid = register_and_login(client, 'mr_mute_t2', 'mr_mute_t2@test.com')
        resp = client.put(f'/api/admin/users/{uid}/mute',
                          headers=admin_headers,
                          json={'reason': 'no until field'})
        assert resp.status_code == 400
        assert 'muted_until' in resp.get_json()['error']

    def test_mute_non_admin_forbidden(self, client, user_headers,
                                     user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.put(f'/api/admin/users/{uid2}/mute',
                          headers=user_headers,
                          json={'muted_until': until})
        assert resp.status_code == 403

    def test_mute_unauthenticated_rejected(self, client):
        # Cookie may still be set by a session-scoped login fixture; ensure a
        # truly unauthenticated request before asserting 401.
        client.delete_cookie('pex_session', path='/', domain='localhost')
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.put('/api/admin/users/1/mute',
                          json={'muted_until': until})
        assert resp.status_code == 401

    def test_unmute_clears_muted_until(self, client, admin_headers):
        h, uid = register_and_login(client, 'mr_mute_t3', 'mr_mute_t3@test.com')
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client.put(f'/api/admin/users/{uid}/mute', headers=admin_headers,
                   json={'muted_until': until})

        before = _audit_action_count(client, admin_headers, 'USER_UNMUTED')
        resp = client.put(f'/api/admin/users/{uid}/unmute',
                          headers=admin_headers, json={})
        assert resp.status_code == 200
        assert resp.get_json() == {'message': 'User unmuted.'}

        detail = client.get(f'/api/admin/users/{uid}',
                            headers=admin_headers).get_json()
        assert detail['muted_until'] is None
        after = _audit_action_count(client, admin_headers, 'USER_UNMUTED')
        assert after == before + 1

    def test_unmute_non_admin_forbidden(self, client, user_headers,
                                       user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.put(f'/api/admin/users/{uid2}/unmute',
                          headers=user_headers, json={})
        assert resp.status_code == 403


class TestAdminAnalytics:
    def test_analytics_kpi_summary(self, client, admin_headers):
        resp = client.get('/api/admin/analytics', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        # Schema: contract enforced by admin_service.get_analytics
        for top_key in ('users', 'sessions', 'ledger',
                        'moderation', 'reputation'):
            assert top_key in data, f'missing top-level key {top_key}'
        assert 'total' in data['users']
        assert 'active' in data['users']
        assert 'by_role' in data['users']
        assert 'by_status' in data['sessions']
        assert 'total_entries' in data['ledger']
        for sub in ('pending_verifications', 'open_violations',
                    'pending_appeals'):
            assert sub in data['moderation']

    def test_analytics_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/admin/analytics', headers=user_headers)
        assert resp.status_code == 403

    def test_analytics_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/admin/analytics')
        assert resp.status_code == 401


class TestAdminViolationDetail:
    def test_get_violation_returns_full_detail(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_vd_r1', 'mr_vd_r1@test.com')
        _, uid2 = register_and_login(client, 'mr_vd_t1', 'mr_vd_t1@test.com')
        vid = _open_violation(client, h1, uid2,
                              violation_type='harassment',
                              severity='medium',
                              description='detail check')

        resp = client.get(f'/api/admin/violations/{vid}',
                          headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == vid
        assert data['target_username'] == 'mr_vd_t1'
        assert data['reporter_username'] == 'mr_vd_r1'
        assert data['severity'] == 'medium'
        # Embedded appeal slot is None until one is filed
        assert 'appeal' in data

    def test_get_violation_404(self, client, admin_headers):
        resp = client.get('/api/admin/violations/9999999',
                          headers=admin_headers)
        assert resp.status_code == 404

    def test_get_violation_non_admin_forbidden(self, client, user_headers):
        # The endpoint requires admin/auditor regardless of whether the id exists
        resp = client.get('/api/admin/violations/1', headers=user_headers)
        assert resp.status_code == 403


class TestAdminEscalateViolation:
    def test_escalate_changes_severity_and_audits(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_esc_r1', 'mr_esc_r1@test.com')
        _, uid2 = register_and_login(client, 'mr_esc_t1', 'mr_esc_t1@test.com')
        vid = _open_violation(client, h1, uid2,
                              violation_type='abuse', severity='low',
                              description='escalation candidate')

        before = _audit_action_count(client, admin_headers,
                                     'VIOLATION_ESCALATED')
        resp = client.put(f'/api/admin/violations/{vid}/escalate',
                          headers=admin_headers,
                          json={'severity': 'high',
                                'reason': 'pattern of repeat reports'})
        assert resp.status_code == 200
        assert resp.get_json() == {'message': 'Violation severity updated.'}

        # Severity persisted on the violation row
        detail = client.get(f'/api/admin/violations/{vid}',
                            headers=admin_headers).get_json()
        assert detail['severity'] == 'high'

        after = _audit_action_count(client, admin_headers,
                                    'VIOLATION_ESCALATED')
        assert after == before + 1

    def test_escalate_invalid_severity_rejected(self, client, admin_headers):
        h1, _ = register_and_login(client, 'mr_esc_r2', 'mr_esc_r2@test.com')
        _, uid2 = register_and_login(client, 'mr_esc_t2', 'mr_esc_t2@test.com')
        vid = _open_violation(client, h1, uid2,
                              violation_type='spam', severity='low')

        resp = client.put(f'/api/admin/violations/{vid}/escalate',
                          headers=admin_headers,
                          json={'severity': 'CRITICAL', 'reason': 'x'})
        assert resp.status_code == 400

    def test_escalate_requires_reason(self, client, admin_headers):
        h1, _ = register_and_login(client, 'mr_esc_r3', 'mr_esc_r3@test.com')
        _, uid2 = register_and_login(client, 'mr_esc_t3', 'mr_esc_t3@test.com')
        vid = _open_violation(client, h1, uid2,
                              violation_type='spam', severity='low')
        resp = client.put(f'/api/admin/violations/{vid}/escalate',
                          headers=admin_headers,
                          json={'severity': 'medium', 'reason': '   '})
        assert resp.status_code == 400

    def test_escalate_already_at_target_severity(self, client, admin_headers):
        h1, _ = register_and_login(client, 'mr_esc_r4', 'mr_esc_r4@test.com')
        _, uid2 = register_and_login(client, 'mr_esc_t4', 'mr_esc_t4@test.com')
        vid = _open_violation(client, h1, uid2,
                              violation_type='spam', severity='medium')
        resp = client.put(f'/api/admin/violations/{vid}/escalate',
                          headers=admin_headers,
                          json={'severity': 'medium', 'reason': 'noop'})
        assert resp.status_code == 400

    def test_escalate_non_admin_forbidden(self, client, user_headers):
        resp = client.put('/api/admin/violations/1/escalate',
                          headers=user_headers,
                          json={'severity': 'high', 'reason': 'x'})
        assert resp.status_code == 403


class TestAdminAppeals:
    def test_list_appeals_admin_returns_pending_and_resolved(self, client,
                                                              admin_headers):
        # File an appeal so the list cannot be empty.
        h_target, uid_target = register_and_login(
            client, 'mr_ap_t1', 'mr_ap_t1@test.com')
        h_reporter, _ = register_and_login(
            client, 'mr_ap_r1', 'mr_ap_r1@test.com')
        vid = _open_violation(client, h_reporter, uid_target,
                              violation_type='spam', severity='low')
        ar = client.post(f'/api/reputation/violations/{vid}/appeal',
                         headers=h_target,
                         json={'reason': 'this report is incorrect'})
        assert ar.status_code == 201

        resp = client.get('/api/admin/appeals', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'appeals' in data
        assert 'total' in data
        assert data['total'] >= 1
        # Each row carries the joined target/violation columns
        sample = data['appeals'][0]
        assert 'violation_id' in sample
        assert 'status' in sample

    def test_list_appeals_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/admin/appeals', headers=user_headers)
        assert resp.status_code == 403

    def test_resolve_appeal_upheld(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_ap_t2', 'mr_ap_t2@test.com')
        h_reporter, _ = register_and_login(
            client, 'mr_ap_r2', 'mr_ap_r2@test.com')
        vid = _open_violation(client, h_reporter, uid_target,
                              violation_type='spam', severity='low')
        ar = client.post(f'/api/reputation/violations/{vid}/appeal',
                         headers=h_target,
                         json={'reason': 'mistake'})
        appeal_id = ar.get_json()['appeal_id']

        before = _audit_action_count(client, admin_headers, 'APPEAL_UPHELD')
        resp = client.put(f'/api/admin/appeals/{appeal_id}/resolve',
                          headers=admin_headers,
                          json={'decision': 'upheld', 'notes': 'mr-test'})
        assert resp.status_code == 200
        assert resp.get_json() == {'message': 'Appeal resolved.'}
        after = _audit_action_count(client, admin_headers, 'APPEAL_UPHELD')
        assert after == before + 1

    def test_resolve_appeal_denied(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_ap_t3', 'mr_ap_t3@test.com')
        h_reporter, _ = register_and_login(
            client, 'mr_ap_r3', 'mr_ap_r3@test.com')
        vid = _open_violation(client, h_reporter, uid_target,
                              violation_type='spam', severity='low')
        ar = client.post(f'/api/reputation/violations/{vid}/appeal',
                         headers=h_target,
                         json={'reason': 'protest'})
        appeal_id = ar.get_json()['appeal_id']

        resp = client.put(f'/api/admin/appeals/{appeal_id}/resolve',
                          headers=admin_headers,
                          json={'decision': 'denied', 'notes': 'no merit'})
        assert resp.status_code == 200

    def test_resolve_appeal_invalid_decision(self, client, admin_headers):
        # The appeal id need not exist — invalid decision is rejected first.
        resp = client.put('/api/admin/appeals/1/resolve',
                          headers=admin_headers,
                          json={'decision': 'maybe'})
        assert resp.status_code == 400

    def test_resolve_appeal_non_admin_forbidden(self, client, user_headers):
        resp = client.put('/api/admin/appeals/1/resolve',
                          headers=user_headers,
                          json={'decision': 'upheld'})
        assert resp.status_code == 403


# ===========================================================================
# 2. Verification Module — /api/verification
# ===========================================================================

class TestVerificationStatus:
    def test_status_for_new_user_is_not_submitted(self, client, admin_headers):
        h, _ = register_and_login(client, 'mr_vs_new', 'mr_vs_new@test.com')
        resp = client.get('/api/verification/status', headers=h)
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'not_submitted'}

    def test_status_after_pending_submission(self, client, admin_headers):
        h, _ = register_and_login(client, 'mr_vs_pend', 'mr_vs_pend@test.com')
        # Submit but do NOT approve
        client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'passport',
                          'document': (io.BytesIO(b'%PDF-1.4 mr'),
                                       'd.pdf', 'application/pdf')},
                    content_type='multipart/form-data')

        resp = client.get('/api/verification/status', headers=h)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['verification']['status'] == 'pending'
        # Document type masked, ciphertext never present
        assert body['verification']['document_type'].startswith('[')
        assert 'document_data_enc' not in body['verification']

    def test_status_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/verification/status')
        assert resp.status_code == 401


class TestVerificationListQueue:
    def test_admin_can_list_queue(self, client, admin_headers):
        # Make sure there's at least one pending row
        h, _ = register_and_login(client, 'mr_vq_p', 'mr_vq_p@test.com')
        client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'national_id',
                          'document': (io.BytesIO(b'%PDF-1.4 mr'),
                                       'd.pdf', 'application/pdf')},
                    content_type='multipart/form-data')

        resp = client.get('/api/verification', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'verifications' in data
        assert 'total' in data
        assert data['total'] >= 1
        # No ciphertext leaked, doc_type masked
        for row in data['verifications']:
            assert 'document_data_enc' not in row
            assert row['document_type'].startswith('[')

    def test_list_queue_writes_audit_log(self, client, admin_headers):
        before = _audit_action_count(client, admin_headers,
                                     'VERIFICATION_LIST_ACCESSED')
        client.get('/api/verification', headers=admin_headers)
        after = _audit_action_count(client, admin_headers,
                                    'VERIFICATION_LIST_ACCESSED')
        assert after == before + 1

    def test_list_queue_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/verification', headers=user_headers)
        assert resp.status_code == 403

    def test_list_queue_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/verification')
        assert resp.status_code == 401


class TestVerificationGetDocument:
    def test_admin_can_fetch_decrypted_document(self, client, admin_headers):
        h, _ = register_and_login(client, 'mr_vdoc_u', 'mr_vdoc_u@test.com')
        pdf = b'%PDF-1.4 mr-doc-payload'
        sub = client.post('/api/verification/submit',
                          headers={k: v for k, v in h.items()
                                   if k.lower() != 'content-type'},
                          data={'document_type': 'passport',
                                'document': (io.BytesIO(pdf),
                                             'd.pdf', 'application/pdf')},
                          content_type='multipart/form-data')
        vid = sub.get_json()['verification_id']

        before = _audit_action_count(client, admin_headers,
                                     'VERIFICATION_DOCUMENT_ACCESSED')
        resp = client.get(f'/api/verification/{vid}/document',
                          headers=admin_headers)
        assert resp.status_code == 200
        # MIME preserved, raw bytes returned (not the ciphertext)
        assert resp.content_type.startswith('application/pdf')
        assert resp.data == pdf
        # Inline disposition lets the admin UI render it
        assert 'inline' in resp.headers.get('Content-Disposition', '')

        after = _audit_action_count(client, admin_headers,
                                    'VERIFICATION_DOCUMENT_ACCESSED')
        assert after == before + 1

    def test_get_document_404(self, client, admin_headers):
        # Audit log is written before the 404 — verify both happen.
        before = _audit_action_count(client, admin_headers,
                                     'VERIFICATION_DOCUMENT_ACCESSED')
        resp = client.get('/api/verification/9999999/document',
                          headers=admin_headers)
        assert resp.status_code == 404
        after = _audit_action_count(client, admin_headers,
                                    'VERIFICATION_DOCUMENT_ACCESSED')
        assert after == before + 1

    def test_get_document_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/verification/1/document', headers=user_headers)
        assert resp.status_code == 403


# ===========================================================================
# 3. Reputation Module — /api/reputation
# ===========================================================================

class TestReputationRate:
    def test_rate_completed_session_succeeds(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_rate_a', 'mr_rate_a@test.com')
        h2, uid2 = register_and_login(client, 'mr_rate_b', 'mr_rate_b@test.com')
        sid = _create_completed_session(client, admin_headers,
                                        h1, uid1, h2, uid2)

        before = _audit_action_count(client, admin_headers, 'RATING_SUBMITTED')
        resp = client.post('/api/reputation/rate', headers=h1,
                           json={'session_id': sid, 'score': 5,
                                 'comment': 'great peer'})
        assert resp.status_code == 201
        assert resp.get_json() == {'message': 'Rating submitted.'}
        after = _audit_action_count(client, admin_headers, 'RATING_SUBMITTED')
        assert after == before + 1

    def test_rate_score_out_of_range(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_rate_a2', 'mr_rate_a2@test.com')
        h2, uid2 = register_and_login(client, 'mr_rate_b2', 'mr_rate_b2@test.com')
        sid = _create_completed_session(client, admin_headers,
                                        h1, uid1, h2, uid2)
        resp = client.post('/api/reputation/rate', headers=h1,
                           json={'session_id': sid, 'score': 9})
        assert resp.status_code == 400

    def test_rate_non_completed_session_rejected(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_rate_a3', 'mr_rate_a3@test.com')
        h2, uid2 = register_and_login(client, 'mr_rate_b3', 'mr_rate_b3@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'still pending',
                               'credit_amount': 0})
        sid = cr.get_json()['session_id']

        # Session is still 'pending' — cannot rate
        resp = client.post('/api/reputation/rate', headers=h1,
                           json={'session_id': sid, 'score': 4})
        assert resp.status_code == 400

    def test_rate_non_participant_rejected(self, client, admin_headers,
                                            user_headers):
        h1, uid1 = register_and_login(client, 'mr_rate_a4', 'mr_rate_a4@test.com')
        h2, uid2 = register_and_login(client, 'mr_rate_b4', 'mr_rate_b4@test.com')
        sid = _create_completed_session(client, admin_headers,
                                        h1, uid1, h2, uid2)
        # Outsider tries to rate
        resp = client.post('/api/reputation/rate', headers=user_headers,
                           json={'session_id': sid, 'score': 5})
        assert resp.status_code == 403

    def test_rate_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.post('/api/reputation/rate',
                           json={'session_id': 1, 'score': 4})
        assert resp.status_code == 401


class TestReputationListRatings:
    def test_list_ratings_for_user(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_lr_a', 'mr_lr_a@test.com')
        h2, uid2 = register_and_login(client, 'mr_lr_b', 'mr_lr_b@test.com')
        sid = _create_completed_session(client, admin_headers,
                                        h1, uid1, h2, uid2)
        client.post('/api/reputation/rate', headers=h1,
                    json={'session_id': sid, 'score': 4, 'comment': 'good'})

        # uid2 was rated by uid1
        resp = client.get(f'/api/reputation/ratings/{uid2}', headers=h2)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'ratings' in data and 'total' in data
        assert data['total'] >= 1
        sample = data['ratings'][0]
        assert sample['score'] == 4
        assert sample['rater_name'] == 'mr_lr_a'

    def test_list_ratings_unknown_user_returns_empty(self, client,
                                                      user_headers):
        resp = client.get('/api/reputation/ratings/9999999',
                          headers=user_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] == 0

    def test_list_ratings_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/reputation/ratings/1')
        assert resp.status_code == 401


class TestReputationScore:
    def test_score_returns_full_breakdown(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_sc_a', 'mr_sc_a@test.com')
        resp = client.get(f'/api/reputation/score/{uid1}', headers=h1)
        assert resp.status_code == 200
        data = resp.get_json()
        # Schema contract from rating_service.get_reputation_score
        for k in ('user_id', 'average_rating', 'total_ratings',
                  'positive_ratings', 'sessions_total',
                  'sessions_completed', 'sessions_cancelled',
                  'close_rate', 'cancellation_rate', 'dispute_rate',
                  'resolved_violations', 'reputation_score'):
            assert k in data, f'missing key {k}'
        assert data['user_id'] == uid1
        assert 0.0 <= data['reputation_score'] <= 100.0

    def test_score_after_rating_reflects_average(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_sc_a2', 'mr_sc_a2@test.com')
        h2, uid2 = register_and_login(client, 'mr_sc_b2', 'mr_sc_b2@test.com')
        sid = _create_completed_session(client, admin_headers,
                                        h1, uid1, h2, uid2)
        client.post('/api/reputation/rate', headers=h1,
                    json={'session_id': sid, 'score': 5})
        data = client.get(f'/api/reputation/score/{uid2}',
                          headers=h2).get_json()
        assert data['total_ratings'] >= 1
        assert data['average_rating'] >= 1.0  # at least the one 5-star

    def test_score_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/reputation/score/1')
        assert resp.status_code == 401


class TestReputationViolations:
    """A user listing violations sees only their own (those filed AGAINST them)."""

    def test_user_sees_violations_filed_against_them(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_rv_t1', 'mr_rv_t1@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_rv_r1', 'mr_rv_r1@test.com')
        _open_violation(client, h_rep, uid_target,
                        violation_type='spam', severity='low')
        resp = client.get('/api/reputation/violations', headers=h_target)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1
        assert all(v['user_id'] == uid_target for v in data['violations'])

    def test_user_does_not_see_other_users_violations(self, client,
                                                       admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_rv_t2', 'mr_rv_t2@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_rv_r2', 'mr_rv_r2@test.com')
        h_other, _ = register_and_login(
            client, 'mr_rv_o2', 'mr_rv_o2@test.com')
        _open_violation(client, h_rep, uid_target,
                        violation_type='spam', severity='low')

        resp = client.get('/api/reputation/violations', headers=h_other)
        # `other` may have zero — that's enough; they must not see uid_target's
        for v in resp.get_json()['violations']:
            assert v['user_id'] != uid_target

    def test_violations_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/reputation/violations')
        assert resp.status_code == 401


class TestReputationFileAppeal:
    def test_file_appeal_happy_path(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_fa_t1', 'mr_fa_t1@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_fa_r1', 'mr_fa_r1@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')

        before = _audit_action_count(client, admin_headers, 'APPEAL_FILED')
        resp = client.post(f'/api/reputation/violations/{vid}/appeal',
                           headers=h_target,
                           json={'reason': 'I did not do this'})
        assert resp.status_code == 201
        assert 'appeal_id' in resp.get_json()
        after = _audit_action_count(client, admin_headers, 'APPEAL_FILED')
        assert after == before + 1

    def test_file_appeal_requires_reason(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_fa_t2', 'mr_fa_t2@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_fa_r2', 'mr_fa_r2@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')
        resp = client.post(f'/api/reputation/violations/{vid}/appeal',
                           headers=h_target, json={'reason': '   '})
        assert resp.status_code == 400

    def test_file_appeal_only_by_target(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_fa_t3', 'mr_fa_t3@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_fa_r3', 'mr_fa_r3@test.com')
        h_other, _ = register_and_login(
            client, 'mr_fa_o3', 'mr_fa_o3@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')
        # Outsider tries to appeal someone else's violation
        resp = client.post(f'/api/reputation/violations/{vid}/appeal',
                           headers=h_other, json={'reason': 'not mine'})
        assert resp.status_code == 403

    def test_duplicate_appeal_rejected(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_fa_t4', 'mr_fa_t4@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_fa_r4', 'mr_fa_r4@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')
        client.post(f'/api/reputation/violations/{vid}/appeal',
                    headers=h_target, json={'reason': 'first'})
        dup = client.post(f'/api/reputation/violations/{vid}/appeal',
                          headers=h_target, json={'reason': 'second'})
        assert dup.status_code == 400


class TestReputationAppealsAdminListing:
    """
    NOTE: /api/reputation/appeals is decorated `@admin_required`, so it
    actually returns the *system-wide* list of appeals — not "the user's own
    submitted appeals".  These tests assert that contract.
    """

    def test_admin_lists_appeals(self, client, admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_ral_t', 'mr_ral_t@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_ral_r', 'mr_ral_r@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')
        client.post(f'/api/reputation/violations/{vid}/appeal',
                    headers=h_target, json={'reason': 'mistake'})
        resp = client.get('/api/reputation/appeals', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1

    def test_user_cannot_list_via_reputation_blueprint(self, client,
                                                       user_headers):
        resp = client.get('/api/reputation/appeals', headers=user_headers)
        assert resp.status_code == 403


class TestReputationResolveAppeal:
    def test_admin_resolves_via_reputation_blueprint(self, client,
                                                     admin_headers):
        h_target, uid_target = register_and_login(
            client, 'mr_rra_t1', 'mr_rra_t1@test.com')
        h_rep, _ = register_and_login(
            client, 'mr_rra_r1', 'mr_rra_r1@test.com')
        vid = _open_violation(client, h_rep, uid_target,
                              violation_type='spam', severity='low')
        ar = client.post(f'/api/reputation/violations/{vid}/appeal',
                         headers=h_target, json={'reason': 'unjust'})
        appeal_id = ar.get_json()['appeal_id']

        resp = client.put(f'/api/reputation/appeals/{appeal_id}/resolve',
                          headers=admin_headers,
                          json={'decision': 'upheld', 'notes': 'agreed'})
        assert resp.status_code == 200
        assert resp.get_json() == {'message': 'Appeal resolved.'}

    def test_invalid_decision_rejected(self, client, admin_headers):
        resp = client.put('/api/reputation/appeals/1/resolve',
                          headers=admin_headers,
                          json={'decision': 'bogus'})
        assert resp.status_code == 400

    def test_resolve_non_admin_forbidden(self, client, user_headers):
        resp = client.put('/api/reputation/appeals/1/resolve',
                          headers=user_headers,
                          json={'decision': 'upheld'})
        assert resp.status_code == 403


# ===========================================================================
# 4. Ledger Module — /api/ledger
# ===========================================================================

class TestLedgerListAll:
    def test_user_sees_own_entries_only(self, client, admin_headers):
        h, uid = register_and_login(client, 'mr_lg_u1', 'mr_lg_u1@test.com')
        # Create a ledger entry by topping the user up
        _credit(client, admin_headers, uid, amount=42.0)
        resp = client.get('/api/ledger', headers=h)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data and 'total' in data
        # Non-privileged response excludes user_id column
        assert data['total'] >= 1
        for e in data['entries']:
            assert 'user_id' not in e
            assert 'transaction_type' in e
            assert 'amount' in e

    def test_admin_sees_all_entries_with_username(self, client, admin_headers):
        resp = client.get('/api/ledger', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        if data['entries']:
            sample = data['entries'][0]
            assert 'username' in sample
            assert 'user_id' in sample

    def test_admin_can_filter_by_user_id(self, client, admin_headers):
        h, uid = register_and_login(client, 'mr_lg_u2', 'mr_lg_u2@test.com')
        _credit(client, admin_headers, uid, amount=15.0)
        resp = client.get(f'/api/ledger?user_id={uid}',
                          headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1
        for e in data['entries']:
            assert e['user_id'] == uid

    def test_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/ledger')
        assert resp.status_code == 401


class TestLedgerRefundInvoice:
    def test_refund_paid_invoice_updates_balances(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_rfd_i1', 'mr_rfd_i1@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_rfd_p1', 'mr_rfd_p1@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)

        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 100.0,
                               'notes': 'mr-refund'})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)

        pay_after_pay = _balance(client, admin_headers, uid_pay)
        iss_after_pay = _balance(client, admin_headers, uid_iss)

        before_audit = _audit_action_count(client, admin_headers,
                                           'INVOICE_REFUNDED')
        resp = client.post(f'/api/ledger/invoices/{inv_id}/refund',
                           headers=admin_headers,
                           json={'amount': 40.0,
                                 'reason': 'partial refund'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['refund_amount'] == 40.0
        assert body['amount_paid'] == 60.0
        # Balances move 40.0 from issuer back to payer.
        assert _balance(client, admin_headers, uid_pay) == pay_after_pay + 40.0
        assert _balance(client, admin_headers, uid_iss) == iss_after_pay - 40.0
        # Audit log written
        after_audit = _audit_action_count(client, admin_headers,
                                          'INVOICE_REFUNDED')
        assert after_audit == before_audit + 1

    def test_refund_requires_reason(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_rfd_i2', 'mr_rfd_i2@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_rfd_p2', 'mr_rfd_p2@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 10.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)

        resp = client.post(f'/api/ledger/invoices/{inv_id}/refund',
                           headers=admin_headers,
                           json={'amount': 5.0, 'reason': '   '})
        assert resp.status_code == 400

    def test_refund_unpaid_invoice_rejected(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_rfd_i3', 'mr_rfd_i3@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_rfd_p3', 'mr_rfd_p3@test.com')
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 10.0})
        inv_id = cr.get_json()['id']
        # Not paid — refund should be rejected
        resp = client.post(f'/api/ledger/invoices/{inv_id}/refund',
                           headers=admin_headers,
                           json={'amount': 5.0, 'reason': 'no'})
        assert resp.status_code == 400

    def test_refund_amount_greater_than_paid_rejected(self, client,
                                                       admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_rfd_i4', 'mr_rfd_i4@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_rfd_p4', 'mr_rfd_p4@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 20.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)
        resp = client.post(f'/api/ledger/invoices/{inv_id}/refund',
                           headers=admin_headers,
                           json={'amount': 999.0, 'reason': 'over'})
        assert resp.status_code == 400

    def test_refund_non_admin_forbidden(self, client, user_headers):
        resp = client.post('/api/ledger/invoices/1/refund',
                           headers=user_headers,
                           json={'amount': 5.0, 'reason': 'x'})
        assert resp.status_code == 403


class TestLedgerAdjustInvoice:
    def test_positive_delta_charges_payer(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_adj_i1', 'mr_adj_i1@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_adj_p1', 'mr_adj_p1@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 50.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)

        pay_before = _balance(client, admin_headers, uid_pay)
        iss_before = _balance(client, admin_headers, uid_iss)
        before_audit = _audit_action_count(client, admin_headers,
                                           'INVOICE_ADJUSTED')

        resp = client.post(f'/api/ledger/invoices/{inv_id}/adjust',
                           headers=admin_headers,
                           json={'delta': 7.0, 'reason': 'late fee'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['delta'] == 7.0
        # 7.0 moves payer -> issuer
        assert _balance(client, admin_headers, uid_pay) == pay_before - 7.0
        assert _balance(client, admin_headers, uid_iss) == iss_before + 7.0
        after_audit = _audit_action_count(client, admin_headers,
                                          'INVOICE_ADJUSTED')
        assert after_audit == before_audit + 1

    def test_negative_delta_credits_payer(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_adj_i2', 'mr_adj_i2@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_adj_p2', 'mr_adj_p2@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 30.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)

        pay_before = _balance(client, admin_headers, uid_pay)
        iss_before = _balance(client, admin_headers, uid_iss)

        resp = client.post(f'/api/ledger/invoices/{inv_id}/adjust',
                           headers=admin_headers,
                           json={'delta': -5.0, 'reason': 'goodwill'})
        assert resp.status_code == 200
        # 5.0 moves issuer -> payer
        assert _balance(client, admin_headers, uid_pay) == pay_before + 5.0
        assert _balance(client, admin_headers, uid_iss) == iss_before - 5.0

    def test_zero_delta_rejected(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_adj_i3', 'mr_adj_i3@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_adj_p3', 'mr_adj_p3@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 10.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)
        resp = client.post(f'/api/ledger/invoices/{inv_id}/adjust',
                           headers=admin_headers,
                           json={'delta': 0, 'reason': 'noop'})
        assert resp.status_code == 400

    def test_adjust_requires_reason(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_adj_i4', 'mr_adj_i4@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_adj_p4', 'mr_adj_p4@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 10.0})
        inv_id = cr.get_json()['id']
        client.post(f'/api/ledger/invoices/{inv_id}/pay', headers=h_pay)
        resp = client.post(f'/api/ledger/invoices/{inv_id}/adjust',
                           headers=admin_headers,
                           json={'delta': 1.0, 'reason': ''})
        assert resp.status_code == 400

    def test_adjust_unpaid_invoice_rejected(self, client, admin_headers):
        h_iss, uid_iss = register_and_login(
            client, 'mr_adj_i5', 'mr_adj_i5@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_adj_p5', 'mr_adj_p5@test.com')
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 10.0})
        inv_id = cr.get_json()['id']
        resp = client.post(f'/api/ledger/invoices/{inv_id}/adjust',
                           headers=admin_headers,
                           json={'delta': 1.0, 'reason': 'x'})
        assert resp.status_code == 400

    def test_adjust_non_admin_forbidden(self, client, user_headers):
        resp = client.post('/api/ledger/invoices/1/adjust',
                           headers=user_headers,
                           json={'delta': 1.0, 'reason': 'x'})
        assert resp.status_code == 403


class TestLedgerMarkOverdue:
    def test_mark_overdue_returns_count(self, client, admin_headers):
        resp = client.post('/api/ledger/invoices/mark-overdue',
                           headers=admin_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'count' in body
        assert isinstance(body['count'], int)
        assert 'message' in body

    def test_mark_overdue_picks_up_past_due_invoice(self, client,
                                                    admin_headers):
        # Create an invoice and back-date its due_date via direct DB write
        from app.models import db as _db

        h_iss, uid_iss = register_and_login(
            client, 'mr_mo_i1', 'mr_mo_i1@test.com')
        h_pay, uid_pay = register_and_login(
            client, 'mr_mo_p1', 'mr_mo_p1@test.com')
        _credit(client, admin_headers, uid_iss)
        _credit(client, admin_headers, uid_pay)
        cr = client.post('/api/ledger/invoices', headers=h_iss,
                         json={'payer_id': uid_pay, 'amount': 5.0,
                               'due_days': 1})
        inv_id = cr.get_json()['id']

        # Force due_date into the past
        with _db() as conn:
            conn.execute(
                'UPDATE invoices SET due_date = ? WHERE id = ?',
                ('2000-01-01', inv_id),
            )

        resp = client.post('/api/ledger/invoices/mark-overdue',
                           headers=admin_headers)
        assert resp.status_code == 200
        # Invoice now has 'overdue' status
        inv = client.get(f'/api/ledger/invoices/{inv_id}',
                         headers=admin_headers).get_json()
        assert inv['status'] == 'overdue'

    def test_mark_overdue_non_admin_forbidden(self, client, user_headers):
        resp = client.post('/api/ledger/invoices/mark-overdue',
                           headers=user_headers)
        assert resp.status_code == 403

    def test_mark_overdue_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.post('/api/ledger/invoices/mark-overdue')
        assert resp.status_code == 401


# ===========================================================================
# 5. Matching Module — /api/matching
# ===========================================================================

class TestMatchingGetSession:
    def test_initiator_can_fetch_session(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_gs_a', 'mr_gs_a@test.com')
        h2, uid2 = register_and_login(client, 'mr_gs_b', 'mr_gs_b@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'mr-get-session',
                               'credit_amount': 0,
                               'duration_minutes': 30})
        sid = cr.get_json()['session_id']

        resp = client.get(f'/api/matching/sessions/{sid}', headers=h1)
        assert resp.status_code == 200
        s = resp.get_json()['session']
        assert s['id'] == sid
        assert s['initiator_id'] == uid1
        assert s['participant_id'] == uid2
        assert s['status'] == 'pending'
        # idempotency_key is sensitive — must not be exposed
        assert 'idempotency_key' not in s

    def test_admin_can_fetch_any_session(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_gs_a2', 'mr_gs_a2@test.com')
        h2, uid2 = register_and_login(client, 'mr_gs_b2', 'mr_gs_b2@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'mr-admin-fetch',
                               'credit_amount': 0})
        sid = cr.get_json()['session_id']

        resp = client.get(f'/api/matching/sessions/{sid}',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_third_party_forbidden(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'mr_gs_a3', 'mr_gs_a3@test.com')
        h2, uid2 = register_and_login(client, 'mr_gs_b3', 'mr_gs_b3@test.com')
        h3, _ = register_and_login(client, 'mr_gs_c3', 'mr_gs_c3@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'mr-rbac',
                               'credit_amount': 0})
        sid = cr.get_json()['session_id']
        resp = client.get(f'/api/matching/sessions/{sid}', headers=h3)
        assert resp.status_code == 403

    def test_get_session_404(self, client, user_headers):
        resp = client.get('/api/matching/sessions/9999999',
                          headers=user_headers)
        assert resp.status_code == 404


class TestMatchingMissing:
    """
    The user's task list mentions two routes that do NOT exist on the
    current matching blueprint:
      - DELETE /api/matching/block/expired   (cleanup of expired temp blocks)
      - GET    /api/matching/tags            (skill-matching tags catalog)

    The blueprint at app/routes/matching.py exposes /block (POST/GET),
    /block/temporary (POST), and /block/<int:blocked_id> (DELETE) — but no
    /block/expired and no /tags route at all.

    These tests pin the *current* shape of the API so they fail loudly the
    moment those endpoints are added (which will be the cue to replace these
    assertions with real happy-path coverage):

      - DELETE /api/matching/block/expired today returns 405 because Flask
        first matches the URL prefix of /block (a route with no DELETE
        handler) — i.e. the route is genuinely missing.
      - GET    /api/matching/tags today returns 200 + HTML because Flask's
        `/<path:path>` SPA-fallback at the application factory swallows any
        URL that no blueprint matched.
    """

    def test_block_expired_cleanup_route_not_present(self, client,
                                                     admin_headers):
        resp = client.delete('/api/matching/block/expired',
                             headers=admin_headers)
        # 404 = no route, 405 = path matched a sibling route lacking DELETE.
        # Either way, the handler the user asked for does not exist.
        assert resp.status_code in (404, 405), (
            f'Got {resp.status_code}: a handler for DELETE '
            '/api/matching/block/expired now exists; replace this assertion '
            'with a real happy-path test (cleanup of expired temp blocks).')

    def test_tags_route_not_present(self, client, user_headers):
        resp = client.get('/api/matching/tags', headers=user_headers)
        # The SPA fallback returns 200/HTML for unmatched URLs.
        # If the response ever becomes JSON, the route was implemented.
        assert 'text/html' in resp.content_type, (
            'GET /api/matching/tags now returns a non-HTML response; the '
            'route appears to have been implemented — replace this assertion '
            'with a real happy-path test (list of skill-matching tags).')


# ===========================================================================
# 6. General/Root routes
# ===========================================================================

class TestRootRoutes:
    def test_index_serves_spa_template(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        # The application factory renders templates/index.html for `/`
        # — content type is HTML, not JSON.
        assert 'text/html' in resp.content_type

    def test_spa_fallback_for_arbitrary_path(self, client):
        resp = client.get('/some/deep/client-side/route')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_spa_fallback_for_unknown_top_level(self, client):
        # Anything that isn't an /api/* route falls through to the SPA template
        resp = client.get('/login')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_unknown_api_route_falls_through_to_spa(self, client):
        # Documenting the current behaviour: the catch-all `/<path:path>`
        # registered in the application factory matches *every* URL the
        # blueprints did not handle — including /api/* paths. Unknown API
        # endpoints therefore return the SPA shell, not a JSON 404.
        # If you ever add a JSON 404 for unknown /api/* routes, flip this
        # assertion to status_code == 404 and JSON content-type.
        resp = client.get('/api/this-route-does-not-exist')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
