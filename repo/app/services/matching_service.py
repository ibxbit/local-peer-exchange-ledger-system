"""
Matching service — peer search, session lifecycle, queue, blacklist.

Governance rules (enforced server-side):
  - Auto-match every 10 s via scheduler (run_auto_match_cycle)
  - Queue entries time out after QUEUE_TIMEOUT_MINUTES (default 3 min)
  - Each entry retried up to MAX_RETRIES times before being expired
  - RETRY_COOLDOWN_SECS enforced between consecutive match attempts
  - MAX_ATTEMPTS_PER_HOUR limits queue joins per user per hour
  - MIN_CANCEL_INTERVAL_MINUTES: must wait before rejoining after a cancel
  - Do-not-match list (blacklist) respected in all auto-match lookups
"""

import json
from datetime import datetime, timezone, timedelta

from app.services.guards import guard_can_act, guard_is_active, guard_is_verified
from app.dal import matching_dal, session_dal, audit_dal, user_dal
from app.utils import utcnow

# ---- Governance constants ------------------------------------------------
MAX_RETRIES               = 3    # scheduler retries per queue entry before expiry
RETRY_COOLDOWN_SECS       = 30   # seconds between scheduler retry attempts
QUEUE_TIMEOUT_MINUTES     = 3    # minutes before a waiting entry is force-expired
MAX_ATTEMPTS_PER_HOUR     = 10   # max new queue entries a user can create per hour
MIN_CANCEL_INTERVAL_MINUTES = 2  # minutes user must wait after cancelling before rejoining

VALID_SESSION_TRANSITIONS = {
    'pending': ['active', 'cancelled'],
    'active':  ['completed', 'cancelled'],
}


# ---- Profile ------------------------------------------------------------

def save_profile(conn, user_id: int, skills_offered: list,
                 skills_needed: list, availability: dict,
                 bio: str, is_active: bool,
                 tags: list = None,
                 preferred_time_slots: list = None,
                 category: str = None) -> None:
    ok, reason = guard_is_active(conn, user_id)
    if not ok:
        raise PermissionError(reason)

    skills_offered = [str(s).strip()[:64] for s in skills_offered if str(s).strip()][:20]
    skills_needed  = [str(s).strip()[:64] for s in skills_needed  if str(s).strip()][:20]
    tags           = [str(t).strip()[:32] for t in (tags or [])   if str(t).strip()][:10]
    preferred_time_slots = [str(s).strip()[:32]
                            for s in (preferred_time_slots or [])
                            if str(s).strip()][:10]

    existing = matching_dal.get_profile(conn, user_id)
    matching_dal.upsert_profile(
        conn, user_id,
        skills_offered, skills_needed,
        availability, bio[:500], is_active,
        tags=tags,
        preferred_time_slots=preferred_time_slots,
        category=(category or '')[:64],
    )
    action = 'MATCHING_PROFILE_UPDATED' if existing else 'MATCHING_PROFILE_CREATED'
    audit_dal.write(conn, action, user_id=user_id,
                    entity_type='matching_profile', entity_id=user_id)


def search_peers(conn, requester_id: int, skill: str = '',
                 tag: str = '', time_slot: str = '') -> list:
    blocked_ids = matching_dal.get_blocked_ids(conn, requester_id)
    profiles = matching_dal.search_profiles(conn, requester_id, blocked_ids)

    if skill:
        skill_lower = skill.lower()
        profiles = [
            p for p in profiles
            if any(skill_lower in s.lower()
                   for s in json.loads(p.get('skills_offered', '[]')))
        ]
    if tag:
        tag_lower = tag.lower()
        profiles = [
            p for p in profiles
            if any(tag_lower in t.lower()
                   for t in json.loads(p.get('tags', '[]')))
        ]
    if time_slot:
        slot_lower = time_slot.lower()
        profiles = [
            p for p in profiles
            if any(slot_lower in s.lower()
                   for s in json.loads(p.get('preferred_time_slots', '[]')))
        ]

    result = []
    for p in profiles:
        result.append({
            'user_id':              p['user_id'],
            'username':             p['username'],
            'skills_offered':       json.loads(p['skills_offered']),
            'skills_needed':        json.loads(p['skills_needed']),
            'tags':                 json.loads(p.get('tags') or '[]'),
            'preferred_time_slots': json.loads(p.get('preferred_time_slots') or '[]'),
            'category':             p.get('category') or '',
            'bio':                  p.get('bio') or '',
        })
    return result


# ---- Sessions -----------------------------------------------------------

def request_session(conn, initiator_id: int, participant_id: int,
                    description: str, duration_minutes: int,
                    credit_amount: float, scheduled_at: str,
                    idempotency_key: str = None,
                    building: str = None, room: str = None,
                    time_slot: str = None) -> int:
    ok, reason = guard_is_verified(conn, initiator_id)
    if not ok:
        raise PermissionError(reason)
    ok, reason = guard_can_act(conn, initiator_id)
    if not ok:
        raise PermissionError(reason)

    if participant_id == initiator_id:
        raise ValueError('Cannot create a session with yourself.')

    participant = user_dal.get_by_id(conn, participant_id)
    if not participant or not participant['is_active']:
        raise LookupError('Participant not found or inactive.')

    if matching_dal.is_blocked(conn, initiator_id, participant_id):
        raise ValueError('Cannot create a session with a blocked user.')

    session_id = session_dal.create(
        conn, initiator_id, participant_id,
        description, duration_minutes, credit_amount,
        scheduled_at, idempotency_key,
        building=building, room=room, time_slot=time_slot
    )
    audit_dal.write(conn, 'SESSION_REQUESTED', user_id=initiator_id,
                    entity_type='session', entity_id=session_id)
    return session_id


def update_session_status(conn, session_id: int, actor_id: int,
                          new_status: str, actor_role: str = 'user',
                          cancel_reason: str = None) -> None:
    session = session_dal.get_by_id(conn, session_id)
    if not session:
        raise LookupError('Session not found.')

    if actor_id not in (session['initiator_id'], session['participant_id']) \
            and actor_role != 'admin':
        raise PermissionError('Access denied.')

    allowed = VALID_SESSION_TRANSITIONS.get(session['status'], [])
    if new_status not in allowed:
        raise ValueError(
            f'Cannot transition from "{session["status"]}" to "{new_status}".'
        )

    session_dal.update_status(conn, session_id, new_status, cancel_reason)
    audit_dal.write(conn, f'SESSION_{new_status.upper()}',
                    user_id=actor_id, entity_type='session', entity_id=session_id,
                    details={'old_status': session['status']})


# ---- Queue --------------------------------------------------------------

def join_queue(conn, user_id: int, skill: str,
               priority: int = 0, expires_at: str = None) -> int:
    ok, reason = guard_is_verified(conn, user_id)
    if not ok:
        raise PermissionError(reason)
    ok, reason = guard_can_act(conn, user_id)
    if not ok:
        raise PermissionError(reason)
    if not skill or not skill.strip():
        raise ValueError('skill is required.')

    now_dt = datetime.now(timezone.utc)

    # Rule: minimum interval between cancellations
    last_cancel = matching_dal.get_last_cancelled_entry(conn, user_id)
    if last_cancel and last_cancel.get('cancelled_at'):
        try:
            cancelled_dt = datetime.fromisoformat(
                last_cancel['cancelled_at'].replace('Z', '+00:00')
            )
            if cancelled_dt.tzinfo is None:
                cancelled_dt = cancelled_dt.replace(tzinfo=timezone.utc)
            min_delta = timedelta(minutes=MIN_CANCEL_INTERVAL_MINUTES)
            elapsed = now_dt - cancelled_dt
            if elapsed < min_delta:
                remaining = int((min_delta - elapsed).total_seconds())
                raise ValueError(
                    f'Must wait {MIN_CANCEL_INTERVAL_MINUTES} minutes between '
                    f'queue cancellations ({remaining}s remaining).'
                )
        except ValueError:
            raise
        except Exception:
            pass  # malformed timestamp — ignore

    # Rule: max attempts per hour
    attempt_count = matching_dal.count_user_queue_entries_since(conn, user_id, hours=1)
    if attempt_count >= MAX_ATTEMPTS_PER_HOUR:
        raise ValueError(
            f'Maximum of {MAX_ATTEMPTS_PER_HOUR} queue joins per hour reached. '
            f'Please wait before joining again.'
        )

    entry_id = matching_dal.enqueue(conn, user_id, skill, priority, expires_at)
    audit_dal.write(conn, 'QUEUE_JOINED', user_id=user_id,
                    entity_type='matching_queue', entity_id=entry_id,
                    details={'skill': skill})
    return entry_id


def cancel_queue_entry(conn, entry_id: int, user_id: int,
                       actor_role: str = 'user') -> None:
    """Cancel a waiting queue entry, recording the cancellation timestamp."""
    entry = matching_dal.get_queue_entry(conn, entry_id)
    if not entry:
        raise LookupError('Queue entry not found.')
    if entry['user_id'] != user_id and actor_role != 'admin':
        raise PermissionError('Access denied.')
    if entry['status'] != 'waiting':
        raise ValueError(f'Entry is already {entry["status"]}.')
    now = utcnow()
    matching_dal.update_queue_entry(
        conn, entry_id, status='cancelled', cancelled_at=now
    )
    audit_dal.write(conn, 'QUEUE_CANCELLED', user_id=user_id,
                    entity_type='matching_queue', entity_id=entry_id)


def auto_match(conn, user_id: int, skill: str) -> dict | None:
    """
    Manual auto-match trigger (POST /queue/match).
    Returns the matched queue entry dict, or None if no match found.
    """
    ok, reason = guard_is_verified(conn, user_id)
    if not ok:
        raise PermissionError(reason)
    ok, reason = guard_can_act(conn, user_id)
    if not ok:
        raise PermissionError(reason)

    blocked_ids = matching_dal.get_blocked_ids(conn, user_id)
    match = matching_dal.find_waiting_match(conn, skill, user_id, blocked_ids)
    if not match:
        return None

    session_id = session_dal.create(
        conn, user_id, match['user_id'],
        f'Auto-matched on skill: {skill}', None, 0.0, None
    )
    matching_dal.update_queue_entry(
        conn, match['id'],
        status='matched', matched_to=user_id, session_id=session_id
    )
    audit_dal.write(conn, 'AUTO_MATCHED', user_id=user_id,
                    entity_type='session', entity_id=session_id,
                    details={'matched_with': match['user_id'], 'skill': skill})
    return {**match, 'session_id': session_id}


def run_auto_match_cycle(conn) -> dict:
    """
    Called by the scheduler every 10 seconds.

    For each waiting queue entry:
      1. Expire entries that exceed QUEUE_TIMEOUT_MINUTES.
      2. Skip entries still within RETRY_COOLDOWN_SECS of their last attempt.
      3. Expire entries that have exhausted MAX_RETRIES.
      4. Attempt to match against the do-not-match-excluded waiting pool.
      5. On success, create a session and update both entries to 'matched'.

    Returns a summary dict for logging.
    """
    now_dt        = datetime.now(timezone.utc)
    timeout_cutoff  = (now_dt - timedelta(minutes=QUEUE_TIMEOUT_MINUTES)).isoformat()
    cooldown_cutoff = (now_dt - timedelta(seconds=RETRY_COOLDOWN_SECS)).isoformat()

    expired  = matching_dal.expire_timed_out_entries(conn, timeout_cutoff)
    entries  = matching_dal.get_waiting_entries(conn)

    attempted = 0
    matched   = 0

    for entry in entries:
        user_id     = entry['user_id']
        skill       = entry['skill']
        entry_id    = entry['id']
        retry_count = entry.get('retry_count') or 0
        last_attempt = entry.get('last_attempt_at')

        # Respect cooldown between retries
        if last_attempt and last_attempt >= cooldown_cutoff:
            continue

        # Expire entries whose owner is not (or is no longer) verified
        ok_ver, _ = guard_is_verified(conn, user_id)
        if not ok_ver:
            matching_dal.update_queue_entry(conn, entry_id, status='expired')
            expired += 1
            continue

        # Expire entries that have exceeded max retries
        if retry_count >= MAX_RETRIES:
            matching_dal.update_queue_entry(conn, entry_id, status='expired')
            expired += 1
            continue

        # Record this attempt
        matching_dal.update_queue_entry(
            conn, entry_id,
            retry_count=retry_count + 1,
            last_attempt_at=utcnow()
        )
        attempted += 1

        # Enforce do-not-match list (blacklist)
        blocked_ids = matching_dal.get_blocked_ids(conn, user_id)
        match = matching_dal.find_waiting_match(conn, skill, user_id, blocked_ids)
        if not match:
            continue

        session_id = session_dal.create(
            conn, user_id, match['user_id'],
            f'Auto-matched on skill: {skill}', None, 0.0, None
        )
        matching_dal.update_queue_entry(
            conn, match['id'],
            status='matched', matched_to=user_id, session_id=session_id
        )
        matching_dal.update_queue_entry(
            conn, entry_id,
            status='matched', matched_to=match['user_id'], session_id=session_id
        )
        audit_dal.write(conn, 'AUTO_MATCHED', user_id=user_id,
                        entity_type='session', entity_id=session_id,
                        details={'matched_with': match['user_id'], 'skill': skill,
                                 'source': 'scheduler'})
        matched += 1

    return {'expired': expired, 'attempted': attempted, 'matched': matched}


# ---- Blacklist ----------------------------------------------------------

def block_user_temporary(conn, blocker_id: int, blocked_id: int,
                          reason: str = None,
                          duration_hours: float = None,
                          expires_at: str = None) -> None:
    """
    Add a temporary do-not-match entry that expires automatically.
    Provide either duration_hours (e.g. 24.0) or an explicit ISO-8601 expires_at.
    After expiry the block is ignored by match lookup but the row remains
    for audit purposes until the user manually removes it.
    """
    ok, r = guard_is_active(conn, blocker_id)
    if not ok:
        raise PermissionError(r)
    if blocker_id == blocked_id:
        raise ValueError('Cannot block yourself.')
    if not duration_hours and not expires_at:
        raise ValueError('Either duration_hours or expires_at is required.')

    if duration_hours and not expires_at:
        exp_dt = datetime.now(timezone.utc) + timedelta(hours=float(duration_hours))
        expires_at = exp_dt.isoformat()

    matching_dal.add_block(conn, blocker_id, blocked_id, reason,
                           is_temporary=True, expires_at=expires_at)
    audit_dal.write(conn, 'USER_BLOCKED_TEMPORARILY', user_id=blocker_id,
                    entity_type='user', entity_id=blocked_id,
                    details={'expires_at': expires_at, 'reason': reason})


def block_user(conn, blocker_id: int, blocked_id: int,
               reason: str = None) -> None:
    ok, r = guard_is_active(conn, blocker_id)
    if not ok:
        raise PermissionError(r)
    if blocker_id == blocked_id:
        raise ValueError('Cannot block yourself.')
    matching_dal.add_block(conn, blocker_id, blocked_id, reason)
    audit_dal.write(conn, 'USER_BLOCKED', user_id=blocker_id,
                    entity_type='user', entity_id=blocked_id)


def unblock_user(conn, blocker_id: int, blocked_id: int) -> None:
    matching_dal.remove_block(conn, blocker_id, blocked_id)
    audit_dal.write(conn, 'USER_UNBLOCKED', user_id=blocker_id,
                    entity_type='user', entity_id=blocked_id)
