"""API tests for security hardening and schedule inventory endpoints."""

from app.models import db


class TestForcedPasswordRotation:
    def test_must_change_password_blocks_non_auth_routes(self, client):
        client.post('/api/auth/register', json={
            'username': 'rot_user',
            'email': 'rot_user@test.com',
            'password': 'Rotate@Test12345!'
        })

        with db() as conn:
            uid = conn.execute(
                'SELECT id FROM users WHERE username = ?', ('rot_user',)
            ).fetchone()['id']
            conn.execute(
                'UPDATE users SET must_change_password = 1 WHERE id = ?',
                (uid,)
            )

        login = client.post('/api/auth/login', json={
            'username': 'rot_user',
            'password': 'Rotate@Test12345!'
        })
        assert login.status_code == 200
        token = login.get_json()['token']
        h = {'Authorization': f'Bearer {token}'}

        blocked = client.get('/api/ledger/balance', headers=h)
        assert blocked.status_code == 403
        assert blocked.get_json().get('code') == 'password_change_required'

        cp = client.post('/api/auth/change-password', headers=h, json={
            'current_password': 'Rotate@Test12345!',
            'new_password': 'Rotate@Changed12345!'
        })
        assert cp.status_code == 200

        login2 = client.post('/api/auth/login', json={
            'username': 'rot_user',
            'password': 'Rotate@Changed12345!'
        })
        assert login2.status_code == 200
        token2 = login2.get_json()['token']
        h2 = {'Authorization': f'Bearer {token2}'}
        allowed = client.get('/api/ledger/balance', headers=h2)
        assert allowed.status_code == 200


class TestScheduleInventoryEndpoints:
    def test_admin_can_crud_schedule_resources(self, client, admin_headers):
        create = client.post('/api/admin/resources', headers=admin_headers, json={
            'building': 'Main',
            'room': '101',
            'time_slot': 'weekday-evening'
        })
        assert create.status_code == 201
        rid = create.get_json()['resource_id']

        listing = client.get('/api/admin/resources', headers=admin_headers)
        assert listing.status_code == 200
        ids = [r['id'] for r in listing.get_json()['resources']]
        assert rid in ids

        update = client.put(f'/api/admin/resources/{rid}', headers=admin_headers, json={
            'room': '102'
        })
        assert update.status_code == 200

        deactivate = client.delete(f'/api/admin/resources/{rid}', headers=admin_headers)
        assert deactivate.status_code == 200

    def test_non_admin_cannot_manage_schedule_resources(self, client, user_headers):
        denied = client.post('/api/admin/resources', headers=user_headers, json={
            'building': 'Main',
            'room': '201',
            'time_slot': 'weekday-morning'
        })
        assert denied.status_code == 403
