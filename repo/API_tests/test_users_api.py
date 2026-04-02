"""API tests for /api/users: list, get, update, role, status, reputation."""

import pytest
from API_tests.conftest import register_and_login


class TestListUsers:
    def test_admin_can_list(self, client, admin_headers):
        resp = client.get('/api/users', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'users' in data
        assert 'total' in data

    def test_user_forbidden(self, client, user_headers):
        resp = client.get('/api/users', headers=user_headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        resp = client.get('/api/users')
        assert resp.status_code == 401

    def test_emails_are_masked(self, client, admin_headers):
        resp = client.get('/api/users', headers=admin_headers)
        for user in resp.get_json()['users']:
            assert '***' in user['email']

    def test_no_password_hash_in_response(self, client, admin_headers):
        resp = client.get('/api/users', headers=admin_headers)
        for user in resp.get_json()['users']:
            assert 'password_hash' not in user

    def test_search_by_username(self, client, admin_headers):
        resp = client.get('/api/users?search=admin', headers=admin_headers)
        data = resp.get_json()
        assert any(u['username'] == 'admin' for u in data['users'])

    def test_filter_by_role(self, client, admin_headers):
        resp = client.get('/api/users?role=admin', headers=admin_headers)
        data = resp.get_json()
        assert all(u['role'] == 'admin' for u in data['users'])

    def test_pagination(self, client, admin_headers):
        resp = client.get('/api/users?per_page=2&page=1', headers=admin_headers)
        data = resp.get_json()
        assert len(data['users']) <= 2


class TestGetUser:
    def test_user_can_get_own_profile(self, client, user_headers_with_id):
        headers, uid = user_headers_with_id
        resp = client.get(f'/api/users/{uid}', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['user']['id'] == uid
        assert '***' in data['user']['email']

    def test_admin_can_get_any_profile(self, client, admin_headers,
                                        user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.get(f'/api/users/{uid}', headers=admin_headers)
        assert resp.status_code == 200

    def test_user_cannot_get_other_profile(self, client, user_headers,
                                            user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.get(f'/api/users/{uid2}', headers=user_headers)
        assert resp.status_code == 403

    def test_nonexistent_user_returns_404(self, client, admin_headers):
        resp = client.get('/api/users/99999', headers=admin_headers)
        assert resp.status_code == 404


class TestUpdateRole:
    def test_admin_can_change_role(self, client, admin_headers):
        h, uid = register_and_login(client, 'role_target', 'role_target@test.com')

        resp = client.put(f'/api/users/{uid}/role', headers=admin_headers,
                          json={'role': 'auditor'})
        assert resp.status_code == 200

        # Verify role changed
        resp2 = client.get(f'/api/users/{uid}', headers=admin_headers)
        assert resp2.get_json()['user']['role'] == 'auditor'

    def test_invalid_role_rejected(self, client, admin_headers):
        h, uid = register_and_login(client, 'role_bad', 'role_bad@test.com')
        resp = client.put(f'/api/users/{uid}/role', headers=admin_headers,
                          json={'role': 'superadmin'})
        assert resp.status_code == 400

    def test_non_admin_cannot_change_role(self, client, user_headers,
                                           user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.put(f'/api/users/{uid2}/role', headers=user_headers,
                          json={'role': 'admin'})
        assert resp.status_code == 403


class TestUpdateStatus:
    def test_admin_can_deactivate_user(self, client, admin_headers):
        h, uid = register_and_login(client, 'ban_target', 'ban_target@test.com')

        resp = client.put(f'/api/users/{uid}/status', headers=admin_headers,
                          json={'is_active': False})
        assert resp.status_code == 200

        resp2 = client.get(f'/api/users/{uid}', headers=admin_headers)
        assert not resp2.get_json()['user']['is_active']

    def test_admin_can_reactivate_user(self, client, admin_headers):
        h, uid = register_and_login(client, 'reactivate', 'reactivate@test.com')
        client.put(f'/api/users/{uid}/status', headers=admin_headers,
                   json={'is_active': False})

        resp = client.put(f'/api/users/{uid}/status', headers=admin_headers,
                          json={'is_active': True})
        assert resp.status_code == 200

    def test_missing_is_active_rejected(self, client, admin_headers,
                                         user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.put(f'/api/users/{uid}/status', headers=admin_headers,
                          json={})
        assert resp.status_code == 400

    def test_non_admin_cannot_change_status(self, client, user_headers,
                                             user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.put(f'/api/users/{uid2}/status', headers=user_headers,
                          json={'is_active': False})
        assert resp.status_code == 403


class TestReputation:
    def test_get_reputation(self, client, user_headers_with_id):
        headers, uid = user_headers_with_id
        resp = client.get(f'/api/users/{uid}/reputation', headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'user_id' in data
        assert 'sessions_completed' in data

    def test_any_authenticated_user_can_get_reputation(self, client,
                                                         user_headers,
                                                         user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.get(f'/api/users/{uid2}/reputation', headers=user_headers)
        assert resp.status_code == 200


class TestUpdateUserEmail:
    def test_user_can_update_own_email(self, client):
        h, uid = register_and_login(client, 'email_update', 'email_update@test.com')
        resp = client.put(f'/api/users/{uid}', headers=h,
                          json={'email': 'newemail_update@test.com'})
        assert resp.status_code == 200

    def test_duplicate_email_rejected(self, client):
        h1, uid1 = register_and_login(client, 'email_dup_a', 'email_dup_a@test.com')
        h2, uid2 = register_and_login(client, 'email_dup_b', 'email_dup_b@test.com')

        resp = client.put(f'/api/users/{uid2}', headers=h2,
                          json={'email': 'email_dup_a@test.com'})
        assert resp.status_code == 409

    def test_user_cannot_update_other(self, client, user_headers,
                                       user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.put(f'/api/users/{uid2}', headers=user_headers,
                          json={'email': 'hacker@test.com'})
        assert resp.status_code == 403
