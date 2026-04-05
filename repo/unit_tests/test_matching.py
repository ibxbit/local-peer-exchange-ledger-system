"""
Unit tests for matching_service: governance rules, auto-match cycle, profiles.

Governance boundaries tested:
  - Queue timeout after 3 minutes
  - Max retries (3) before expiry
  - 30-second cooldown between retry attempts
  - Max 10 queue joins per hour
  - 2-minute cancellation interval
  - Do-not-match list (blacklist) respected in scheduler cycle
  - Extended profile fields (tags, preferred_time_slots, category)
"""

import pytest
from datetime import datetime, timezone, timedelta
from app.services import matching_service
from app.dal import matching_dal
from app.utils import utcnow


# ---- Helpers ------------------------------------------------------------

def _credit_user(conn, uid, amount=500.0):
    conn.execute('UPDATE users SET credit_balance = ? WHERE id = ?', (amount, uid))
    conn.commit()


def _verify_user(conn, user_id, admin_id):
    """Insert a verified identity record so the user passes guard_is_verified."""
    conn.execute(
        "INSERT INTO identity_verifications "
        "(user_id, document_type, document_data_enc, document_fingerprint, "
        " content_type, file_size_bytes, status, submitted_at, reviewed_at, reviewer_id) "
        "VALUES (?, 'passport', 'enc', 'fp', 'image/jpeg', 512, 'verified', ?, ?, ?)",
        (user_id, utcnow(), utcnow(), admin_id)
    )
    conn.commit()


def _make_profile(conn, uid, skills_offered=None, skill_str='python'):
    skills_offered = skills_offered or [skill_str]
    matching_dal.upsert_profile(
        conn, uid, skills_offered, [], {}, 'bio', True
    )


def _insert_queue_entry(conn, user_id, skill='python',
                        status='waiting', priority=0,
                        created_ago_seconds=0,
                        retry_count=0, last_attempt_at=None,
                        cancelled_at=None):
    """Insert a queue entry with controlled timestamps for governance tests."""
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(seconds=created_ago_seconds)).isoformat()
    cur = conn.execute(
        "INSERT INTO matching_queue "
        "(user_id, skill, priority, status, retry_count, "
        " last_attempt_at, cancelled_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, skill.lower().strip(), priority, status, retry_count,
         last_attempt_at, cancelled_at, created_at, created_at)
    )
    conn.commit()
    return cur.lastrowid


# ---- Profile tests ------------------------------------------------------

class TestExtendedProfile:
    def test_save_profile_with_tags(self, conn, user_id):
        _credit_user(conn, user_id)
        matching_service.save_profile(
            conn, user_id,
            skills_offered=['Python'], skills_needed=[],
            availability={}, bio='Test',
            is_active=True,
            tags=['programming', 'backend'],
            preferred_time_slots=['weekday-evening'],
            category='Technology',
        )
        prof = matching_dal.get_profile(conn, user_id)
        import json
        assert json.loads(prof['tags']) == ['programming', 'backend']
        assert json.loads(prof['preferred_time_slots']) == ['weekday-evening']
        assert prof['category'] == 'Technology'

    def test_tags_truncated_at_10(self, conn, user_id):
        _credit_user(conn, user_id)
        many_tags = [f'tag{i}' for i in range(15)]
        matching_service.save_profile(
            conn, user_id, [], [], {}, '', True,
            tags=many_tags
        )
        import json
        prof = matching_dal.get_profile(conn, user_id)
        assert len(json.loads(prof['tags'])) == 10

    def test_search_by_tag(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        matching_service.save_profile(
            conn, user_id, ['Python'], [], {}, '', True,
            tags=['backend']
        )
        matching_service.save_profile(
            conn, user2_id, ['Design'], [], {}, '', True,
            tags=['frontend']
        )
        results = matching_service.search_peers(conn, user2_id, tag='backend')
        assert any(p['user_id'] == user_id for p in results)
        assert not any(p['user_id'] == user2_id for p in results)

    def test_search_by_time_slot(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        matching_service.save_profile(
            conn, user_id, ['Python'], [], {}, '', True,
            preferred_time_slots=['weekday-evening']
        )
        matching_service.save_profile(
            conn, user2_id, ['Design'], [], {}, '', True,
            preferred_time_slots=['weekend-morning']
        )
        results = matching_service.search_peers(conn, user2_id, time_slot='weekday-evening')
        assert any(p['user_id'] == user_id for p in results)

    def test_search_by_skill_and_tag_combined(self, conn, user_id, user2_id, admin_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        _credit_user(conn, admin_id, 1000.0)
        matching_service.save_profile(
            conn, user_id, ['Python'], [], {}, '', True, tags=['backend']
        )
        matching_service.save_profile(
            conn, user2_id, ['Python'], [], {}, '', True, tags=['frontend']
        )
        # Search for python + backend tag
        results = matching_service.search_peers(conn, admin_id, skill='python', tag='backend')
        assert any(p['user_id'] == user_id for p in results)
        assert not any(p['user_id'] == user2_id for p in results)


# ---- Queue governance tests ---------------------------------------------

class TestQueueGovernance:
    @pytest.fixture(autouse=True)
    def _auto_verify(self, conn, user_id, user2_id, admin_id):
        """Verification is now mandatory — pre-verify both users for all queue tests."""
        _verify_user(conn, user_id, admin_id)
        _verify_user(conn, user2_id, admin_id)

    def test_join_queue_success(self, conn, user_id):
        _credit_user(conn, user_id)
        eid = matching_service.join_queue(conn, user_id, 'python')
        assert eid > 0

    def test_max_attempts_per_hour_enforced(self, conn, user_id):
        _credit_user(conn, user_id)
        limit = matching_service.MAX_ATTEMPTS_PER_HOUR
        # Insert entries directly (bypassing governance to set up state)
        for i in range(limit):
            _insert_queue_entry(conn, user_id, skill=f'skill{i}', status='expired',
                                created_ago_seconds=30)
        with pytest.raises(ValueError, match='per hour'):
            matching_service.join_queue(conn, user_id, 'python')

    def test_cancel_interval_enforced(self, conn, user_id):
        _credit_user(conn, user_id)
        # Insert a recently cancelled entry
        recent_cancel = datetime.now(timezone.utc).isoformat()
        _insert_queue_entry(conn, user_id, skill='python',
                            status='cancelled', cancelled_at=recent_cancel)
        with pytest.raises(ValueError, match='2 minutes'):
            matching_service.join_queue(conn, user_id, 'python')

    def test_cancel_interval_not_enforced_after_cooldown(self, conn, user_id):
        _credit_user(conn, user_id)
        # Insert a cancelled entry older than MIN_CANCEL_INTERVAL_MINUTES
        old_cancel = (
            datetime.now(timezone.utc) -
            timedelta(minutes=matching_service.MIN_CANCEL_INTERVAL_MINUTES + 1)
        ).isoformat()
        _insert_queue_entry(conn, user_id, skill='python',
                            status='cancelled', cancelled_at=old_cancel)
        eid = matching_service.join_queue(conn, user_id, 'python')
        assert eid > 0

    def test_cancel_queue_entry_records_cancelled_at(self, conn, user_id):
        _credit_user(conn, user_id)
        eid = matching_service.join_queue(conn, user_id, 'python')
        matching_service.cancel_queue_entry(conn, eid, user_id)
        entry = matching_dal.get_queue_entry(conn, eid)
        assert entry['status'] == 'cancelled'
        assert entry['cancelled_at'] is not None

    def test_cancel_nonexistent_raises(self, conn, user_id):
        with pytest.raises(LookupError):
            matching_service.cancel_queue_entry(conn, 99999, user_id)

    def test_cancel_already_cancelled_raises(self, conn, user_id):
        _credit_user(conn, user_id)
        eid = _insert_queue_entry(conn, user_id, status='cancelled')
        with pytest.raises(ValueError):
            matching_service.cancel_queue_entry(conn, eid, user_id)

    def test_cancel_wrong_user_raises(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        eid = matching_service.join_queue(conn, user_id, 'python')
        with pytest.raises(PermissionError):
            matching_service.cancel_queue_entry(conn, eid, user2_id, actor_role='user')


# ---- Auto-match cycle (scheduler) tests ---------------------------------

class TestAutoMatchCycle:
    @pytest.fixture(autouse=True)
    def _auto_verify(self, conn, user_id, user2_id, admin_id):
        """Auto-match cycle now requires verified users — pre-verify both."""
        _verify_user(conn, user_id, admin_id)
        _verify_user(conn, user2_id, admin_id)

    def test_expire_timed_out_entries(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        # Entry older than QUEUE_TIMEOUT_MINUTES should be expired
        timeout_secs = matching_service.QUEUE_TIMEOUT_MINUTES * 60 + 10
        eid = _insert_queue_entry(conn, user_id, skill='python',
                                  created_ago_seconds=timeout_secs)
        matching_service.run_auto_match_cycle(conn)
        entry = matching_dal.get_queue_entry(conn, eid)
        assert entry['status'] == 'expired'

    def test_fresh_entry_not_expired(self, conn, user_id):
        _credit_user(conn, user_id)
        eid = _insert_queue_entry(conn, user_id, skill='python', created_ago_seconds=5)
        matching_service.run_auto_match_cycle(conn)
        entry = matching_dal.get_queue_entry(conn, eid)
        assert entry['status'] != 'expired'

    def test_max_retries_expires_entry(self, conn, user_id):
        _credit_user(conn, user_id)
        # Entry with retry_count already at MAX_RETRIES should be expired
        eid = _insert_queue_entry(
            conn, user_id, skill='python',
            retry_count=matching_service.MAX_RETRIES,
            last_attempt_at=(
                datetime.now(timezone.utc) -
                timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
            ).isoformat()
        )
        matching_service.run_auto_match_cycle(conn)
        entry = matching_dal.get_queue_entry(conn, eid)
        assert entry['status'] == 'expired'

    def test_retry_cooldown_respected(self, conn, user_id):
        _credit_user(conn, user_id)
        # Entry attempted very recently should not be retried
        recent_attempt = datetime.now(timezone.utc).isoformat()
        eid = _insert_queue_entry(
            conn, user_id, skill='python',
            retry_count=0, last_attempt_at=recent_attempt
        )
        matching_service.run_auto_match_cycle(conn)
        entry = matching_dal.get_queue_entry(conn, eid)
        # retry_count should NOT have changed since cooldown not elapsed
        assert (entry.get('retry_count') or 0) == 0

    def test_retry_count_increments_on_attempt(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        # No match available — cycle should increment retry_count
        old_attempt = (
            datetime.now(timezone.utc) -
            timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
        ).isoformat()
        eid = _insert_queue_entry(
            conn, user_id, skill='python',
            retry_count=0, last_attempt_at=old_attempt
        )
        matching_service.run_auto_match_cycle(conn)
        entry = matching_dal.get_queue_entry(conn, eid)
        assert (entry.get('retry_count') or 0) == 1

    def test_scheduler_creates_match_when_peer_available(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        _make_profile(conn, user_id, skills_offered=['python'])
        # user2 is waiting for a python peer
        old_attempt = (
            datetime.now(timezone.utc) -
            timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
        ).isoformat()
        eid = _insert_queue_entry(
            conn, user2_id, skill='python',
            retry_count=0, last_attempt_at=old_attempt
        )
        # user_id is also waiting (they are the potential match for user2)
        eid2 = _insert_queue_entry(
            conn, user_id, skill='python',
            retry_count=0, last_attempt_at=old_attempt
        )
        summary = matching_service.run_auto_match_cycle(conn)
        assert summary['matched'] >= 1
        e1 = matching_dal.get_queue_entry(conn, eid)
        e2 = matching_dal.get_queue_entry(conn, eid2)
        # At least one of them should be matched
        statuses = {e1['status'], e2['status']}
        assert 'matched' in statuses

    def test_do_not_match_list_respected(self, conn, user_id, user2_id):
        _credit_user(conn, user_id)
        _credit_user(conn, user2_id)
        # Block user2 → user_id so they won't be matched
        matching_dal.add_block(conn, user_id, user2_id)
        conn.commit()

        old_attempt = (
            datetime.now(timezone.utc) -
            timedelta(seconds=matching_service.RETRY_COOLDOWN_SECS + 5)
        ).isoformat()
        _insert_queue_entry(conn, user_id, skill='python',
                            retry_count=0, last_attempt_at=old_attempt)
        _insert_queue_entry(conn, user2_id, skill='python',
                            retry_count=0, last_attempt_at=old_attempt)

        summary = matching_service.run_auto_match_cycle(conn)
        # Should NOT match because they are blocked
        assert summary['matched'] == 0

    def test_cycle_returns_summary(self, conn, user_id):
        _credit_user(conn, user_id)
        summary = matching_service.run_auto_match_cycle(conn)
        assert 'expired' in summary
        assert 'attempted' in summary
        assert 'matched' in summary
