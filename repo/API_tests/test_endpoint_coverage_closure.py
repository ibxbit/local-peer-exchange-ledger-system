"""
Endpoint-coverage closure tests.

These tests close the coverage gap flagged by the coverage audit for the
following endpoints by exercising each one through the real Flask test
client (no service/controller mocking) with:

    * explicit method + resolved URL
    * meaningful request body / query params
    * meaningful response content assertions (not just status code)
    * positive AND negative (auth / permission / validation / not-found) cases

Endpoints covered:

    DELETE /api/admin/permissions/<int:target_admin_id>/<resource>
    PUT    /api/admin/permissions/<int:target_admin_id>/<resource>
    GET    /api/analytics/reports/<report_date>
    PUT    /api/matching/profile
    GET    /api/matching/queue
    GET    /api/matching/sessions
    GET    /<path:path>                              (SPA catch-all route)

The helpers mirror the style already used in
``API_tests/test_missing_routes_coverage.py`` so these tests integrate
cleanly with the existing session-scoped client and SQLite DB.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

from API_tests.conftest import register_and_login


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _credit(client, admin_headers, uid, amount: float = 500.0) -> None:
    client.post(
        '/api/ledger/credit',
        headers=admin_headers,
        json={'user_id': uid, 'amount': amount,
              'description': 'coverage-closure top-up'},
    )


def _verify(client, admin_headers, headers) -> None:
    """Submit + admin-approve a minimal verification document."""
    pdf = b'%PDF-1.4 coverage'
    hdrs = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
    submit = client.post(
        '/api/verification/submit',
        headers=hdrs,
        data={'document_type': 'passport',
              'document': (io.BytesIO(pdf), 'doc.pdf', 'application/pdf')},
        content_type='multipart/form-data',
    )
    if submit.status_code != 201:
        return
    vid = submit.get_json()['verification_id']
    client.put(
        f'/api/verification/{vid}/review',
        headers=admin_headers,
        json={'decision': 'verified', 'notes': 'auto'},
    )


def _promote_to_admin(client, admin_headers, uid: int) -> None:
    resp = client.put(
        f'/api/users/{uid}/role',
        headers=admin_headers,
        json={'role': 'admin'},
    )
    assert resp.status_code == 200, resp.data


# ===========================================================================
# PUT /api/admin/permissions/<target_admin_id>/<resource>
# DELETE /api/admin/permissions/<target_admin_id>/<resource>
# ===========================================================================


class TestAdminPermissionsGrantAndRevoke:
    """
    These tests exercise the fine-grained permission grant / revoke pathway
    end-to-end: grant a restricted permission, verify the permission appears
    in GET /permissions/<id>, revoke it, and verify it is gone.
    """

    def test_put_grants_permission_and_get_reflects_it(
            self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_perm_grant',
                                    'cc_perm_grant@test.com')
        _promote_to_admin(client, admin_headers, uid)

        put_resp = client.put(
            f'/api/admin/permissions/{uid}/users',
            headers=admin_headers,
            json={'can_write': False,
                  'scope': {'role': ['user']}},
        )
        assert put_resp.status_code == 200, put_resp.data
        assert put_resp.get_json() == {
            'message': 'Permission granted: users.'
        }

        get_resp = client.get(
            f'/api/admin/permissions/{uid}', headers=admin_headers)
        assert get_resp.status_code == 200
        body = get_resp.get_json()
        assert body['admin_id'] == uid
        perms = {p['resource']: p for p in body['permissions']}
        assert 'users' in perms, (
            f'granted resource not present in GET: {body}')
        assert perms['users']['can_write'] in (0, False)

    def test_put_can_upgrade_existing_permission_to_write(
            self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_perm_upgrade',
                                    'cc_perm_upgrade@test.com')
        _promote_to_admin(client, admin_headers, uid)

        # Grant read-only first
        client.put(f'/api/admin/permissions/{uid}/violations',
                   headers=admin_headers,
                   json={'can_write': False})
        # Upgrade to write
        up = client.put(f'/api/admin/permissions/{uid}/violations',
                        headers=admin_headers,
                        json={'can_write': True})
        assert up.status_code == 200

        get_resp = client.get(
            f'/api/admin/permissions/{uid}', headers=admin_headers)
        perms = {p['resource']: p for p in get_resp.get_json()['permissions']}
        assert 'violations' in perms
        assert perms['violations']['can_write'] in (1, True)

    def test_put_rejects_unknown_resource(self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_perm_badres',
                                    'cc_perm_badres@test.com')
        _promote_to_admin(client, admin_headers, uid)

        resp = client.put(
            f'/api/admin/permissions/{uid}/not-a-real-resource',
            headers=admin_headers,
            json={'can_write': False},
        )
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_put_non_admin_forbidden(self, client, user_headers):
        resp = client.put(
            '/api/admin/permissions/1/users',
            headers=user_headers,
            json={'can_write': False},
        )
        assert resp.status_code == 403
        assert resp.get_json()['error']

    def test_put_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.put(
            '/api/admin/permissions/1/users', json={'can_write': False})
        assert resp.status_code == 401

    def test_delete_revokes_permission_and_get_removes_it(
            self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_perm_revoke',
                                    'cc_perm_revoke@test.com')
        _promote_to_admin(client, admin_headers, uid)

        # Seed a permission to revoke
        grant = client.put(
            f'/api/admin/permissions/{uid}/ledger',
            headers=admin_headers,
            json={'can_write': True})
        assert grant.status_code == 200

        # Confirm it's present before revocation
        before = client.get(f'/api/admin/permissions/{uid}',
                            headers=admin_headers).get_json()
        assert any(p['resource'] == 'ledger' for p in before['permissions'])

        # Revoke
        del_resp = client.delete(
            f'/api/admin/permissions/{uid}/ledger', headers=admin_headers)
        assert del_resp.status_code == 200
        assert del_resp.get_json() == {
            'message': 'Permission revoked: ledger.'
        }

        # Now absent
        after = client.get(f'/api/admin/permissions/{uid}',
                           headers=admin_headers).get_json()
        assert not any(p['resource'] == 'ledger' for p in after['permissions'])

    def test_delete_missing_permission_rejected(self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_perm_missing',
                                    'cc_perm_missing@test.com')
        _promote_to_admin(client, admin_headers, uid)
        resp = client.delete(
            f'/api/admin/permissions/{uid}/audit', headers=admin_headers)
        assert resp.status_code == 400

    def test_delete_non_admin_forbidden(self, client, user_headers):
        resp = client.delete(
            '/api/admin/permissions/1/users', headers=user_headers)
        assert resp.status_code == 403

    def test_delete_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.delete('/api/admin/permissions/1/users')
        assert resp.status_code == 401


# ===========================================================================
# GET /api/analytics/reports/<report_date>
# ===========================================================================


class TestAnalyticsGetReportByDate:
    """
    The handler accepts either a literal ``YYYY-MM-DD`` or the sentinel
    ``latest`` and streams the saved CSV back as a download. These tests
    exercise:
      - happy path: generate a report for a specific date, fetch it, get the
        CSV bytes and the attachment header back.
      - 'latest' alias returns the most recently saved report.
      - missing report returns a 404 JSON body with an 'error' field.
      - role enforcement (user forbidden, unauthenticated rejected).
    """

    def test_get_report_by_explicit_date_returns_csv_download(
            self, client, admin_headers):
        target = (date.today() - timedelta(days=5)).isoformat()
        gen = client.post(
            '/api/analytics/reports/generate',
            headers=admin_headers,
            json={'date': target},
        )
        assert gen.status_code == 201, gen.data

        resp = client.get(
            f'/api/analytics/reports/{target}', headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content_type.startswith('text/csv')
        disposition = resp.headers.get('Content-Disposition', '')
        assert 'attachment' in disposition
        assert disposition.endswith('.csv"') or '.csv' in disposition
        # Response must be the saved CSV payload, not an empty / JSON body
        assert len(resp.data) > 0
        assert b'error' not in resp.data[:64].lower()

    def test_get_report_latest_alias_returns_csv(
            self, client, admin_headers):
        # Ensure at least one report exists
        client.post('/api/analytics/reports/generate',
                    headers=admin_headers, json={})
        resp = client.get(
            '/api/analytics/reports/latest', headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content_type.startswith('text/csv')
        assert 'attachment' in resp.headers.get('Content-Disposition', '')

    def test_get_report_unknown_date_returns_json_404(
            self, client, admin_headers):
        resp = client.get(
            '/api/analytics/reports/1999-12-31', headers=admin_headers)
        assert resp.status_code == 404
        body = resp.get_json()
        assert body and 'error' in body
        assert 'not found' in body['error'].lower()

    def test_get_report_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/analytics/reports/2024-01-01',
                          headers=user_headers)
        assert resp.status_code == 403

    def test_get_report_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/analytics/reports/2024-01-01')
        assert resp.status_code == 401


# ===========================================================================
# PUT /api/matching/profile
# ===========================================================================


class TestMatchingProfilePut:
    """
    The matching profile endpoint is wired to both POST and PUT (idempotent
    upsert semantics). These tests pin the PUT variant so it stays covered:
      - PUT creates a profile for a user with no existing row.
      - PUT updates an existing profile (subsequent PUT overrides prior data).
      - PUT validates the payload (invalid category → 400).
      - PUT requires authentication.
    """

    def test_put_creates_profile_when_absent(self, client, admin_headers):
        h, uid = register_and_login(
            client, 'cc_prof_create', 'cc_prof_create@test.com')
        _credit(client, admin_headers, uid)

        resp = client.put(
            '/api/matching/profile',
            headers=h,
            json={
                'skills_offered': ['TypeScript', 'React'],
                'skills_needed':  ['Rust'],
                'bio':            'Frontend engineer · PUT-created',
                'tags':           ['frontend', 'web'],
                'preferred_time_slots': ['weekday-evening'],
                'category':       'Technology',
                'is_active':      True,
            },
        )
        assert resp.status_code == 200, resp.data
        assert resp.get_json() == {'message': 'Profile saved.'}

        # Profile is now retrievable via GET and matches the PUT payload
        got = client.get('/api/matching/profile', headers=h).get_json()
        prof = got['profile']
        assert prof is not None
        assert sorted(prof['skills_offered']) == ['React', 'TypeScript']
        assert prof['skills_needed'] == ['Rust']
        assert prof['bio'].startswith('Frontend engineer')
        assert 'frontend' in prof['tags']
        assert 'weekday-evening' in prof['preferred_time_slots']
        assert prof['category'] == 'Technology'

    def test_put_updates_existing_profile(self, client, admin_headers):
        h, uid = register_and_login(
            client, 'cc_prof_update', 'cc_prof_update@test.com')
        _credit(client, admin_headers, uid)

        # Seed via POST (existing idempotent upsert)
        client.post('/api/matching/profile', headers=h, json={
            'skills_offered': ['Python'],
            'bio': 'v1',
            'is_active': True,
        })

        # PUT overrides with new payload
        resp = client.put('/api/matching/profile', headers=h, json={
            'skills_offered': ['Go'],
            'skills_needed':  ['Python'],
            'bio': 'v2 via PUT',
            'is_active': True,
        })
        assert resp.status_code == 200

        prof = client.get('/api/matching/profile',
                          headers=h).get_json()['profile']
        assert prof['skills_offered'] == ['Go']
        assert prof['skills_needed'] == ['Python']
        assert prof['bio'] == 'v2 via PUT'

    def test_put_blocked_when_user_banned(self, client, admin_headers):
        h, uid = register_and_login(
            client, 'cc_prof_banned', 'cc_prof_banned@test.com')
        # Ban first so guard_is_active will reject the upsert
        ban = client.put(f'/api/admin/users/{uid}/ban',
                         headers=admin_headers,
                         json={'reason': 'coverage test'})
        assert ban.status_code == 200

        resp = client.put('/api/matching/profile', headers=h, json={
            'skills_offered': ['Python'],
            'is_active': True,
        })
        # Banned users fail guard_is_active → PermissionError → 400 per route.
        # login_required still allows token-based requests for banned users
        # because the ban is enforced per-action, not at login token level.
        assert resp.status_code in (400, 401, 403)
        assert 'error' in (resp.get_json() or {})

    def test_put_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.put('/api/matching/profile',
                          json={'skills_offered': []})
        assert resp.status_code == 401


# ===========================================================================
# GET /api/matching/queue
# ===========================================================================


class TestMatchingQueueList:
    """
    Listing queue entries: regular users see only their own waiting entries,
    admin sees all entries across the platform. Supports pagination + status
    filter.
    """

    def test_user_sees_only_own_queue_entries(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'cc_q_u1', 'cc_q_u1@test.com')
        h2, uid2 = register_and_login(client, 'cc_q_u2', 'cc_q_u2@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)

        j1 = client.post('/api/matching/queue', headers=h1,
                         json={'skill': 'cc-q-skill-one'})
        j2 = client.post('/api/matching/queue', headers=h2,
                         json={'skill': 'cc-q-skill-two'})
        assert j1.status_code == 201 and j2.status_code == 201
        eid1 = j1.get_json()['entry_id']
        eid2 = j2.get_json()['entry_id']

        resp = client.get('/api/matching/queue', headers=h1)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'queue' in body and 'total' in body

        my_ids = {e['id'] for e in body['queue']}
        assert eid1 in my_ids
        assert eid2 not in my_ids  # never leak other users' entries
        for row in body['queue']:
            assert row['user_id'] == uid1

    def test_admin_sees_all_queue_entries(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'cc_q_all1',
                                       'cc_q_all1@test.com')
        _credit(client, admin_headers, uid1)
        _verify(client, admin_headers, h1)
        join = client.post('/api/matching/queue', headers=h1,
                           json={'skill': 'cc-q-admin-view'})
        assert join.status_code == 201
        eid = join.get_json()['entry_id']

        resp = client.get('/api/matching/queue', headers=admin_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        ids = [e['id'] for e in body['queue']]
        assert eid in ids
        # Admin view may include entries belonging to different users
        user_ids = {e['user_id'] for e in body['queue']}
        assert uid1 in user_ids

    def test_status_filter_applied(self, client, admin_headers):
        h, uid = register_and_login(client, 'cc_q_status',
                                    'cc_q_status@test.com')
        _credit(client, admin_headers, uid)
        _verify(client, admin_headers, h)
        join = client.post('/api/matching/queue', headers=h,
                           json={'skill': 'cc-q-status-filter'})
        eid = join.get_json()['entry_id']
        # Cancel so we have a non-waiting entry
        client.put(f'/api/matching/queue/{eid}/cancel', headers=h)

        waiting = client.get('/api/matching/queue?status=waiting',
                             headers=h).get_json()
        cancelled = client.get('/api/matching/queue?status=cancelled',
                               headers=h).get_json()
        assert all(e['status'] == 'waiting' for e in waiting['queue'])
        assert any(e['id'] == eid for e in cancelled['queue'])

    def test_queue_list_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/matching/queue')
        assert resp.status_code == 401


# ===========================================================================
# GET /api/matching/sessions
# ===========================================================================


class TestMatchingSessionsList:
    """
    Returns the calling user's sessions (as initiator or participant, or both).
    """

    def test_initiator_sees_created_session(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'cc_s_init',
                                      'cc_s_init@test.com')
        h2, uid2 = register_and_login(client, 'cc_s_part',
                                      'cc_s_part@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)

        create = client.post('/api/matching/sessions', headers=h1,
                             json={'participant_id': uid2,
                                   'description': 'cc-s-listing',
                                   'credit_amount': 0})
        assert create.status_code == 201
        sid = create.get_json()['session_id']

        resp = client.get('/api/matching/sessions', headers=h1)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'sessions' in body and 'total' in body
        sess_ids = [s['id'] for s in body['sessions']]
        assert sid in sess_ids

        the_session = next(s for s in body['sessions'] if s['id'] == sid)
        assert the_session['initiator_id'] == uid1
        assert the_session['participant_id'] == uid2
        assert the_session['status'] == 'pending'

    def test_role_filter_limits_to_initiator(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'cc_s_role_i',
                                      'cc_s_role_i@test.com')
        h2, uid2 = register_and_login(client, 'cc_s_role_p',
                                      'cc_s_role_p@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)

        # h1 initiates one, h2 initiates another (with h1 as participant)
        s1 = client.post('/api/matching/sessions', headers=h1,
                         json={'participant_id': uid2,
                               'description': 'cc-s-i1',
                               'credit_amount': 0}).get_json()['session_id']
        s2 = client.post('/api/matching/sessions', headers=h2,
                         json={'participant_id': uid1,
                               'description': 'cc-s-i2',
                               'credit_amount': 0}).get_json()['session_id']

        as_init = client.get('/api/matching/sessions?role=initiator',
                             headers=h1).get_json()
        as_part = client.get('/api/matching/sessions?role=participant',
                             headers=h1).get_json()

        init_ids = {s['id'] for s in as_init['sessions']}
        part_ids = {s['id'] for s in as_part['sessions']}
        assert s1 in init_ids and s1 not in part_ids
        assert s2 in part_ids and s2 not in init_ids

    def test_status_filter_applied(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'cc_s_filt_a',
                                      'cc_s_filt_a@test.com')
        h2, uid2 = register_and_login(client, 'cc_s_filt_b',
                                      'cc_s_filt_b@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)
        _verify(client, admin_headers, h2)

        sid = client.post('/api/matching/sessions', headers=h1,
                          json={'participant_id': uid2,
                                'description': 'cc-s-filter',
                                'credit_amount': 0}
                          ).get_json()['session_id']

        pending = client.get('/api/matching/sessions?status=pending',
                             headers=h1).get_json()
        completed = client.get('/api/matching/sessions?status=completed',
                               headers=h1).get_json()
        assert any(s['id'] == sid for s in pending['sessions'])
        assert all(s['status'] == 'pending' for s in pending['sessions'])
        assert not any(s['id'] == sid for s in completed['sessions'])

    def test_sessions_list_unauthenticated_rejected(self, client):
        client.delete_cookie('pex_session', path='/', domain='localhost')
        resp = client.get('/api/matching/sessions')
        assert resp.status_code == 401


# ===========================================================================
# GET /<path:path>   (SPA catch-all route)
# ===========================================================================


class TestSPACatchAllRoute:
    """
    The SPA entrypoint registered on the application factory matches every
    path that no blueprint handled and renders ``templates/index.html``.
    The audit-flagged coverage gap is specifically the parameterised
    ``/<path:path>`` branch; the following tests exercise it directly.
    """

    def test_deep_client_route_renders_spa_shell(self, client):
        resp = client.get('/dashboard/users/42')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        # The SPA template is index.html — it always loads app.js
        assert b'app.js' in resp.data or b'pex' in resp.data.lower()

    def test_unknown_top_level_path_renders_spa(self, client):
        resp = client.get('/this-route-does-not-exist')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type

    def test_root_serves_same_template(self, client):
        """
        ``/`` is handled by the ``defaults={'path': ''}`` branch of the
        same view function — verifying that the responses for ``/`` and
        ``/<path>`` share the SPA shell.
        """
        root = client.get('/')
        deep = client.get('/anywhere/at/all')
        assert root.status_code == 200
        assert deep.status_code == 200
        # Both should return HTML of the same SPA template
        assert 'text/html' in root.content_type
        assert 'text/html' in deep.content_type
        # SPA shell response bodies should be identical (same rendered template)
        assert root.data == deep.data

    def test_api_prefixed_unknown_path_falls_through_to_spa(self, client):
        # Documents the intended fall-through behaviour: unknown /api/* URLs
        # hit the SPA catch-all (no separate JSON 404 layer).
        resp = client.get('/api/totally-made-up-endpoint')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
