"""
Action guards — enforce platform-wide eligibility rules.

Rules (applied before any session/matching/transfer action):
  1. Banned   — is_active = 0                          → blocked always
  2. Muted    — muted_until > now                      → blocked for actions
  3. Credit   — credit_balance < 60                    → blocked for actions
  4. Disputes — open violations against user > 3       → blocked for actions

Usage:
    ok, reason = guard_can_act(conn, user_id)
    if not ok:
        return jsonify({'error': reason}), 403
"""

from app.utils import utcnow_dt, parse_dt
from app.dal import user_dal, violation_dal

# Minimum credit balance required to initiate actions
MIN_CREDIT_THRESHOLD = 60.0

# Maximum open disputes before actions are blocked
MAX_OPEN_DISPUTES = 3


def guard_can_act(conn, user_id: int) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Call this before any session creation, queue entry, or transfer.
    """
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        return False, 'User not found.'

    # Rule 1 — Banned
    if not user['is_active']:
        return False, 'Your account has been banned.'

    # Rule 2 — Muted
    muted_until = user.get('muted_until')
    if muted_until:
        try:
            if utcnow_dt() < parse_dt(muted_until):
                return False, (
                    f'Your account is muted until {muted_until}. '
                    'You cannot perform this action.'
                )
        except (ValueError, TypeError):
            pass  # malformed date — treat as expired

    # Rule 3 — Insufficient credits
    if user['credit_balance'] < MIN_CREDIT_THRESHOLD:
        return False, (
            f'Insufficient credits. You need at least {MIN_CREDIT_THRESHOLD} '
            f'credits to perform this action (current: {user["credit_balance"]:.2f}).'
        )

    # Rule 4 — Too many open disputes
    open_disputes = violation_dal.count_open_against(conn, user_id)
    if open_disputes > MAX_OPEN_DISPUTES:
        return False, (
            f'Your account has {open_disputes} open disputes. '
            f'Actions are blocked when disputes exceed {MAX_OPEN_DISPUTES}.'
        )

    return True, ''


def guard_is_active(conn, user_id: int) -> tuple[bool, str]:
    """Lightweight check — only verifies the account is not banned."""
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        return False, 'User not found.'
    if not user['is_active']:
        return False, 'Your account has been banned.'
    return True, ''
