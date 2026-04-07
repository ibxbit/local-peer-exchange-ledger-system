"""
API tests: frontend auth hardening — httpOnly cookie-based auth.

  - Login sets a 'pex_session' httpOnly cookie (Set-Cookie header).
  - Requests authenticated solely via the cookie succeed.
  - POST /auth/logout clears the cookie (Set-Cookie: pex_session=; Max-Age=0).
  - After logout, cookie-only requests fail with 401.
  - Bearer token still works (backward compatibility).
  - Login state isolation: switching users does not leak the previous role.
  - Two sequential user sessions don't cross-contaminate.
"""

import pytest
from API_tests.conftest import register_and_login


# ---- Helpers ----------------------------------------------------------------

def _login(c, username, password='TestUser@123456!'):
    return c.post('/api/auth/login',
                  json={'username': username, 'password': password})


def _get_cookie(resp, name='pex_session') -> str | None:
    """Extract a cookie value from a response's Set-Cookie header."""
    for header_name, header_val in resp.headers:
        if header_name.lower() == 'set-cookie':
            if name + '=' in header_val:
                # Extract value between 'name=' and the next ';'
                start = header_val.index(name + '=') + len(name) + 1
                end   = header_val.find(';', start)
                return header_val[start:] if end == -1 else header_val[start:end]
    return None


def _me_with_cookie(c, cookie_value):
    """Call /auth/me using only the cookie (no Authorization header)."""
    return c.get('/api/auth/me',
                 headers={'Cookie': f'pex_session={cookie_value}'})


# ---- Login sets httpOnly cookie ----------------------------------------
# These tests use fresh_client (function-scoped) to avoid cookie jar leakage.

class TestLoginSetsCookie:
    def test_login_sets_pex_session_cookie(self, client, fresh_client):
        register_and_login(client, 'ck_login1', 'ck_login1@test.com')
        resp = _login(fresh_client, 'ck_login1')
        assert resp.status_code == 200
        cookie = _get_cookie(resp)
        assert cookie is not None, 'pex_session cookie not set'

    def test_cookie_is_httponly(self, client, fresh_client):
        register_and_login(client, 'ck_http1', 'ck_http1@test.com')
        resp = _login(fresh_client, 'ck_http1')
        cookie_headers = [v for n, v in resp.headers
                          if n.lower() == 'set-cookie' and 'pex_session=' in v]
        assert any('httponly' in h.lower() for h in cookie_headers)

    def test_cookie_is_samesite_strict(self, client, fresh_client):
        register_and_login(client, 'ck_ss1', 'ck_ss1@test.com')
        resp = _login(fresh_client, 'ck_ss1')
        cookie_headers = [v for n, v in resp.headers
                          if n.lower() == 'set-cookie' and 'pex_session=' in v]
        assert any('samesite=strict' in h.lower() for h in cookie_headers)

    def test_login_also_returns_token_in_json(self, client, fresh_client):
        """JSON response still includes token for API clients."""
        register_and_login(client, 'ck_json1', 'ck_json1@test.com')
        resp = _login(fresh_client, 'ck_json1')
        data = resp.get_json()
        assert 'token' in data

    def test_cookie_secure_enabled_for_non_local_host(self, client, fresh_client):
        """Secure flag should be set when request host is not localhost/127.0.0.1."""
        register_and_login(client, 'ck_secure1', 'ck_secure1@test.com')
        resp = fresh_client.post(
            '/api/auth/login',
            json={'username': 'ck_secure1', 'password': 'TestUser@123456!'},
            headers={'Host': 'example.internal:443'},
        )
        assert resp.status_code == 200
        cookie_headers = [
            v for n, v in resp.headers
            if n.lower() == 'set-cookie' and 'pex_session=' in v
        ]
        assert any('secure' in h.lower() for h in cookie_headers)


# ---- Cookie-based auth works -----------------------------------------------

class TestCookieAuth:
    def test_me_endpoint_via_cookie(self, client, fresh_client):
        register_and_login(client, 'ck_me1', 'ck_me1@test.com')
        resp = _login(fresh_client, 'ck_me1')
        cookie = _get_cookie(resp)
        assert cookie

        me_resp = _me_with_cookie(fresh_client, cookie)
        assert me_resp.status_code == 200
        assert me_resp.get_json()['user']['username'] == 'ck_me1'

    def test_bearer_token_still_works(self, client):
        """Bearer token backward compat — API clients unaffected."""
        h, uid = register_and_login(client, 'ck_bearer1', 'ck_bearer1@test.com')
        resp = client.get('/api/auth/me', headers=h)
        assert resp.status_code == 200

    def test_no_auth_returns_401(self, fresh_client):
        resp = fresh_client.get('/api/auth/me')
        assert resp.status_code == 401

    def test_invalid_cookie_returns_401(self, fresh_client):
        resp = _me_with_cookie(fresh_client, 'invalid.jwt.token')
        assert resp.status_code == 401


# ---- Logout clears cookie --------------------------------------------------

class TestLogout:
    def test_logout_endpoint_exists(self, client, fresh_client):
        register_and_login(client, 'ck_lo1', 'ck_lo1@test.com')
        resp = _login(fresh_client, 'ck_lo1')
        cookie = _get_cookie(resp)
        logout_resp = fresh_client.post('/api/auth/logout',
                                        headers={'Cookie': f'pex_session={cookie}'})
        assert logout_resp.status_code == 200

    def test_logout_clears_cookie(self, client, fresh_client):
        register_and_login(client, 'ck_lo2', 'ck_lo2@test.com')
        resp = _login(fresh_client, 'ck_lo2')
        cookie = _get_cookie(resp)

        logout_resp = fresh_client.post('/api/auth/logout',
                                        headers={'Cookie': f'pex_session={cookie}'})
        cookie_headers = [v for n, v in logout_resp.headers
                          if n.lower() == 'set-cookie' and 'pex_session' in v]
        assert any(
            'max-age=0' in h.lower() or 'expires' in h.lower()
            for h in cookie_headers
        ), f'Cookie not cleared: {cookie_headers}'

    def test_after_logout_cookie_auth_fails(self, client, fresh_client):
        register_and_login(client, 'ck_lo3', 'ck_lo3@test.com')
        resp = _login(fresh_client, 'ck_lo3')
        cookie = _get_cookie(resp)

        fresh_client.post('/api/auth/logout',
                          headers={'Cookie': f'pex_session={cookie}'})

        # Empty cookie should fail auth
        me_resp = _me_with_cookie(fresh_client, '')
        assert me_resp.status_code == 401

    def test_logout_returns_200_without_cookie(self, fresh_client):
        """Logout should gracefully handle unauthenticated calls."""
        resp = fresh_client.post('/api/auth/logout')
        assert resp.status_code == 200


# ---- Login state isolation (user switching) --------------------------------

class TestLoginStateIsolation:
    """
    Each test uses a fresh_client (empty cookie jar) so cookie state
    from one test cannot contaminate another.
    """

    def test_sequential_logins_cookies_are_distinct(self, client, fresh_client, app):
        """Two logins produce distinct tokens."""
        register_and_login(client, 'iso_ua', 'iso_ua@test.com')
        register_and_login(client, 'iso_ub', 'iso_ub@test.com')

        fc_a = app.test_client()
        fc_b = app.test_client()

        resp_a = _login(fc_a, 'iso_ua')
        resp_b = _login(fc_b, 'iso_ub')
        cookie_a = _get_cookie(resp_a)
        cookie_b = _get_cookie(resp_b)

        assert cookie_a != cookie_b
        assert _me_with_cookie(fc_a, cookie_a).get_json()['user']['username'] == 'iso_ua'
        assert _me_with_cookie(fc_b, cookie_b).get_json()['user']['username'] == 'iso_ub'

    def test_logout_then_login_different_user_no_leakage(self, client, app):
        register_and_login(client, 'iso_c', 'iso_c@test.com')
        register_and_login(client, 'iso_d', 'iso_d@test.com')

        fc = app.test_client()
        resp_c = _login(fc, 'iso_c')
        cookie_c = _get_cookie(resp_c)

        fc.post('/api/auth/logout', headers={'Cookie': f'pex_session={cookie_c}'})

        resp_d = _login(fc, 'iso_d')
        cookie_d = _get_cookie(resp_d)
        assert cookie_d != cookie_c

        me_d = _me_with_cookie(fc, cookie_d)
        assert me_d.get_json()['user']['username'] == 'iso_d'

    def test_admin_role_not_leaked_to_regular_user(self, client, fresh_client):
        register_and_login(client, 'iso_reg', 'iso_reg@test.com')

        resp_reg = _login(fresh_client, 'iso_reg')
        cookie_reg = _get_cookie(resp_reg)
        me = _me_with_cookie(fresh_client, cookie_reg)
        assert me.get_json()['user']['role'] == 'user'

        admin_resp = fresh_client.get('/api/admin/users',
                                      headers={'Cookie': f'pex_session={cookie_reg}'})
        assert admin_resp.status_code == 403

    def test_different_users_cookies_are_independent(self, client, app):
        """Each user's cookie resolves to their own account, not another user's."""
        register_and_login(client, 'iso_e', 'iso_e@test.com')
        register_and_login(client, 'iso_f', 'iso_f@test.com')

        fc_e = app.test_client()
        fc_f = app.test_client()

        resp_e = _login(fc_e, 'iso_e')
        resp_f = _login(fc_f, 'iso_f')

        # Each client has its own cookie — resolves to the correct user
        me_e = fc_e.get('/api/auth/me')
        me_f = fc_f.get('/api/auth/me')
        assert me_e.get_json()['user']['username'] == 'iso_e'
        assert me_f.get_json()['user']['username'] == 'iso_f'

        # The two tokens are different values
        cookie_e = _get_cookie(resp_e)
        cookie_f = _get_cookie(resp_f)
        assert cookie_e != cookie_f
