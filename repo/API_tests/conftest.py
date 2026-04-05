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


@pytest.fixture(autouse=True)
def _clear_session_cookie(client):
    """
    Clear the pex_session cookie from the shared test client after each test.
    Login now sets an httpOnly cookie in the Flask test client's cookie jar;
    without cleanup that cookie would be auto-forwarded to unrelated tests,
    turning expected-401 responses into 403s (authenticated but wrong role).
    """
    yield
    client.delete_cookie('pex_session', path='/', domain='localhost')


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


@pytest.fixture
def fresh_client(app):
    """
    Function-scoped test client with an empty cookie jar.
    Use this in tests that set/check httpOnly cookies so that stored cookies
    from a previous test cannot interfere with explicit Cookie headers.
    """
    return app.test_client()


@pytest.fixture(scope='session')
def user2_headers_with_id(client):
    return register_and_login(client, 'api_user3', 'api_user3@test.com')


def create_admin_user(username, email, password='Admin@Test123456!'):
    """
    Create a user with role='admin' directly via the DAL (bypasses the
    registration API which always creates role='user').  Returns (headers, uid).
    Use this inside a test that has access to the client fixture.
    """
    from app.models import db as _db
    from app.dal import user_dal as _udal
    from app.utils import generate_token as _gen

    with _db() as conn:
        # Check if already exists
        existing = _udal.get_by_username(conn, username)
        if existing:
            uid = existing['id']
        else:
            uid = _udal.create(conn, username, email, password, role='admin')
            conn.execute('UPDATE users SET role=? WHERE id=?', ('admin', uid))

    token = _gen(uid, username, 'admin')
    return {'Authorization': f'Bearer {token}'}, uid
