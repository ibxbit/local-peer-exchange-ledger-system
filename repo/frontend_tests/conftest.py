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

import io
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


# Clear the pex_session cookie after every test so unauthenticated tests
# are not accidentally authenticated by a previous test's login cookie.
@pytest.fixture(autouse=True)
def _clear_session_cookie(client):
    yield
    client.delete_cookie('pex_session', path='/', domain='localhost')


@pytest.fixture(scope='session')
def admin_headers(client):
    bootstrap_pw = _cfg.Config.ADMIN_SEED_PASSWORD
    rotated_pw = 'Admin@Rotated123456!'

    resp = None
    used_pw = None
    for candidate in (rotated_pw, bootstrap_pw):
        probe = client.post('/api/auth/login', json={
            'username': 'admin', 'password': candidate
        })
        if probe.status_code == 200:
            resp = probe
            used_pw = candidate
            break
    assert resp is not None, 'Admin login failed with known credentials.'

    data = resp.get_json()
    token = data['token']

    if data.get('user', {}).get('must_change_password'):
        cp = client.post('/api/auth/change-password', headers={
            'Authorization': f'Bearer {token}'
        }, json={
            'current_password': used_pw,
            'new_password': rotated_pw,
        })
        assert cp.status_code == 200, f'Admin rotation failed: {cp.data}'

        resp2 = client.post('/api/auth/login', json={
            'username': 'admin',
            'password': rotated_pw,
        })
        assert resp2.status_code == 200, f'Admin re-login failed: {resp2.data}'
        token = resp2.get_json()['token']

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


def _verify_user(client, admin_headers, h):
    """Submit a minimal verification doc and have admin approve it (idempotent)."""
    pdf = b'%PDF-1.4 test'
    r = client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'passport',
                          'document': (io.BytesIO(pdf), 'doc.pdf', 'application/pdf')},
                    content_type='multipart/form-data')
    if r.status_code != 201:
        return  # already submitted or already verified
    vid = r.get_json()['verification_id']
    client.put(f'/api/verification/{vid}/review', headers=admin_headers,
               json={'decision': 'verified', 'notes': 'auto-approved in test'})


@pytest.fixture(scope='session')
def user_with_credits(client, admin_headers):
    h, uid = _register_and_login(client, 'fe_user', 'fe_user@test.com')
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': 500.0,
                      'description': 'FE test credits'})
    _verify_user(client, admin_headers, h)
    return h, uid
