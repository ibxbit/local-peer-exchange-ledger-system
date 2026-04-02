"""API tests for /api/auth: register, login, /me, change-password."""

import pytest


class TestRegister:
    def test_register_success(self, client):
        resp = client.post('/api/auth/register', json={
            'username': 'reg_test_user',
            'email': 'reg_test@test.com',
            'password': 'Reg@Test123456!'
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'user_id' in data
        assert data['message'] == 'Registration successful.'

    def test_register_duplicate_username(self, client):
        client.post('/api/auth/register', json={
            'username': 'dup_user',
            'email': 'dup1@test.com',
            'password': 'Dup@Test123456!'
        })
        resp = client.post('/api/auth/register', json={
            'username': 'dup_user',
            'email': 'dup2@test.com',
            'password': 'Dup@Test123456!'
        })
        assert resp.status_code == 409
        assert 'error' in resp.get_json()

    def test_register_duplicate_email(self, client):
        client.post('/api/auth/register', json={
            'username': 'email_dup1',
            'email': 'shared@test.com',
            'password': 'Pass@123456789!'
        })
        resp = client.post('/api/auth/register', json={
            'username': 'email_dup2',
            'email': 'shared@test.com',
            'password': 'Pass@123456789!'
        })
        assert resp.status_code == 409

    def test_register_invalid_email(self, client):
        resp = client.post('/api/auth/register', json={
            'username': 'bademail',
            'email': 'notanemail',
            'password': 'Pass@123456789!'
        })
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_register_weak_password(self, client):
        resp = client.post('/api/auth/register', json={
            'username': 'weakpass',
            'email': 'weakpass@test.com',
            'password': 'short'
        })
        assert resp.status_code == 400

    def test_register_missing_fields(self, client):
        resp = client.post('/api/auth/register', json={})
        assert resp.status_code == 400


class TestLogin:
    def test_login_success(self, client, admin_headers):
        resp = client.post('/api/auth/login', json={
            'username': 'admin',
            'password': 'Admin@123456!'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert 'user' in data
        assert data['user']['role'] == 'admin'

    def test_login_wrong_password(self, client):
        client.post('/api/auth/register', json={
            'username': 'login_test_u',
            'email': 'logintest@test.com',
            'password': 'Login@123456789!'
        })
        resp = client.post('/api/auth/login', json={
            'username': 'login_test_u',
            'password': 'wrongpassword'
        })
        assert resp.status_code == 401
        assert 'error' in resp.get_json()

    def test_login_nonexistent_user(self, client):
        resp = client.post('/api/auth/login', json={
            'username': 'ghost_user',
            'password': 'Pass@123456789!'
        })
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post('/api/auth/login', json={})
        assert resp.status_code == 401


class TestMe:
    def test_me_authenticated(self, client, admin_headers):
        resp = client.get('/api/auth/me', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'user' in data
        assert data['user']['username'] == 'admin'
        assert '***' in data['user']['email']   # masked
        assert 'password_hash' not in data['user']

    def test_me_unauthenticated(self, client):
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401

    def test_me_invalid_token(self, client):
        resp = client.get('/api/auth/me',
                          headers={'Authorization': 'Bearer garbage.token.here'})
        assert resp.status_code == 401

    def test_me_returns_credit_balance(self, client, admin_headers):
        resp = client.get('/api/auth/me', headers=admin_headers)
        assert 'credit_balance' in resp.get_json()['user']


class TestChangePassword:
    def test_change_password_success(self, client):
        client.post('/api/auth/register', json={
            'username': 'pw_change_user',
            'email': 'pw_change@test.com',
            'password': 'OldPass@123456!'
        })
        login = client.post('/api/auth/login', json={
            'username': 'pw_change_user',
            'password': 'OldPass@123456!'
        })
        token = login.get_json()['token']
        headers = {'Authorization': f'Bearer {token}'}

        resp = client.post('/api/auth/change-password',
                           headers=headers,
                           json={
                               'current_password': 'OldPass@123456!',
                               'new_password':     'NewPass@789012!'
                           })
        assert resp.status_code == 200

    def test_change_password_wrong_current(self, client, user_headers):
        resp = client.post('/api/auth/change-password',
                           headers=user_headers,
                           json={
                               'current_password': 'wrong_current',
                               'new_password':     'NewPass@789012!'
                           })
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_change_password_unauthenticated(self, client):
        resp = client.post('/api/auth/change-password',
                           json={'current_password': 'x', 'new_password': 'y'})
        assert resp.status_code == 401
