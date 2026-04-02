"""API tests for /api/admin: violations, bans, permissions, user detail."""

import pytest
from API_tests.conftest import register_and_login


def _credit_user(client, admin_headers, uid, amount=500.0):
    """Give a user credits so they pass the guard threshold."""
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount, 'description': 'Test top-up'})


class TestAdminUsers:
    def test_list_users_admin(self, client, admin_headers):
        resp = client.get('/api/admin/users', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'users' in data

    def test_list_users_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/admin/users', headers=user_headers)
        assert resp.status_code == 403

    def test_user_detail(self, client, admin_headers, user_headers_with_id):
        _, uid = user_headers_with_id
        resp = client.get(f'/api/admin/users/{uid}', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        # admin detail returns the user dict directly (no 'user' wrapper)
        assert 'id' in data
        assert '***' in data['email']


class TestViolations:
    def test_report_violation(self, client, admin_headers,
                               user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        # Violations are reported via /api/reputation/violations (not /api/admin/violations)
        resp = client.post('/api/reputation/violations', headers=headers1,
                           json={
                               'user_id': uid2,
                               'violation_type': 'spam',
                               'description': 'Sending spam messages',
                               'severity': 'low'
                           })
        assert resp.status_code == 201
        assert 'violation_id' in resp.get_json()

    def test_list_violations_admin(self, client, admin_headers):
        resp = client.get('/api/admin/violations', headers=admin_headers)
        assert resp.status_code == 200
        assert 'violations' in resp.get_json()

    def test_cannot_report_self(self, client, user_headers_with_id):
        headers, uid = user_headers_with_id
        resp = client.post('/api/reputation/violations', headers=headers,
                           json={
                               'user_id': uid,
                               'violation_type': 'spam',
                               'description': 'Self-report',
                               'severity': 'low'
                           })
        assert resp.status_code == 400

    def test_resolve_violation(self, client, admin_headers,
                                user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        create = client.post('/api/reputation/violations', headers=headers1,
                             json={
                                 'user_id': uid2,
                                 'violation_type': 'fraud',
                                 'description': 'Fraudulent behaviour',
                                 'severity': 'high'
                             })
        vid = create.get_json()['violation_id']

        # Resolve via /api/reputation/violations/<vid>/resolve
        resp = client.put(f'/api/reputation/violations/{vid}/resolve',
                          headers=admin_headers,
                          json={'decision': 'resolved',
                                'notes': 'Confirmed fraud'})
        assert resp.status_code == 200

    def test_dismiss_violation(self, client, admin_headers,
                                user_headers_with_id, user2_headers_with_id):
        headers1, uid1 = user_headers_with_id
        _, uid2 = user2_headers_with_id

        create = client.post('/api/reputation/violations', headers=headers1,
                             json={
                                 'user_id': uid2,
                                 'violation_type': 'other',
                                 'description': 'Minor issue',
                                 'severity': 'low'
                             })
        vid = create.get_json()['violation_id']
        resp = client.put(f'/api/reputation/violations/{vid}/resolve',
                          headers=admin_headers,
                          json={'decision': 'dismissed',
                                'notes': 'Not substantiated'})
        assert resp.status_code == 200


class TestBanUnban:
    def test_admin_can_ban_user(self, client, admin_headers):
        h, uid = register_and_login(client, 'ban_me', 'ban_me@test.com')
        resp = client.put(f'/api/admin/users/{uid}/ban', headers=admin_headers,
                          json={'reason': 'Repeated violations'})
        assert resp.status_code == 200

        # Verify user is inactive (admin detail returns dict directly, not wrapped)
        detail = client.get(f'/api/admin/users/{uid}', headers=admin_headers)
        assert not detail.get_json()['is_active']

    def test_banned_user_cannot_login(self, client, admin_headers):
        h, uid = register_and_login(client, 'ban_login', 'ban_login@test.com')
        client.put(f'/api/admin/users/{uid}/ban', headers=admin_headers,
                   json={'reason': 'Test ban'})

        resp = client.post('/api/auth/login', json={
            'username': 'ban_login',
            'password': 'TestUser@123456!'
        })
        assert resp.status_code in (401, 429, 403)

    def test_admin_can_unban_user(self, client, admin_headers):
        h, uid = register_and_login(client, 'unban_me', 'unban_me@test.com')
        client.put(f'/api/admin/users/{uid}/ban', headers=admin_headers,
                   json={'reason': 'Temp ban'})

        resp = client.put(f'/api/admin/users/{uid}/unban', headers=admin_headers,
                          json={'reason': 'Appeal upheld'})
        assert resp.status_code == 200

        detail = client.get(f'/api/admin/users/{uid}', headers=admin_headers)
        assert detail.get_json()['is_active']

    def test_non_admin_cannot_ban(self, client, user_headers,
                                   user2_headers_with_id):
        _, uid2 = user2_headers_with_id
        resp = client.put(f'/api/admin/users/{uid2}/ban', headers=user_headers,
                          json={'reason': 'Unauthorized'})
        assert resp.status_code == 403


class TestSessions:
    def test_list_sessions_admin(self, client, admin_headers):
        resp = client.get('/api/admin/sessions', headers=admin_headers)
        assert resp.status_code == 200
        assert 'sessions' in resp.get_json()

    def test_list_sessions_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/admin/sessions', headers=user_headers)
        assert resp.status_code == 403


class TestAdminPermissions:
    def test_grant_permission(self, client, admin_headers):
        h, uid = register_and_login(client, 'perm_admin',
                                     'perm_admin@test.com')
        # Promote to admin first
        client.put(f'/api/users/{uid}/role', headers=admin_headers,
                   json={'role': 'admin'})

        resp = client.put(f'/api/admin/permissions/{uid}/users',
                          headers=admin_headers,
                          json={'can_read': True, 'can_write': False})
        assert resp.status_code == 200

    def test_get_permissions(self, client, admin_headers):
        h, uid = register_and_login(client, 'perm_check',
                                     'perm_check@test.com')
        client.put(f'/api/users/{uid}/role', headers=admin_headers,
                   json={'role': 'admin'})

        client.put(f'/api/admin/permissions/{uid}/violations',
                   headers=admin_headers,
                   json={'can_read': True, 'can_write': True})

        resp = client.get(f'/api/admin/permissions/{uid}',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_revoke_permission(self, client, admin_headers):
        h, uid = register_and_login(client, 'perm_revoke',
                                     'perm_revoke@test.com')
        client.put(f'/api/users/{uid}/role', headers=admin_headers,
                   json={'role': 'admin'})

        client.put(f'/api/admin/permissions/{uid}/ledger',
                   headers=admin_headers,
                   json={'can_read': True, 'can_write': False})

        resp = client.delete(f'/api/admin/permissions/{uid}/ledger',
                             headers=admin_headers)
        assert resp.status_code == 200
