"""
Shared fixtures for frontend tests.
Uses the same Flask test client pattern as API_tests but with a separate DB
so frontend and API test suites remain isolated.
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('WERKZEUG_RUN_MAIN', 'false')

_db_fd, _DB_PATH = tempfile.mkstemp(suffix='_fe_test.db')
os.close(_db_fd)

import config as _cfg
_cfg.Config.DATABASE_PATH = _DB_PATH   # isolate from API_tests DB

import pytest


@pytest.fixture(scope='session')
def app():
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    yield application
    if os.path.exists(_DB_PATH):
        os.unlink(_DB_PATH)


@pytest.fixture(scope='session')
def client(app):
    return app.test_client()


@pytest.fixture(scope='session')
def admin_headers(client):
    resp = client.post('/api/auth/login', json={
        'username': 'admin', 'password': 'Admin@123456!'
    })
    assert resp.status_code == 200, f'Admin login failed: {resp.data}'
    token = resp.get_json()['token']
    return {'Authorization': f'Bearer {token}'}


def _register_and_login(client, username, email, password='FETest@123456!'):
    reg = client.post('/api/auth/register',
                      json={'username': username, 'email': email,
                            'password': password})
    uid = reg.get_json().get('user_id')
    lr  = client.post('/api/auth/login',
                      json={'username': username, 'password': password})
    token = lr.get_json()['token']
    return {'Authorization': f'Bearer {token}'}, uid


@pytest.fixture(scope='session')
def user_with_credits(client, admin_headers):
    h, uid = _register_and_login(client, 'fe_user', 'fe_user@test.com')
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': 500.0,
                      'description': 'FE test credits'})
    return h, uid
