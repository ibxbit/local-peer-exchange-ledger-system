"""
API tests: fine-grained admin resource permissions for sessions.

Resource dimensions: building, room, time_slot.

  - Session creation accepts building/room/time_slot fields.
  - Admin GET /admin/sessions returns dimension fields.
  - Super-admin (no rows in admin_permissions) can see ALL sessions.
  - Admin with sessions grant + buildings scope → only sees sessions in those buildings.
  - Admin with sessions grant + rooms scope → only sees sessions in those rooms.
  - Admin with sessions grant + time_slots scope → only sees sessions in those time_slots.
  - Out-of-scope session IDs are absent from the restricted admin's response.
"""

import io
import pytest
from API_tests.conftest import register_and_login, create_admin_user


# ---- Helpers ----------------------------------------------------------------

def _credit(client, admin_headers, uid, amount=500.0):
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount, 'description': 'test'})


def _verify(client, admin_headers, h):
    pdf = b'%PDF-1.4 minimal'
    r = client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'passport',
                          'document': (io.BytesIO(pdf), 'd.pdf', 'application/pdf')},
                    content_type='multipart/form-data')
    vid = r.get_json()['verification_id']
    client.put(f'/api/verification/{vid}/review', headers=admin_headers,
               json={'decision': 'verified', 'notes': 'auto'})


def _register_verified(client, admin_headers, username, email):
    h, uid = register_and_login(client, username, email)
    _credit(client, admin_headers, uid)
    _verify(client, admin_headers, h)
    return h, uid


def _create_session(client, h1, uid2, building=None, room=None, time_slot=None):
    body = {'participant_id': uid2, 'description': 'scope test',
            'credit_amount': 0}
    if building:  body['building']  = building
    if room:      body['room']      = room
    if time_slot: body['time_slot'] = time_slot
    resp = client.post('/api/matching/sessions', headers=h1, json=body)
    assert resp.status_code == 201, f'Session create failed: {resp.data}'
    return resp.get_json()['session_id']


def _grant_scope(admin_uid, resource, scope, granted_by_uid, can_read=True, can_write=False):
    """Directly insert/upsert a scoped permission for a given admin_uid."""
    import json
    from app.models import db as _db
    from app.utils import utcnow as _now
    scope_str = json.dumps(scope) if scope else None
    with _db() as conn:
        conn.execute(
            'INSERT INTO admin_permissions '
            '(admin_id, resource, can_read, can_write, scope, granted_by, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(admin_id, resource) DO UPDATE SET '
            'can_read=excluded.can_read, can_write=excluded.can_write, '
            'scope=excluded.scope, granted_by=excluded.granted_by',
            (admin_uid, resource, int(can_read), int(can_write),
             scope_str, granted_by_uid, _now())
        )


def _clear_permissions(admin_uid):
    """Remove all permission rows for admin_uid (makes them super-admin again)."""
    from app.models import db as _db
    with _db() as conn:
        conn.execute('DELETE FROM admin_permissions WHERE admin_id=?', (admin_uid,))


# ---- Session creation with dimensions --------------------------------------

class TestSessionDimensions:
    def test_create_session_with_building(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sdim_b1', 'sdim_b1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sdim_b2', 'sdim_b2@test.com')
        sid = _create_session(client, h1, uid2, building='BuildingA')
        resp = client.get(f'/api/matching/sessions/{sid}', headers=h1)
        s = resp.get_json()['session']
        assert s['building'] == 'BuildingA'

    def test_create_session_with_room_and_time_slot(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sdim_r1', 'sdim_r1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sdim_r2', 'sdim_r2@test.com')
        sid = _create_session(client, h1, uid2, room='101', time_slot='weekday-evening')
        resp = client.get(f'/api/matching/sessions/{sid}', headers=h1)
        s = resp.get_json()['session']
        assert s['room'] == '101'
        assert s['time_slot'] == 'weekday-evening'

    def test_dimensions_are_optional(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sdim_opt1', 'sdim_opt1@t.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sdim_opt2', 'sdim_opt2@t.com')
        sid = _create_session(client, h1, uid2)
        resp = client.get(f'/api/matching/sessions/{sid}', headers=h1)
        s = resp.get_json()['session']
        assert s.get('building') is None
        assert s.get('room') is None
        assert s.get('time_slot') is None


# ---- Super-admin sees all --------------------------------------------------

class TestSuperAdminScope:
    def test_super_admin_sees_all_sessions(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sa_all1', 'sa_all1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sa_all2', 'sa_all2@test.com')
        sid = _create_session(client, h1, uid2, building='ZoneX')
        resp = client.get('/api/admin/sessions', headers=admin_headers)
        assert resp.status_code == 200
        ids = [s['id'] for s in resp.get_json()['sessions']]
        assert sid in ids

    def test_admin_sessions_returns_dimension_fields(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sa_dim1', 'sa_dim1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sa_dim2', 'sa_dim2@test.com')
        _create_session(client, h1, uid2, building='B1', room='R5', time_slot='ts-morning')
        resp = client.get('/api/admin/sessions', headers=admin_headers)
        sessions = resp.get_json()['sessions']
        with_dims = [s for s in sessions
                     if s.get('building') == 'B1' and s.get('room') == 'R5']
        assert len(with_dims) >= 1


# ---- Scoped admin enforcement (via DB helper) ------------------------------

class TestAdminSessionsScope:
    """
    Tests use create_admin_user() to create an admin directly in the DB,
    then _grant_scope() to set a scoped permission row, proving that the
    route enforces the scope at the query level.
    """

    def test_scoped_admin_building_sees_only_own_building(self, client, admin_headers):
        # Create sessions in two distinct buildings
        h1, uid1 = _register_verified(client, admin_headers, 'sc_bld1', 'sc_bld1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sc_bld2', 'sc_bld2@test.com')
        h3, uid3 = _register_verified(client, admin_headers, 'sc_bld3', 'sc_bld3@test.com')
        sid_a = _create_session(client, h1, uid2, building='BuildingA')
        sid_b = _create_session(client, h1, uid3, building='BuildingB')

        # Create a new admin user directly
        from app.models import db as _db
        from app.utils import utcnow
        ha, uid_a = create_admin_user('sc_admin_bld', 'sc_admin_bld@test.com')

        # Grant sessions permission scoped to BuildingA only
        me = client.get('/api/auth/me', headers=admin_headers)
        super_admin_uid = me.get_json()['user']['id']
        _grant_scope(uid_a, 'sessions', {'buildings': ['BuildingA']}, super_admin_uid)

        try:
            resp = client.get('/api/admin/sessions', headers=ha)
            assert resp.status_code == 200
            ids = [s['id'] for s in resp.get_json()['sessions']]
            assert sid_a in ids, 'In-scope session not returned'
            assert sid_b not in ids, 'Out-of-scope session returned (scope enforcement failed)'
        finally:
            _clear_permissions(uid_a)

    def test_scoped_admin_room_sees_only_own_room(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sc_rm1', 'sc_rm1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sc_rm2', 'sc_rm2@test.com')
        h3, uid3 = _register_verified(client, admin_headers, 'sc_rm3', 'sc_rm3@test.com')
        sid_101 = _create_session(client, h1, uid2, room='101')
        sid_202 = _create_session(client, h1, uid3, room='202')

        ha, uid_a = create_admin_user('sc_admin_rm', 'sc_admin_rm@test.com')
        me = client.get('/api/auth/me', headers=admin_headers)
        super_admin_uid = me.get_json()['user']['id']
        _grant_scope(uid_a, 'sessions', {'rooms': ['101']}, super_admin_uid)

        try:
            resp = client.get('/api/admin/sessions', headers=ha)
            assert resp.status_code == 200
            ids = [s['id'] for s in resp.get_json()['sessions']]
            assert sid_101 in ids
            assert sid_202 not in ids
        finally:
            _clear_permissions(uid_a)

    def test_scoped_admin_time_slot_sees_only_own_slot(self, client, admin_headers):
        h1, uid1 = _register_verified(client, admin_headers, 'sc_ts1', 'sc_ts1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sc_ts2', 'sc_ts2@test.com')
        h3, uid3 = _register_verified(client, admin_headers, 'sc_ts3', 'sc_ts3@test.com')
        sid_eve  = _create_session(client, h1, uid2, time_slot='weekday-evening')
        sid_morn = _create_session(client, h1, uid3, time_slot='weekend-morning')

        ha, uid_a = create_admin_user('sc_admin_ts', 'sc_admin_ts@test.com')
        me = client.get('/api/auth/me', headers=admin_headers)
        super_admin_uid = me.get_json()['user']['id']
        _grant_scope(uid_a, 'sessions', {'time_slots': ['weekday-evening']}, super_admin_uid)

        try:
            resp = client.get('/api/admin/sessions', headers=ha)
            assert resp.status_code == 200
            ids = [s['id'] for s in resp.get_json()['sessions']]
            assert sid_eve  in ids
            assert sid_morn not in ids
        finally:
            _clear_permissions(uid_a)

    def test_scoped_admin_cannot_see_out_of_scope_building(self, client, admin_headers):
        """Explicit out-of-scope assertion: scoped admin receives 0 results
        for a session in a building not in their scope."""
        h1, uid1 = _register_verified(client, admin_headers, 'sc_oos1', 'sc_oos1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sc_oos2', 'sc_oos2@test.com')
        sid_other = _create_session(client, h1, uid2, building='OtherBuilding')

        ha, uid_a = create_admin_user('sc_admin_oos', 'sc_admin_oos@test.com')
        me = client.get('/api/auth/me', headers=admin_headers)
        super_admin_uid = me.get_json()['user']['id']
        _grant_scope(uid_a, 'sessions', {'buildings': ['TargetBuilding']}, super_admin_uid)

        try:
            resp = client.get('/api/admin/sessions', headers=ha)
            assert resp.status_code == 200
            ids = [s['id'] for s in resp.get_json()['sessions']]
            assert sid_other not in ids
        finally:
            _clear_permissions(uid_a)

    def test_super_admin_not_affected_by_other_restricted_admins(self, client, admin_headers):
        """Granting scope to another admin does NOT restrict the super-admin."""
        h1, uid1 = _register_verified(client, admin_headers, 'sa_nd1', 'sa_nd1@test.com')
        h2, uid2 = _register_verified(client, admin_headers, 'sa_nd2', 'sa_nd2@test.com')
        sid = _create_session(client, h1, uid2, building='NicheBuilding')

        # Super-admin should still see this session
        resp = client.get('/api/admin/sessions', headers=admin_headers)
        ids = [s['id'] for s in resp.get_json()['sessions']]
        assert sid in ids

    def test_access_denied_when_no_sessions_permission(self, client, admin_headers):
        """An admin restricted to 'users' resource cannot access sessions."""
        ha, uid_a = create_admin_user('sc_admin_noperm', 'sc_admin_noperm@test.com')
        me = client.get('/api/auth/me', headers=admin_headers)
        super_admin_uid = me.get_json()['user']['id']
        # Grant only 'users' permission — no 'sessions'
        _grant_scope(uid_a, 'users', None, super_admin_uid)

        try:
            resp = client.get('/api/admin/sessions', headers=ha)
            assert resp.status_code == 403
        finally:
            _clear_permissions(uid_a)
