"""API tests for /api/audit: logs listing, summary, chain verification."""

import pytest


class TestListLogs:
    def test_admin_can_list_logs(self, client, admin_headers):
        resp = client.get('/api/audit/logs', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'logs' in data
        assert 'total' in data
        assert 'page' in data
        assert 'categories' in data

    def test_regular_user_forbidden(self, client, user_headers):
        resp = client.get('/api/audit/logs', headers=user_headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        resp = client.get('/api/audit/logs')
        assert resp.status_code == 401

    def test_filter_by_category_auth(self, client, admin_headers):
        resp = client.get('/api/audit/logs?category=auth',
                          headers=admin_headers)
        assert resp.status_code == 200
        logs = resp.get_json()['logs']
        assert all(log['category'] == 'auth' for log in logs)

    def test_filter_by_category_financial(self, client, admin_headers):
        resp = client.get('/api/audit/logs?category=financial',
                          headers=admin_headers)
        assert resp.status_code == 200
        logs = resp.get_json()['logs']
        assert all(log['category'] == 'financial' for log in logs)

    def test_invalid_category_returns_400(self, client, admin_headers):
        resp = client.get('/api/audit/logs?category=invalid_cat',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_filter_by_action_substring(self, client, admin_headers):
        resp = client.get('/api/audit/logs?action=LOGIN',
                          headers=admin_headers)
        assert resp.status_code == 200
        logs = resp.get_json()['logs']
        assert all('LOGIN' in log['action'] for log in logs)

    def test_pagination(self, client, admin_headers):
        resp = client.get('/api/audit/logs?per_page=2&page=1',
                          headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['logs']) <= 2
        assert data['page'] == 1

    def test_date_range_filter(self, client, admin_headers):
        resp = client.get('/api/audit/logs?from_date=2020-01-01&to_date=2030-12-31',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_logs_have_category_field(self, client, admin_headers):
        resp = client.get('/api/audit/logs', headers=admin_headers)
        logs = resp.get_json()['logs']
        if logs:
            assert 'category' in logs[0]

    def test_auditor_can_list_logs(self, client, admin_headers):
        """Auditors also have read access to logs."""
        from API_tests.conftest import register_and_login
        aud_headers, aud_id = register_and_login(
            client, 'auditor_test', 'auditor@test.com'
        )
        # Promote to auditor
        client.put(f'/api/users/{aud_id}/role', headers=admin_headers,
                   json={'role': 'auditor'})
        # Re-login to get fresh token with auditor role
        login = client.post('/api/auth/login', json={
            'username': 'auditor_test', 'password': 'TestUser@123456!'
        })
        aud_token = login.get_json()['token']
        aud_hdr = {'Authorization': f'Bearer {aud_token}'}

        resp = client.get('/api/audit/logs', headers=aud_hdr)
        assert resp.status_code == 200


class TestAuditSummary:
    def test_summary_returns_by_category(self, client, admin_headers):
        resp = client.get('/api/audit/logs/summary', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'by_category' in data
        assert 'by_action' in data
        assert 'date_range' in data
        assert 'categories' in data

    def test_summary_category_keys_present(self, client, admin_headers):
        resp = client.get('/api/audit/logs/summary', headers=admin_headers)
        by_cat = resp.get_json()['by_category']
        for cat in ('auth', 'permissions', 'financial', 'data_access', 'admin'):
            assert cat in by_cat

    def test_summary_with_date_range(self, client, admin_headers):
        resp = client.get('/api/audit/logs/summary?from_date=2020-01-01&to_date=2030-12-31',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_summary_forbidden_for_users(self, client, user_headers):
        resp = client.get('/api/audit/logs/summary', headers=user_headers)
        assert resp.status_code == 403


class TestVerifyChain:
    def test_chain_is_valid(self, client, admin_headers):
        resp = client.get('/api/audit/logs/verify', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['valid'] is True
        assert 'entries' in data or 'entries_checked' in data

    def test_verify_forbidden_for_users(self, client, user_headers):
        resp = client.get('/api/audit/logs/verify', headers=user_headers)
        assert resp.status_code == 403

    def test_verify_returns_entry_count(self, client, admin_headers):
        resp = client.get('/api/audit/logs/verify', headers=admin_headers)
        data = resp.get_json()
        assert 'entries' in data
        assert isinstance(data['entries'], int)
        assert data['entries'] > 0
