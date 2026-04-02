"""Unit tests for rating_service: submission, duplicate prevention, reputation."""

import pytest
from app.services import rating_service, ledger_service
from app.dal import user_dal
from app.utils import utcnow


def _make_session(conn, initiator_id, participant_id, status='completed'):
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO sessions '
        '(initiator_id, participant_id, status, credit_amount, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (initiator_id, participant_id, status, 50.0, now, now)
    )
    conn.commit()
    return cur.lastrowid


class TestSubmitRating:
    def test_submit_success(self, conn, user_id, user2_id):
        sid = _make_session(conn, user_id, user2_id)
        rating_id = rating_service.submit_rating(conn, user_id, sid, 5, 'Great!')
        assert rating_id > 0

    def test_participant_can_rate_too(self, conn, user_id, user2_id):
        sid = _make_session(conn, user_id, user2_id)
        rating_id = rating_service.submit_rating(conn, user2_id, sid, 4, 'Good')
        assert rating_id > 0

    def test_duplicate_rating_rejected(self, conn, user_id, user2_id):
        sid = _make_session(conn, user_id, user2_id)
        rating_service.submit_rating(conn, user_id, sid, 5, 'First')
        with pytest.raises(ValueError, match='already rated'):
            rating_service.submit_rating(conn, user_id, sid, 4, 'Second')

    def test_non_participant_rejected(self, conn, user_id, user2_id, admin_id):
        sid = _make_session(conn, user_id, user2_id)
        with pytest.raises(PermissionError, match='not a participant'):
            rating_service.submit_rating(conn, admin_id, sid, 3, 'Intruder')

    def test_pending_session_rejected(self, conn, user_id, user2_id):
        sid = _make_session(conn, user_id, user2_id, status='pending')
        with pytest.raises(ValueError, match='completed sessions'):
            rating_service.submit_rating(conn, user_id, sid, 5, 'Too soon')

    def test_nonexistent_session_rejected(self, conn, user_id):
        with pytest.raises(LookupError, match='Session not found'):
            rating_service.submit_rating(conn, user_id, 99999, 5, 'No session')

    def test_inactive_user_blocked(self, conn, user_id, user2_id):
        user_dal.update_fields(conn, user_id, is_active=0)
        sid = _make_session(conn, user_id, user2_id)
        with pytest.raises(PermissionError, match='banned'):
            rating_service.submit_rating(conn, user_id, sid, 5, 'Banned')


class TestReputationScore:
    def test_new_user_has_neutral_score(self, conn, user_id):
        rep = rating_service.get_reputation_score(conn, user_id)
        # Neutral avg_norm=0.6, close_rate=0, cancel_rate=0, dispute_rate=0
        # raw = 0.6*50 + 0*30 + (1-0)*10 + (1-0)*10 = 30 + 0 + 10 + 10 = 50
        assert rep['reputation_score'] == 50.0
        assert rep['total_ratings'] == 0

    def test_high_ratings_increase_score(self, conn, user_id, user2_id, admin_id):
        # Complete several sessions and rate highly
        for i in range(3):
            sid = _make_session(conn, user2_id, user_id)
            rating_service.submit_rating(conn, user2_id, sid, 5, 'Excellent')

        rep = rating_service.get_reputation_score(conn, user_id)
        assert rep['total_ratings'] == 3
        assert rep['average_rating'] == 5.0
        assert rep['reputation_score'] > 80.0

    def test_cancelled_sessions_lower_score(self, conn, user_id, user2_id):
        # Add cancelled sessions
        for _ in range(3):
            _make_session(conn, user_id, user2_id, status='cancelled')

        rep = rating_service.get_reputation_score(conn, user_id)
        assert rep['sessions_cancelled'] == 3
        assert rep['cancellation_rate'] > 0

    def test_score_bounded_0_to_100(self, conn, user_id, user2_id):
        # Add many disputes to stress test the formula
        for _ in range(5):
            _make_session(conn, user_id, user2_id, status='cancelled')

        rep = rating_service.get_reputation_score(conn, user_id)
        assert 0.0 <= rep['reputation_score'] <= 100.0

    def test_score_components_present(self, conn, user_id):
        rep = rating_service.get_reputation_score(conn, user_id)
        expected_keys = {
            'user_id', 'average_rating', 'total_ratings',
            'sessions_total', 'sessions_completed', 'sessions_cancelled',
            'close_rate', 'cancellation_rate', 'dispute_rate',
            'reputation_score',
        }
        assert expected_keys.issubset(rep.keys())


class TestViolationReporting:
    def test_report_violation_success(self, conn, user_id, user2_id):
        vid = rating_service.report_violation(
            conn, user_id, user2_id, 'spam', 'Spamming', 'low'
        )
        assert vid > 0

    def test_cannot_report_self(self, conn, user_id):
        with pytest.raises(ValueError, match='yourself'):
            rating_service.report_violation(
                conn, user_id, user_id, 'spam', 'Self-report', 'low'
            )

    def test_invalid_violation_type(self, conn, user_id, user2_id):
        with pytest.raises(ValueError, match='violation_type'):
            rating_service.report_violation(
                conn, user_id, user2_id, 'fake_type', 'Bad', 'low'
            )

    def test_invalid_severity(self, conn, user_id, user2_id):
        with pytest.raises(ValueError, match='severity'):
            rating_service.report_violation(
                conn, user_id, user2_id, 'spam', 'Bad', 'critical'
            )
