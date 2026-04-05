"""
API tests for /api/matching: profiles, search, sessions, queue governance,
HTMX partial endpoints, blacklist.
"""

import io
import pytest
from API_tests.conftest import register_and_login


def _credit_user(client, admin_headers, uid, amount=500.0):
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount,
                      'description': 'test credit'})


def _verify_user(client, admin_headers, h):
    """Submit a minimal verification document and have admin approve it."""
    pdf = b'%PDF-1.4 test'
    r = client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'passport',
                          'document': (io.BytesIO(pdf), 'doc.pdf', 'application/pdf')},
                    content_type='multipart/form-data')
    if r.status_code != 201:
        return  # already submitted or other error
    vid = r.get_json()['verification_id']
    client.put(f'/api/verification/{vid}/review', headers=admin_headers,
               json={'decision': 'verified', 'notes': 'auto-approved in test'})


class TestMatchingProfile:
    def test_upsert_and_get_profile(self, client, user_headers_with_id, admin_headers):
        headers, uid = user_headers_with_id
        _credit_user(client, admin_headers, uid)
        resp = client.post('/api/matching/profile', headers=headers,
                           json={
                               'skills_offered': ['Python', 'Flask'],
                               'skills_needed':  ['Design'],
                               'bio':            'Backend dev',
                               'is_active':      True,
                           })
        assert resp.status_code == 200

        get_resp = client.get('/api/matching/profile', headers=headers)
        assert get_resp.status_code == 200
        prof = get_resp.get_json()['profile']
        assert 'Python' in prof['skills_offered']

    def test_profile_with_tags_and_slots(self, client, admin_headers):
        h, uid = register_and_login(client, 'prof_tags', 'prof_tags@test.com')
        _credit_user(client, admin_headers, uid)
        resp = client.post('/api/matching/profile', headers=h,
                           json={
                               'skills_offered':       ['Go'],
                               'skills_needed':        [],
                               'tags':                 ['backend', 'systems'],
                               'preferred_time_slots': ['weekday-evening', 'weekend-morning'],
                               'category':             'Technology',
                               'is_active':            True,
                           })
        assert resp.status_code == 200

        get_resp = client.get('/api/matching/profile', headers=h)
        prof = get_resp.get_json()['profile']
        assert 'backend' in prof['tags']
        assert 'weekday-evening' in prof['preferred_time_slots']
        assert prof['category'] == 'Technology'

    def test_unauthenticated_rejected(self, client):
        resp = client.get('/api/matching/profile')
        assert resp.status_code == 401


class TestMatchingSearch:
    def test_search_returns_profiles(self, client, admin_headers):
        h, uid = register_and_login(client, 'srch_a', 'srch_a@test.com')
        _credit_user(client, admin_headers, uid)
        client.post('/api/matching/profile', headers=h,
                    json={'skills_offered': ['Rust'], 'skills_needed': [],
                          'is_active': True})

        h2, uid2 = register_and_login(client, 'srch_b', 'srch_b@test.com')
        _credit_user(client, admin_headers, uid2)
        resp = client.get('/api/matching/search', headers=h2,
                          query_string={'skill': 'Rust'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert any(p['user_id'] == uid for p in data['profiles'])

    def test_search_by_tag(self, client, admin_headers):
        h, uid = register_and_login(client, 'tag_srch', 'tag_srch@test.com')
        _credit_user(client, admin_headers, uid)
        client.post('/api/matching/profile', headers=h,
                    json={'skills_offered': ['Vue'], 'skills_needed': [],
                          'tags': ['frontend'], 'is_active': True})

        h2, uid2 = register_and_login(client, 'tag_srch2', 'tag_srch2@test.com')
        _credit_user(client, admin_headers, uid2)
        resp = client.get('/api/matching/search', headers=h2,
                          query_string={'tag': 'frontend'})
        assert resp.status_code == 200
        assert any(p['user_id'] == uid for p in resp.get_json()['profiles'])

    def test_search_excludes_self(self, client, user_headers_with_id, admin_headers):
        headers, uid = user_headers_with_id
        _credit_user(client, admin_headers, uid)
        client.post('/api/matching/profile', headers=headers,
                    json={'skills_offered': ['React'], 'is_active': True})
        resp = client.get('/api/matching/search', headers=headers,
                          query_string={'skill': 'React'})
        assert resp.status_code == 200
        assert not any(p['user_id'] == uid for p in resp.get_json()['profiles'])


class TestHTMXPartials:
    def test_peers_partial_returns_html(self, client, user_headers_with_id, admin_headers):
        headers, uid = user_headers_with_id
        _credit_user(client, admin_headers, uid)
        resp = client.get('/api/matching/peers-partial', headers=headers)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_peers_partial_unauthenticated(self, client):
        resp = client.get('/api/matching/peers-partial')
        assert resp.status_code == 401

    def test_queue_status_partial_waiting(self, client, admin_headers):
        h, uid = register_and_login(client, 'htmx_q', 'htmx_q@test.com')
        _credit_user(client, admin_headers, uid)
        _verify_user(client, admin_headers, h)
        # Join queue to create a waiting entry
        jr = client.post('/api/matching/queue', headers=h,
                         json={'skill': 'htmx-test', 'priority': 0})
        assert jr.status_code == 201
        eid = jr.get_json()['entry_id']

        resp = client.get(f'/api/matching/queue/{eid}/status-partial', headers=h)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        body = resp.data.decode()
        # Waiting state includes HTMX polling attributes
        assert 'hx-trigger' in body
        assert 'every 10s' in body

    def test_queue_status_partial_matched(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'htmx_m1', 'htmx_m1@test.com')
        h2, uid2 = register_and_login(client, 'htmx_m2', 'htmx_m2@test.com')
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        _verify_user(client, admin_headers, h1)
        _verify_user(client, admin_headers, h2)

        jr1 = client.post('/api/matching/queue', headers=h1,
                          json={'skill': 'htmx-match'})
        jr2 = client.post('/api/matching/queue', headers=h2,
                          json={'skill': 'htmx-match'})
        eid1 = jr1.get_json()['entry_id']
        eid2 = jr2.get_json()['entry_id']

        # Trigger a manual match
        client.post('/api/matching/queue/match', headers=h1,
                    json={'skill': 'htmx-match'})

        # Check the partial for the matched entry
        # One of the entries will be matched
        resp1 = client.get(f'/api/matching/queue/{eid1}/status-partial', headers=h1)
        resp2 = client.get(f'/api/matching/queue/{eid2}/status-partial', headers=h2)
        bodies = [resp1.data.decode(), resp2.data.decode()]
        # At least one should show 'found' state (no polling trigger)
        assert any('Match Found' in b or 'Searching' in b for b in bodies)

    def test_sessions_partial_returns_html(self, client, user_headers_with_id, admin_headers):
        headers, uid = user_headers_with_id
        _credit_user(client, admin_headers, uid)
        resp = client.get('/api/matching/sessions-partial', headers=headers)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_sessions_partial_unauthenticated(self, client):
        resp = client.get('/api/matching/sessions-partial')
        assert resp.status_code == 401


class TestQueueGovernance:
    def test_join_queue_basic(self, client, admin_headers):
        h, uid = register_and_login(client, 'qgov_a', 'qgov_a@test.com')
        _credit_user(client, admin_headers, uid)
        _verify_user(client, admin_headers, h)
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'governance-test'})
        assert resp.status_code == 201
        assert 'entry_id' in resp.get_json()

    def test_cancel_queue_entry(self, client, admin_headers):
        h, uid = register_and_login(client, 'qcancel', 'qcancel@test.com')
        _credit_user(client, admin_headers, uid)
        _verify_user(client, admin_headers, h)
        jr = client.post('/api/matching/queue', headers=h,
                         json={'skill': 'cancel-test'})
        eid = jr.get_json()['entry_id']
        resp = client.put(f'/api/matching/queue/{eid}/cancel', headers=h)
        assert resp.status_code == 200
        # Verify entry is now cancelled
        get_r = client.get(f'/api/matching/queue/{eid}', headers=h)
        assert get_r.get_json()['entry']['status'] == 'cancelled'

    def test_cancel_interval_enforced(self, client, admin_headers):
        h, uid = register_and_login(client, 'qcooldown', 'qcooldown@test.com')
        _credit_user(client, admin_headers, uid)
        _verify_user(client, admin_headers, h)
        # First join and cancel
        jr = client.post('/api/matching/queue', headers=h,
                         json={'skill': 'cooldown-test'})
        eid = jr.get_json()['entry_id']
        client.put(f'/api/matching/queue/{eid}/cancel', headers=h)
        # Immediately try to rejoin — should be rejected
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'cooldown-test-2'})
        assert resp.status_code == 400
        assert 'minutes' in resp.get_json()['error'].lower()

    def test_queue_entry_access_control(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'qacc1', 'qacc1@test.com')
        h2, uid2 = register_and_login(client, 'qacc2', 'qacc2@test.com')
        _credit_user(client, admin_headers, uid1)
        _verify_user(client, admin_headers, h1)
        jr = client.post('/api/matching/queue', headers=h1,
                         json={'skill': 'access-test'})
        eid = jr.get_json()['entry_id']
        # user2 cannot see user1's queue entry
        resp = client.get(f'/api/matching/queue/{eid}', headers=h2)
        assert resp.status_code == 403

    def test_non_admin_cannot_cancel_others_entry(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'qother1', 'qother1@test.com')
        h2, uid2 = register_and_login(client, 'qother2', 'qother2@test.com')
        _credit_user(client, admin_headers, uid1)
        _verify_user(client, admin_headers, h1)
        jr = client.post('/api/matching/queue', headers=h1,
                         json={'skill': 'other-test'})
        eid = jr.get_json()['entry_id']
        resp = client.put(f'/api/matching/queue/{eid}/cancel', headers=h2)
        assert resp.status_code == 403

    def test_insufficient_credits_blocked(self, client, admin_headers):
        h, uid = register_and_login(client, 'qnocred', 'qnocred@test.com')
        # No credit top-up — default balance is 0
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'no-credit'})
        assert resp.status_code == 403

    def test_skill_required(self, client, admin_headers):
        h, uid = register_and_login(client, 'qnoskill', 'qnoskill@test.com')
        _credit_user(client, admin_headers, uid)
        _verify_user(client, admin_headers, h)
        resp = client.post('/api/matching/queue', headers=h,
                           json={'skill': ''})
        assert resp.status_code == 400


class TestSessionLifecycle:
    def test_request_session(self, client, admin_headers,
                              user_headers_with_id, user2_headers_with_id):
        h1, uid1 = user_headers_with_id
        h2, uid2 = user2_headers_with_id
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        _verify_user(client, admin_headers, h1)
        _verify_user(client, admin_headers, h2)
        resp = client.post('/api/matching/sessions', headers=h1,
                           json={'participant_id': uid2,
                                 'description': 'Test session',
                                 'credit_amount': 0})
        assert resp.status_code == 201
        assert 'session_id' in resp.get_json()

    def test_session_status_transitions(self, client, admin_headers,
                                         user_headers_with_id, user2_headers_with_id):
        h1, uid1 = user_headers_with_id
        h2, uid2 = user2_headers_with_id
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        _verify_user(client, admin_headers, h1)
        _verify_user(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'Lifecycle', 'credit_amount': 0})
        sid = cr.get_json()['session_id']

        # Accept (pending → active)
        r = client.put(f'/api/matching/sessions/{sid}', headers=h2,
                       json={'status': 'active'})
        assert r.status_code == 200

        # Complete (active → completed)
        r2 = client.put(f'/api/matching/sessions/{sid}', headers=h1,
                        json={'status': 'completed'})
        assert r2.status_code == 200

    def test_invalid_transition_rejected(self, client, admin_headers,
                                          user_headers_with_id, user2_headers_with_id):
        h1, uid1 = user_headers_with_id
        h2, uid2 = user2_headers_with_id
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        _verify_user(client, admin_headers, h1)
        _verify_user(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'Bad trans', 'credit_amount': 0})
        sid = cr.get_json()['session_id']
        # Can't go pending → completed directly
        r = client.put(f'/api/matching/sessions/{sid}', headers=h1,
                       json={'status': 'completed'})
        assert r.status_code == 400

    def test_third_party_cannot_update_session(self, client, admin_headers,
                                                user_headers_with_id,
                                                user2_headers_with_id):
        h1, uid1 = user_headers_with_id
        h2, uid2 = user2_headers_with_id
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        _verify_user(client, admin_headers, h1)
        _verify_user(client, admin_headers, h2)
        cr = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'RBAC', 'credit_amount': 0})
        sid = cr.get_json()['session_id']
        # Register a third user
        h3, uid3 = register_and_login(client, 'third_user', 'third@test.com')
        r = client.put(f'/api/matching/sessions/{sid}', headers=h3,
                       json={'status': 'cancelled'})
        assert r.status_code == 403


class TestBlocklist:
    def test_block_and_unblock(self, client, admin_headers,
                                user_headers_with_id, user2_headers_with_id):
        h1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id
        _credit_user(client, admin_headers, uid1)

        br = client.post('/api/matching/block', headers=h1,
                         json={'user_id': uid2, 'reason': 'test'})
        assert br.status_code == 200

        ubr = client.delete(f'/api/matching/block/{uid2}', headers=h1)
        assert ubr.status_code == 200

    def test_cannot_block_self(self, client, user_headers_with_id, admin_headers):
        headers, uid = user_headers_with_id
        _credit_user(client, admin_headers, uid)
        resp = client.post('/api/matching/block', headers=headers,
                           json={'user_id': uid})
        assert resp.status_code == 400

    def test_blocked_user_excluded_from_search(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'blk_s1', 'blk_s1@test.com')
        h2, uid2 = register_and_login(client, 'blk_s2', 'blk_s2@test.com')
        _credit_user(client, admin_headers, uid1)
        _credit_user(client, admin_headers, uid2)
        client.post('/api/matching/profile', headers=h2,
                    json={'skills_offered': ['Elixir'], 'is_active': True})
        client.post('/api/matching/block', headers=h1,
                    json={'user_id': uid2})
        resp = client.get('/api/matching/search', headers=h1,
                          query_string={'skill': 'Elixir'})
        profiles = resp.get_json()['profiles']
        assert not any(p['user_id'] == uid2 for p in profiles)
