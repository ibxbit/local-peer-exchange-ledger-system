"""Rating & violation service."""

from app.services.guards import guard_is_active
from app.dal import rating_dal, session_dal, violation_dal, audit_dal


def submit_rating(conn, rater_id: int, session_id: int,
                  score: int, comment: str) -> int:
    ok, reason = guard_is_active(conn, rater_id)
    if not ok:
        raise PermissionError(reason)

    session = session_dal.get_by_id(conn, session_id)
    if not session:
        raise LookupError('Session not found.')
    if session['status'] != 'completed':
        raise ValueError('Can only rate completed sessions.')
    if rater_id not in (session['initiator_id'], session['participant_id']):
        raise PermissionError('You are not a participant in this session.')

    if rating_dal.get_by_rater_session(conn, rater_id, session_id):
        raise ValueError('You have already rated this session.')

    ratee_id = (session['participant_id']
                if rater_id == session['initiator_id']
                else session['initiator_id'])

    rating_id = rating_dal.create(conn, rater_id, ratee_id, session_id,
                                   score, comment)
    audit_dal.write(conn, 'RATING_SUBMITTED', user_id=rater_id,
                    entity_type='rating', entity_id=rating_id,
                    details={'ratee_id': ratee_id, 'score': score})
    return rating_id


def get_reputation_score(conn, user_id: int) -> dict:
    stats         = rating_dal.get_stats(conn, user_id)
    sess          = session_dal.count_session_stats_for_user(conn, user_id)
    resolved_viol = violation_dal.count_resolved_against(conn, user_id)

    total_sess    = sess['total']
    completed     = sess['completed']
    cancelled     = sess['cancelled']

    # Normalised star average; neutral 0.6 when no ratings exist yet
    avg_star = stats['avg_score'] or 0.0
    avg_norm = (avg_star / 5.0) if stats['total'] > 0 else 0.6

    close_rate  = completed / max(1, total_sess)
    cancel_rate = cancelled / max(1, total_sess)
    # Dispute rate capped at 1 to prevent runaway penalty
    dispute_rate = min(resolved_viol / max(1, completed), 1.0)

    # Weighted aggregate – maximum possible = 50+30+10+10 = 100
    raw = (
        avg_norm          * 50   # star quality
        + close_rate      * 30   # session reliability
        + (1 - cancel_rate)  * 10   # cancellation reliability
        + (1 - dispute_rate) * 10   # dispute-free history
    )
    score = round(max(0.0, min(100.0, raw)), 1)

    return {
        'user_id':            user_id,
        'average_rating':     round(avg_star, 2),
        'total_ratings':      stats['total'],
        'positive_ratings':   stats['positive'] or 0,
        'sessions_total':     total_sess,
        'sessions_completed': completed,
        'sessions_cancelled': cancelled,
        'close_rate':         round(close_rate, 3),
        'cancellation_rate':  round(cancel_rate, 3),
        'dispute_rate':       round(dispute_rate, 3),
        'resolved_violations': resolved_viol,
        'reputation_score':   score,
    }


def report_violation(conn, reporter_id: int, target_id: int,
                     violation_type: str, description: str,
                     severity: str) -> int:
    ok, reason = guard_is_active(conn, reporter_id)
    if not ok:
        raise PermissionError(reason)
    if reporter_id == target_id:
        raise ValueError('Cannot report yourself.')

    ALLOWED_TYPES = ('spam', 'harassment', 'fraud', 'no_show', 'abuse', 'other')
    if violation_type not in ALLOWED_TYPES:
        raise ValueError(f'violation_type must be one of: {", ".join(ALLOWED_TYPES)}.')
    if severity not in ('low', 'medium', 'high'):
        raise ValueError('severity must be low, medium, or high.')

    vid = violation_dal.create(conn, target_id, reporter_id,
                                violation_type, description, severity)
    audit_dal.write(conn, 'VIOLATION_REPORTED', user_id=reporter_id,
                    entity_type='violation', entity_id=vid,
                    details={'target_id': target_id, 'type': violation_type})
    return vid


def resolve_violation(conn, admin_id: int, vid: int,
                      decision: str, notes: str) -> None:
    if decision not in ('resolved', 'dismissed'):
        raise ValueError('decision must be "resolved" or "dismissed".')
    row = violation_dal.get_by_id(conn, vid)
    if not row:
        raise LookupError('Violation not found.')
    if row['status'] != 'open':
        raise ValueError('Violation is already closed.')
    violation_dal.resolve(conn, vid, decision, admin_id, notes)
    audit_dal.write(conn, f'VIOLATION_{decision.upper()}',
                    user_id=admin_id, entity_type='violation', entity_id=vid)


def file_appeal(conn, appellant_id: int, violation_id: int,
                reason: str) -> int:
    ok, r = guard_is_active(conn, appellant_id)
    if not ok:
        raise PermissionError(r)
    v = violation_dal.get_by_id(conn, violation_id)
    if not v:
        raise LookupError('Violation not found.')
    if v['user_id'] != appellant_id:
        raise PermissionError('You can only appeal violations filed against you.')
    if violation_dal.get_appeal(conn, violation_id, appellant_id):
        raise ValueError('You have already filed an appeal for this violation.')

    aid = violation_dal.create_appeal(conn, violation_id, appellant_id, reason)
    audit_dal.write(conn, 'APPEAL_FILED', user_id=appellant_id,
                    entity_type='violation_appeal', entity_id=aid,
                    details={'violation_id': violation_id})
    return aid


def resolve_appeal(conn, admin_id: int, appeal_id: int,
                   decision: str, notes: str) -> None:
    if decision not in ('upheld', 'denied'):
        raise ValueError('decision must be "upheld" or "denied".')
    violation_dal.resolve_appeal(conn, appeal_id, decision, admin_id, notes)
    audit_dal.write(conn, f'APPEAL_{decision.upper()}',
                    user_id=admin_id, entity_type='violation_appeal',
                    entity_id=appeal_id)
