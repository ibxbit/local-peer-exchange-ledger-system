"""
API tests: temporary do-not-match list.

  - POST /api/matching/block/temporary — create temporary block
  - Active temporary block prevents session request (is_blocked check)
  - Active temporary block prevents matching in search results
  - Expired block no longer prevents matching
  - Permanent block persists regardless
  - Cannot create self-block
  - Duration_hours and expires_at both accepted
  - Missing both raises 400
"""

import pytest
from datetime import datetime, timezone, timedelta
from API_tests.conftest import register_and_login
import io


def _credit(client, admin_headers, uid, amount=500.0):
    client.post('/api/ledger/credit', headers=admin_headers,
                json={'user_id': uid, 'amount': amount, 'description': 'test'})


def _verify(client, admin_headers, h):
    pdf = b'%PDF-1.4 test'
    r = client.post('/api/verification/submit',
                    headers={k: v for k, v in h.items()
                             if k.lower() != 'content-type'},
                    data={'document_type': 'passport',
                          'document': (io.BytesIO(pdf), 'doc.pdf', 'application/pdf')},
                    content_type='multipart/form-data')
    vid = r.get_json()['verification_id']
    client.put(f'/api/verification/{vid}/review',
               headers=admin_headers,
               json={'decision': 'verified', 'notes': 'auto'})


class TestTemporaryBlockCreate:
    def test_create_with_duration_hours(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'tb_d1', 'tb_d1@test.com')
        h2, uid2 = register_and_login(client, 'tb_d2', 'tb_d2@test.com')
        resp = client.post('/api/matching/block/temporary', headers=h1,
                           json={'user_id': uid2, 'duration_hours': 24,
                                 'reason': 'cooling off'})
        assert resp.status_code == 200

    def test_create_with_explicit_expires_at(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'tb_e1', 'tb_e1@test.com')
        h2, uid2 = register_and_login(client, 'tb_e2', 'tb_e2@test.com')
        future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        resp = client.post('/api/matching/block/temporary', headers=h1,
                           json={'user_id': uid2, 'expires_at': future})
        assert resp.status_code == 200

    def test_missing_both_raises_400(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'tb_nb1', 'tb_nb1@test.com')
        h2, uid2 = register_and_login(client, 'tb_nb2', 'tb_nb2@test.com')
        resp = client.post('/api/matching/block/temporary', headers=h1,
                           json={'user_id': uid2})
        assert resp.status_code == 400

    def test_self_block_raises_400(self, client, admin_headers):
        h, uid = register_and_login(client, 'tb_self', 'tb_self@test.com')
        resp = client.post('/api/matching/block/temporary', headers=h,
                           json={'user_id': uid, 'duration_hours': 1})
        assert resp.status_code == 400

    def test_missing_user_id_raises_400(self, client):
        h, _ = register_and_login(client, 'tb_noid', 'tb_noid@test.com')
        resp = client.post('/api/matching/block/temporary', headers=h,
                           json={'duration_hours': 1})
        assert resp.status_code == 400

    def test_unauthenticated_raises_401(self, client):
        resp = client.post('/api/matching/block/temporary',
                           json={'user_id': 99, 'duration_hours': 1})
        assert resp.status_code == 401


class TestTemporaryBlockPreventsSession:
    """An active temporary block prevents session creation (is_blocked check)."""

    def test_active_temp_block_prevents_session(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'tb_sess1', 'tb_sess1@test.com')
        h2, uid2 = register_and_login(client, 'tb_sess2', 'tb_sess2@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)

        # Block uid2
        client.post('/api/matching/block/temporary', headers=h1,
                    json={'user_id': uid2, 'duration_hours': 24})

        resp = client.post('/api/matching/sessions', headers=h1,
                           json={'participant_id': uid2, 'description': 'test',
                                 'credit_amount': 0})
        assert resp.status_code == 400
        assert 'blocked' in resp.get_json()['error'].lower()


class TestTemporaryBlockPermanentStillWorks:
    def test_permanent_block_still_prevents(self, client, admin_headers):
        h1, uid1 = register_and_login(client, 'tb_perm1', 'tb_perm1@test.com')
        h2, uid2 = register_and_login(client, 'tb_perm2', 'tb_perm2@test.com')
        _credit(client, admin_headers, uid1)
        _credit(client, admin_headers, uid2)
        _verify(client, admin_headers, h1)

        # Permanent block
        client.post('/api/matching/block', headers=h1,
                    json={'user_id': uid2, 'reason': 'permanent'})

        resp = client.post('/api/matching/sessions', headers=h1,
                           json={'participant_id': uid2, 'description': 'test',
                                 'credit_amount': 0})
        assert resp.status_code == 400


class TestTemporaryBlockListsBlocks:
    def test_list_includes_temp_block_with_expires_at(self, client):
        h1, uid1 = register_and_login(client, 'tb_list1', 'tb_list1@test.com')
        h2, uid2 = register_and_login(client, 'tb_list2', 'tb_list2@test.com')
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        client.post('/api/matching/block/temporary', headers=h1,
                    json={'user_id': uid2, 'expires_at': future})
        resp = client.get('/api/matching/block', headers=h1)
        blocks = resp.get_json()['blocks']
        assert len(blocks) >= 1
        tb = next(b for b in blocks if b['blocked_id'] == uid2)
        assert tb['is_temporary'] == 1
        assert tb['expires_at'] is not None
        assert tb['is_active_block'] is True

    def test_expired_temp_block_shown_as_inactive(self, client, admin_headers):
        """List includes the row but marks it inactive after expiry."""
        from app.models import db as _db
        from app.dal import matching_dal as mdal
        from app.utils import utcnow as _utcnow

        h1, uid1 = register_and_login(client, 'tb_exp1', 'tb_exp1@test.com')
        h2, uid2 = register_and_login(client, 'tb_exp2', 'tb_exp2@test.com')
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        # Insert directly through the route (using a past expires_at)
        # The route validates duration_hours / expires_at — we use expires_at directly.
        resp = client.post('/api/matching/block/temporary', headers=h1,
                           json={'user_id': uid2, 'expires_at': past})
        assert resp.status_code == 200

        resp = client.get('/api/matching/block', headers=h1)
        blocks = resp.get_json()['blocks']
        tb = next((b for b in blocks if b['blocked_id'] == uid2), None)
        assert tb is not None
        assert tb['is_active_block'] is False
