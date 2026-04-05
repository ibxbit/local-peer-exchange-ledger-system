"""
Unit tests for temporary do-not-match list (blacklist with expiry).

Covers:
  - add_block with is_temporary=True and a future expires_at → blocks
  - get_blocked_ids excludes expired temporary blocks
  - is_blocked excludes expired temporary blocks
  - Permanent blocks (is_temporary=False) never expire
  - list_blocks annotates is_active_block correctly
  - block_user_temporary service function validates inputs
  - Auto-match cycle respects active temporary blocks
  - Auto-match cycle ignores expired temporary blocks
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.dal import matching_dal
from app.services import matching_service
from app.utils import utcnow


def _future_iso(hours=24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(seconds=1) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _credit_user(conn, uid, amount=500.0):
    conn.execute('UPDATE users SET credit_balance=? WHERE id=?', (amount, uid))
    conn.commit()


def _verify_user(conn, user_id, admin_id=1):
    conn.execute(
        "INSERT INTO identity_verifications "
        "(user_id, document_type, document_data_enc, document_fingerprint, "
        " content_type, file_size_bytes, status, submitted_at, reviewed_at, reviewer_id) "
        "VALUES (?, 'passport', 'enc', 'fp', 'image/jpeg', 512, 'verified', ?, ?, ?)",
        (user_id, utcnow(), utcnow(), admin_id)
    )
    conn.commit()


# ---- DAL-level tests --------------------------------------------------------

class TestTemporaryBlockDAL:
    def test_active_temporary_block_appears_in_blocked_ids(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_future_iso(24))
        conn.commit()
        blocked = matching_dal.get_blocked_ids(conn, user_id)
        assert user2_id in blocked

    def test_expired_temporary_block_excluded_from_blocked_ids(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(5))
        conn.commit()
        blocked = matching_dal.get_blocked_ids(conn, user_id)
        assert user2_id not in blocked

    def test_permanent_block_always_in_blocked_ids(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id, is_temporary=False)
        conn.commit()
        blocked = matching_dal.get_blocked_ids(conn, user_id)
        assert user2_id in blocked

    def test_is_blocked_active_temporary_returns_true(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_future_iso(1))
        conn.commit()
        assert matching_dal.is_blocked(conn, user_id, user2_id)

    def test_is_blocked_expired_temporary_returns_false(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(1))
        conn.commit()
        assert not matching_dal.is_blocked(conn, user_id, user2_id)

    def test_is_blocked_permanent_always_true(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id, is_temporary=False)
        conn.commit()
        assert matching_dal.is_blocked(conn, user_id, user2_id)

    def test_bidirectional_check_reverse(self, conn, user_id, user2_id):
        """Blocking A→B should also block B from seeing A."""
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_future_iso(1))
        conn.commit()
        blocked_by_b = matching_dal.get_blocked_ids(conn, user2_id)
        assert user_id in blocked_by_b

    def test_expired_bidirectional_check_reverse(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(1))
        conn.commit()
        blocked_by_b = matching_dal.get_blocked_ids(conn, user2_id)
        assert user_id not in blocked_by_b

    def test_list_blocks_annotates_is_active_block(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(1))
        conn.commit()
        blocks = matching_dal.list_blocks(conn, user_id)
        assert len(blocks) == 1
        assert blocks[0]['is_active_block'] is False

    def test_list_blocks_active_temp_is_true(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_future_iso(24))
        conn.commit()
        blocks = matching_dal.list_blocks(conn, user_id)
        assert blocks[0]['is_active_block'] is True

    def test_list_blocks_permanent_is_active(self, conn, user_id, user2_id):
        matching_dal.add_block(conn, user_id, user2_id, is_temporary=False)
        conn.commit()
        blocks = matching_dal.list_blocks(conn, user_id)
        assert blocks[0]['is_active_block'] is True

    def test_upsert_replaces_existing_block(self, conn, user_id, user2_id):
        """Re-blocking the same pair replaces the previous entry."""
        matching_dal.add_block(conn, user_id, user2_id, is_temporary=False)
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(1))
        conn.commit()
        assert not matching_dal.is_blocked(conn, user_id, user2_id)


# ---- Service-level tests ----------------------------------------------------

class TestTemporaryBlockService:
    def test_block_user_temporary_with_duration(self, conn, user_id, user2_id):
        matching_service.block_user_temporary(
            conn, user_id, user2_id, reason='test', duration_hours=24
        )
        conn.commit()
        assert matching_dal.is_blocked(conn, user_id, user2_id)

    def test_block_user_temporary_with_explicit_expires_at(self, conn, user_id, user2_id):
        matching_service.block_user_temporary(
            conn, user_id, user2_id,
            expires_at=_future_iso(48)
        )
        conn.commit()
        assert matching_dal.is_blocked(conn, user_id, user2_id)

    def test_block_self_raises(self, conn, user_id):
        with pytest.raises(ValueError, match='yourself'):
            matching_service.block_user_temporary(
                conn, user_id, user_id, duration_hours=1
            )

    def test_neither_duration_nor_expires_raises(self, conn, user_id, user2_id):
        with pytest.raises(ValueError):
            matching_service.block_user_temporary(conn, user_id, user2_id)

    def test_inactive_user_cannot_block(self, conn, user_id, user2_id):
        conn.execute('UPDATE users SET is_active=0 WHERE id=?', (user_id,))
        conn.commit()
        with pytest.raises(PermissionError):
            matching_service.block_user_temporary(
                conn, user_id, user2_id, duration_hours=1
            )


# ---- Auto-match respects temporary blocks -----------------------------------

class TestAutoMatchWithTempBlocks:
    def _setup_verified_users(self, conn, user_id, user2_id, admin_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        _verify_user(conn, user_id, admin_id)
        _verify_user(conn, user2_id, admin_id)

    def test_active_temp_block_prevents_match(self, conn, user_id, user2_id, admin_id):
        self._setup_verified_users(conn, user_id, user2_id, admin_id)
        # Block user2 from matching with user_id
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_future_iso(1))
        conn.commit()

        from datetime import timezone, timedelta
        old_attempt = (
            datetime.now(timezone.utc) -
            timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
        ).isoformat()
        conn.execute(
            "INSERT INTO matching_queue "
            "(user_id, skill, priority, status, retry_count, "
            " last_attempt_at, created_at, updated_at) "
            "VALUES (?, 'python', 0, 'waiting', 0, ?, ?, ?)",
            (user_id, old_attempt, utcnow(), utcnow())
        )
        conn.execute(
            "INSERT INTO matching_queue "
            "(user_id, skill, priority, status, retry_count, "
            " last_attempt_at, created_at, updated_at) "
            "VALUES (?, 'python', 0, 'waiting', 0, ?, ?, ?)",
            (user2_id, old_attempt, utcnow(), utcnow())
        )
        conn.commit()

        summary = matching_service.run_auto_match_cycle(conn)
        assert summary['matched'] == 0  # blocked → no match

    def test_expired_temp_block_allows_match(self, conn, user_id, user2_id, admin_id):
        self._setup_verified_users(conn, user_id, user2_id, admin_id)
        # Expired block — should be ignored
        matching_dal.add_block(conn, user_id, user2_id,
                               is_temporary=True, expires_at=_past_iso(60))
        conn.commit()

        from datetime import timezone, timedelta
        old_attempt = (
            datetime.now(timezone.utc) -
            timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
        ).isoformat()
        conn.execute(
            "INSERT INTO matching_queue "
            "(user_id, skill, priority, status, retry_count, "
            " last_attempt_at, created_at, updated_at) "
            "VALUES (?, 'python', 0, 'waiting', 0, ?, ?, ?)",
            (user_id, old_attempt, utcnow(), utcnow())
        )
        conn.execute(
            "INSERT INTO matching_queue "
            "(user_id, skill, priority, status, retry_count, "
            " last_attempt_at, created_at, updated_at) "
            "VALUES (?, 'python', 0, 'waiting', 0, ?, ?, ?)",
            (user2_id, old_attempt, utcnow(), utcnow())
        )
        conn.commit()

        summary = matching_service.run_auto_match_cycle(conn)
        assert summary['matched'] >= 1  # expired block ignored → match created
