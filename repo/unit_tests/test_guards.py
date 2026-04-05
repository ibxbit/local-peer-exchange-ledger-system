"""
Unit tests for guard_is_verified — shared guard path for matching actions.

Covers:
  - Unverified user (no record)       → blocked
  - User with pending verification    → blocked
  - User with rejected verification   → blocked
  - User with verified verification   → allowed
  - guard_is_verified is called before guard_can_act in join_queue,
    request_session, and auto_match
"""

import pytest
from app.services.guards import guard_is_verified
from app.services import matching_service
from app.utils import utcnow


# ---- Helpers ----------------------------------------------------------------

def _insert_verification(conn, user_id, status='verified', admin_id=1):
    reviewed_at = utcnow() if status == 'verified' else None
    reviewer_id = admin_id if status == 'verified' else None
    conn.execute(
        "INSERT INTO identity_verifications "
        "(user_id, document_type, document_data_enc, document_fingerprint, "
        " content_type, file_size_bytes, status, submitted_at, reviewed_at, reviewer_id) "
        "VALUES (?, 'passport', 'enc_stub', 'fp_stub', 'image/jpeg', 1024, ?, ?, ?, ?)",
        (user_id, status, utcnow(), reviewed_at, reviewer_id)
    )
    conn.commit()


def _credit_user(conn, uid, amount=500.0):
    conn.execute('UPDATE users SET credit_balance=? WHERE id=?', (amount, uid))
    conn.commit()


# ---- guard_is_verified unit tests -------------------------------------------

class TestGuardIsVerified:
    def test_no_verification_record_blocked(self, conn, user_id):
        ok, reason = guard_is_verified(conn, user_id)
        assert not ok
        assert 'verification' in reason.lower()

    def test_pending_verification_blocked(self, conn, user_id):
        _insert_verification(conn, user_id, status='pending')
        ok, reason = guard_is_verified(conn, user_id)
        assert not ok
        assert 'verification' in reason.lower()

    def test_rejected_verification_blocked(self, conn, user_id):
        _insert_verification(conn, user_id, status='rejected')
        ok, reason = guard_is_verified(conn, user_id)
        assert not ok

    def test_verified_user_allowed(self, conn, user_id, admin_id):
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        ok, reason = guard_is_verified(conn, user_id)
        assert ok
        assert reason == ''

    def test_latest_record_is_used(self, conn, user_id, admin_id):
        """When a user has multiple verification records, the latest wins."""
        _insert_verification(conn, user_id, status='rejected')
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        ok, _ = guard_is_verified(conn, user_id)
        assert ok

    def test_verified_then_rejected_uses_latest(self, conn, user_id, admin_id):
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        _insert_verification(conn, user_id, status='rejected')
        ok, _ = guard_is_verified(conn, user_id)
        assert not ok


# ---- Verification gate in matching_service ----------------------------------

class TestVerificationGateInService:
    """
    Ensure guard_is_verified is enforced in the shared guard path,
    not just at the route level (cannot be bypassed).
    """

    def test_join_queue_unverified_raises(self, conn, user_id):
        _credit_user(conn, user_id)
        with pytest.raises(PermissionError, match='verification'):
            matching_service.join_queue(conn, user_id, 'python')

    def test_join_queue_verified_succeeds(self, conn, user_id, admin_id):
        _credit_user(conn, user_id)
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        eid = matching_service.join_queue(conn, user_id, 'python')
        assert eid > 0

    def test_request_session_unverified_raises(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        with pytest.raises(PermissionError, match='verification'):
            matching_service.request_session(
                conn, user_id, user2_id, 'Test', None, 0.0, None
            )

    def test_request_session_verified_succeeds(self, conn, user_id, user2_id, admin_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        sid = matching_service.request_session(
            conn, user_id, user2_id, 'Test', None, 0.0, None
        )
        assert sid > 0

    def test_auto_match_unverified_raises(self, conn, user_id):
        _credit_user(conn, user_id)
        with pytest.raises(PermissionError, match='verification'):
            matching_service.auto_match(conn, user_id, 'python')

    def test_auto_match_verified_succeeds_no_match(self, conn, user_id, admin_id):
        """Returns None (no peer found) rather than raising."""
        _credit_user(conn, user_id)
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        result = matching_service.auto_match(conn, user_id, 'python')
        assert result is None

    def test_scheduler_expires_unverified_entries(self, conn, user_id):
        """run_auto_match_cycle should expire queue entries of unverified users."""
        _credit_user(conn, user_id)
        # Directly insert a waiting entry (bypassing service layer)
        from app.dal import matching_dal
        from datetime import datetime, timezone, timedelta
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
        conn.commit()
        summary = matching_service.run_auto_match_cycle(conn)
        # Unverified user's entry should be expired
        assert summary['expired'] >= 1

    def test_scheduler_does_not_expire_verified_entries(self, conn, user_id, admin_id):
        """Verified user's waiting entry should NOT be expired by the scheduler."""
        _credit_user(conn, user_id)
        _insert_verification(conn, user_id, status='verified', admin_id=admin_id)
        from datetime import datetime, timezone, timedelta
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
        conn.commit()
        summary = matching_service.run_auto_match_cycle(conn)
        # Entry is still waiting — not expired due to missing verification
        assert summary['expired'] == 0
