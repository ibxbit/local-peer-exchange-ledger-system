"""
Shared fixtures for API tests.
Uses Flask test client against a temporary SQLite database.
The scheduler is disabled by setting WERKZEUG_RUN_MAIN=false in the environment.
"""

import sys
import os
import tempfile

# Ensure repo root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Prevent the APScheduler from starting during tests
os.environ.setdefault('WERKZEUG_RUN_MAIN', 'false')

# Patch Config.DATABASE_PATH before any Flask app code is imported
_db_fd, _DB_PATH = tempfile.mkstemp(suffix='_test.db')
os.close(_db_fd)

import config as _cfg
_cfg.Config.DATABASE_PATH = _DB_PATH

import pytest


@pytest.fixture(scope='session')
def app():
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    yield application
    # Teardown: remove temp DB
    if os.path.exists(_DB_PATH):
        os.unlink(_DB_PATH)


@pytest.fixture(scope='session')
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def admin_headers(client):
    resp = client.post('/api/auth/login', json={
        'username': 'admin',
        'password': 'Admin@123456!'
    })
    assert resp.status_code == 200, f'Admin login failed: {resp.data}'
    token = resp.get_json()['token']
    return {'Authorization': f'Bearer {token}'}


def register_and_login(client, username, email, password='TestUser@123456!'):
    """Register a user and return their auth headers + user_id."""
    reg = client.post('/api/auth/register', json={
        'username': username, 'email': email, 'password': password
    })
    user_id = reg.get_json().get('user_id')

    login = client.post('/api/auth/login', json={
        'username': username, 'password': password
    })
    token = login.get_json()['token']
    return {'Authorization': f'Bearer {token}'}, user_id


@pytest.fixture(scope='session')
def user_headers(client):
    headers, _ = register_and_login(
        client, 'api_user1', 'api_user1@test.com'
    )
    return headers


@pytest.fixture(scope='session')
def user_headers_with_id(client):
    return register_and_login(client, 'api_user2', 'api_user2@test.com')


@pytest.fixture(scope='session')
def user2_headers_with_id(client):
    return register_and_login(client, 'api_user3', 'api_user3@test.com')
