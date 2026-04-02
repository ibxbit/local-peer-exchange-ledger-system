"""
Frontend tests — verify HTMX partial endpoints, SPA shell, and core UI flows.

These tests use the Flask test client to verify:
  1. The SPA shell (index.html) loads correctly with type="module" script tag.
  2. HTMX partial endpoints return valid HTML fragments.
  3. Core flows (auth, matching, sessions) respond with expected HTML content.
  4. HTMX polling attributes are present in waiting-state responses.
  5. Matched/expired states do NOT include polling attributes (stop polling).
"""

import pytest
import re


# ---- SPA shell ----------------------------------------------------------

class TestSPAShell:
    def test_index_returns_200(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_index_contains_module_script(self, client):
        """Critical: app.js must be loaded as type=module for ES imports to work."""
        resp = client.get('/')
        body = resp.data.decode()
        assert 'type="module"' in body
        assert 'app.js' in body

    def test_index_includes_htmx(self, client):
        """HTMX script must be present before the module script."""
        resp = client.get('/')
        body = resp.data.decode()
        assert 'htmx' in body.lower()

    def test_static_app_js_served(self, client):
        resp = client.get('/static/js/app.js')
        assert resp.status_code == 200

    def test_static_htmx_served(self, client):
        resp = client.get('/static/js/htmx.min.js')
        assert resp.status_code == 200
        # Should be substantial JS content, not an error page
        assert len(resp.data) > 10_000

    def test_static_css_served(self, client):
        resp = client.get('/static/css/style.css')
        assert resp.status_code == 200

    def test_htmx_indicator_css_present(self, client):
        """HTMX indicator class must be in stylesheet."""
        resp = client.get('/static/css/style.css')
        assert b'htmx-indicator' in resp.data


# ---- Auth flow ----------------------------------------------------------

class TestAuthFlow:
    def test_login_returns_token(self, client):
        resp = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'Admin@123456!'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert data['user']['role'] == 'admin'

    def test_me_endpoint_returns_user(self, client, admin_headers):
        resp = client.get('/api/auth/me', headers=admin_headers)
        assert resp.status_code == 200
        assert 'user' in resp.get_json()

    def test_unauthenticated_api_returns_401(self, client):
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401

    def test_bad_password_returns_401(self, client):
        resp = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'wrongpassword'
        })
        assert resp.status_code == 401


# ---- HTMX peer search partial -------------------------------------------

class TestPeersPartial:
    def test_peers_partial_requires_auth(self, client):
        resp = client.get('/api/matching/peers-partial')
        assert resp.status_code == 401

    def test_peers_partial_returns_html(self, client, user_with_credits):
        headers, uid = user_with_credits
        resp = client.get('/api/matching/peers-partial', headers=headers)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_peers_partial_empty_state(self, client, user_with_credits):
        headers, uid = user_with_credits
        # Search for a skill no one has
        resp = client.get('/api/matching/peers-partial', headers=headers,
                          query_string={'skill': 'xyzzy-nonexistent-skill-99'})
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'No matching peers' in body or 'empty' in body.lower()

    def test_peers_partial_shows_peer_card(self, client, user_with_credits,
                                            admin_headers):
        headers, uid = user_with_credits
        # Create a second user with a profile
        from frontend_tests.conftest import _register_and_login
        h2, uid2 = _register_and_login(client, 'peer_card', 'peer_card@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid2, 'amount': 500.0,
                          'description': 'test'})
        client.post('/api/matching/profile', headers=h2,
                    json={'skills_offered': ['Haskell'], 'is_active': True})

        resp = client.get('/api/matching/peers-partial', headers=headers,
                          query_string={'skill': 'Haskell'})
        body = resp.data.decode()
        assert 'Haskell' in body or 'peer_card' in body

    def test_peers_partial_with_tag_filter(self, client, user_with_credits,
                                            admin_headers):
        headers, uid = user_with_credits
        from frontend_tests.conftest import _register_and_login
        h2, uid2 = _register_and_login(client, 'tag_peer', 'tag_peer@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid2, 'amount': 500.0,
                          'description': 'test'})
        client.post('/api/matching/profile', headers=h2,
                    json={'skills_offered': ['Elm'], 'tags': ['functional'],
                          'is_active': True})

        resp = client.get('/api/matching/peers-partial', headers=headers,
                          query_string={'tag': 'functional'})
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'tag_peer' in body or 'Elm' in body


# ---- HTMX queue status partial ------------------------------------------

class TestQueueStatusPartial:
    def test_queue_status_waiting_has_htmx_poll(self, client, user_with_credits,
                                                 admin_headers):
        """While waiting, the fragment must have hx-trigger='every 10s'."""
        headers, uid = user_with_credits
        jr = client.post('/api/matching/queue', headers=headers,
                         json={'skill': 'ui-test-skill'})
        assert jr.status_code == 201, jr.get_json()
        eid = jr.get_json()['entry_id']

        resp = client.get(f'/api/matching/queue/{eid}/status-partial',
                          headers=headers)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        body = resp.data.decode()
        assert 'hx-trigger' in body
        assert 'every 10s' in body

    def test_queue_status_partial_shows_skill(self, client, admin_headers):
        from frontend_tests.conftest import _register_and_login
        h, uid = _register_and_login(client, 'qs_skill', 'qs_skill@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid, 'amount': 500.0,
                          'description': 'test'})
        jr = client.post('/api/matching/queue', headers=h,
                         json={'skill': 'uniqueskill42'})
        eid = jr.get_json()['entry_id']

        resp = client.get(f'/api/matching/queue/{eid}/status-partial', headers=h)
        body = resp.data.decode()
        assert 'uniqueskill42' in body

    def test_queue_status_partial_after_cancel_no_poll(self, client, admin_headers):
        """After cancellation, the fragment must NOT include hx-trigger polling."""
        from frontend_tests.conftest import _register_and_login
        h, uid = _register_and_login(client, 'qs_cancel', 'qs_cancel@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid, 'amount': 500.0,
                          'description': 'test'})
        jr = client.post('/api/matching/queue', headers=h,
                         json={'skill': 'cancelskill'})
        eid = jr.get_json()['entry_id']
        client.put(f'/api/matching/queue/{eid}/cancel', headers=h)

        resp = client.get(f'/api/matching/queue/{eid}/status-partial', headers=h)
        body = resp.data.decode()
        # Cancelled state should NOT have the every-10s poll trigger
        assert 'every 10s' not in body

    def test_queue_status_partial_wrong_user(self, client, admin_headers):
        from frontend_tests.conftest import _register_and_login
        h1, uid1 = _register_and_login(client, 'qs_own', 'qs_own@test.com')
        h2, uid2 = _register_and_login(client, 'qs_other', 'qs_other@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid1, 'amount': 500.0,
                          'description': 'test'})
        jr = client.post('/api/matching/queue', headers=h1,
                         json={'skill': 'private-skill'})
        eid = jr.get_json()['entry_id']

        # Other user cannot see this entry's status partial
        resp = client.get(f'/api/matching/queue/{eid}/status-partial', headers=h2)
        body = resp.data.decode()
        assert 'Access denied' in body or resp.status_code == 401


# ---- HTMX sessions partial ----------------------------------------------

class TestSessionsPartial:
    def test_sessions_partial_empty(self, client, admin_headers):
        from frontend_tests.conftest import _register_and_login
        h, uid = _register_and_login(client, 'sess_empty', 'sess_empty@test.com')
        resp = client.get('/api/matching/sessions-partial', headers=h)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        body = resp.data.decode()
        assert 'No sessions' in body or '<tr>' in body

    def test_sessions_partial_shows_session(self, client, admin_headers):
        from frontend_tests.conftest import _register_and_login
        h1, uid1 = _register_and_login(client, 'sess_a', 'sess_a@test.com')
        h2, uid2 = _register_and_login(client, 'sess_b', 'sess_b@test.com')
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid1, 'amount': 500.0,
                          'description': 'test'})
        client.post('/api/ledger/credit', headers=admin_headers,
                    json={'user_id': uid2, 'amount': 500.0,
                          'description': 'test'})
        client.post('/api/matching/sessions', headers=h1,
                    json={'participant_id': uid2, 'description': 'HTMX session',
                          'credit_amount': 0})

        resp = client.get('/api/matching/sessions-partial', headers=h1)
        body = resp.data.decode()
        assert '<tr>' in body
        assert 'pending' in body.lower() or 'sess_b' in body
