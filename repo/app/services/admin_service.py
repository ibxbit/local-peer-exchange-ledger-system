"""
Admin service — analytics, user management, moderation, permissions.

Permission model
────────────────
role='auditor'  → read-only on every resource (no admin_permissions rows needed).
role='admin', no rows in admin_permissions → super-admin (full access to everything).
role='admin', has rows in admin_permissions → restricted to explicit grants.

require_permission(conn, admin_id, resource, write=False):
  Returns scope dict (or None for full access).
  Raises PermissionError if access is denied.
"""

import json
from app.dal import user_dal, violation_dal, audit_dal, admin_dal
from app.utils import utcnow

ALLOWED_SEVERITIES = ('low', 'medium', 'high')


# ---------------------------------------------------------------------------
# Permission guard
# ---------------------------------------------------------------------------

def require_permission(conn, admin_id: int, resource: str,
                       write: bool = False) -> dict | None:
    """
    Resolve fine-grained permission for admin_id on resource.

    Returns:
        None           → super-admin, no scope restriction.
        dict           → restricted admin; caller should filter by returned scope.

    Raises:
        PermissionError if access is denied.
    """
    # Auditors handled separately (read-only, enforced in route layer)
    if not admin_dal.has_any_permission(conn, admin_id):
        # No explicit grants → super-admin; full unrestricted access.
        return None

    perm = admin_dal.get_permission(conn, admin_id, resource)
    if not perm:
        raise PermissionError(
            f'Access to resource "{resource}" is not granted to your account.'
        )
    if not perm['can_read']:
        raise PermissionError(f'Read access to resource "{resource}" is denied.')
    if write and not perm['can_write']:
        raise PermissionError(
            f'Write access to resource "{resource}" is not granted to your account.'
        )

    scope = None
    if perm.get('scope'):
        try:
            scope = json.loads(perm['scope'])
        except (json.JSONDecodeError, TypeError):
            scope = None
    return scope


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def get_analytics(conn) -> dict:
    total_users  = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    active_users = conn.execute('SELECT COUNT(*) FROM users WHERE is_active=1').fetchone()[0]
    by_role = {r['role']: r['count'] for r in [
        dict(r) for r in conn.execute(
            'SELECT role, COUNT(*) as count FROM users GROUP BY role'
        ).fetchall()
    ]}
    total_sess = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
    by_status  = {r['status']: r['count'] for r in [
        dict(r) for r in conn.execute(
            'SELECT status, COUNT(*) as count FROM sessions GROUP BY status'
        ).fetchall()
    ]}
    ledger_row = conn.execute(
        'SELECT COUNT(*) as entries, SUM(amount) as volume FROM ledger_entries'
    ).fetchone()
    pending_ver = conn.execute(
        "SELECT COUNT(*) FROM identity_verifications WHERE status='pending'"
    ).fetchone()[0]
    open_viol = conn.execute(
        "SELECT COUNT(*) FROM violations WHERE status='open'"
    ).fetchone()[0]
    pending_appeals = conn.execute(
        "SELECT COUNT(*) FROM violation_appeals WHERE status='pending'"
    ).fetchone()[0]
    avg_rep = conn.execute(
        'SELECT AVG(avg_score) FROM ('
        '  SELECT AVG(score) as avg_score FROM ratings GROUP BY ratee_id)'
    ).fetchone()[0]
    new_7d = conn.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()[0]

    return {
        'users': {
            'total': total_users, 'active': active_users,
            'by_role': by_role, 'registered_last_7d': new_7d,
        },
        'sessions': {'total': total_sess, 'by_status': by_status},
        'ledger': {
            'total_entries': ledger_row[0] or 0,
            'total_volume':  round(ledger_row[1] or 0, 2),
        },
        'moderation': {
            'pending_verifications': pending_ver,
            'open_violations':       open_viol,
            'pending_appeals':       pending_appeals,
        },
        'reputation': {
            'platform_avg_rating': round(avg_rep, 2) if avg_rep else None,
        },
    }


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def ban_user(conn, admin_id: int, user_id: int, reason: str) -> None:
    if not reason or not reason.strip():
        raise ValueError('Reason is required for banning.')
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')
    if not user['is_active']:
        raise ValueError('User is already banned.')

    user_dal.update_fields(conn, user_id, is_active=0)
    violation_dal.create(
        conn, user_id, admin_id, 'admin_ban',
        f'Account banned by admin: {reason}', 'high'
    )
    audit_dal.write(conn, 'USER_BANNED', user_id=admin_id,
                    entity_type='user', entity_id=user_id,
                    details={'reason': reason})


def unban_user(conn, admin_id: int, user_id: int, reason: str) -> None:
    """Reinstate a banned user. Closes any open admin_ban violation."""
    if not reason or not reason.strip():
        raise ValueError('Reason is required for unbanning.')
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')
    if user['is_active']:
        raise ValueError('User is not banned.')

    user_dal.update_fields(conn, user_id, is_active=1)

    # Close the most recent open admin_ban violation (if present)
    ban_viol = conn.execute(
        "SELECT id FROM violations WHERE user_id = ? AND violation_type = 'admin_ban' "
        "AND status = 'open' ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    if ban_viol:
        violation_dal.resolve(
            conn, ban_viol['id'], 'dismissed', admin_id,
            f'Account reinstated: {reason}'
        )

    audit_dal.write(conn, 'USER_UNBANNED', user_id=admin_id,
                    entity_type='user', entity_id=user_id,
                    details={'reason': reason})


def mute_user(conn, admin_id: int, user_id: int,
              muted_until: str, reason: str = None) -> None:
    user = user_dal.get_by_id(conn, user_id)
    if not user:
        raise LookupError('User not found.')
    user_dal.update_fields(conn, user_id, muted_until=muted_until)
    audit_dal.write(conn, 'USER_MUTED', user_id=admin_id,
                    entity_type='user', entity_id=user_id,
                    details={'muted_until': muted_until, 'reason': reason})


def unmute_user(conn, admin_id: int, user_id: int) -> None:
    user_dal.update_fields(conn, user_id, muted_until=None)
    audit_dal.write(conn, 'USER_UNMUTED', user_id=admin_id,
                    entity_type='user', entity_id=user_id)


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------

def escalate_violation(conn, admin_id: int, vid: int,
                       new_severity: str, reason: str) -> None:
    """Change the severity of an open violation and record it in the audit log."""
    if new_severity not in ALLOWED_SEVERITIES:
        raise ValueError(f'severity must be one of: {", ".join(ALLOWED_SEVERITIES)}.')
    if not reason or not reason.strip():
        raise ValueError('reason is required for escalation.')

    row = violation_dal.get_by_id(conn, vid)
    if not row:
        raise LookupError('Violation not found.')
    if row['status'] != 'open':
        raise ValueError('Only open violations can be escalated.')
    if row['severity'] == new_severity:
        raise ValueError(f'Violation is already at severity "{new_severity}".')

    conn.execute(
        'UPDATE violations SET severity = ? WHERE id = ?',
        (new_severity, vid)
    )
    audit_dal.write(conn, 'VIOLATION_ESCALATED', user_id=admin_id,
                    entity_type='violation', entity_id=vid,
                    details={'old_severity': row['severity'],
                              'new_severity': new_severity,
                              'reason': reason})


def get_violation_detail(conn, vid: int) -> dict | None:
    """Return a violation with its appeal (if any) and reporter/target usernames."""
    from app.models import row_to_dict, rows_to_list
    row = row_to_dict(conn.execute(
        'SELECT v.*, '
        'u1.username as target_username, u2.username as reporter_username '
        'FROM violations v '
        'JOIN users u1 ON v.user_id   = u1.id '
        'JOIN users u2 ON v.reported_by = u2.id '
        'WHERE v.id = ?',
        (vid,)
    ).fetchone())
    if not row:
        return None
    # Attach appeal if present
    appeal = row_to_dict(conn.execute(
        'SELECT va.*, u.username as appellant_username '
        'FROM violation_appeals va JOIN users u ON va.appellant_id = u.id '
        'WHERE va.violation_id = ?',
        (vid,)
    ).fetchone())
    row['appeal'] = appeal
    return row


# ---------------------------------------------------------------------------
# User detail
# ---------------------------------------------------------------------------

def get_user_detail(conn, user_id: int) -> dict | None:
    """Return a user with their moderation history summary."""
    from app.utils import mask_email
    from app.dal import rating_dal
    from app.models import rows_to_list

    user = user_dal.get_by_id(conn, user_id)
    if not user:
        return None

    stats = rating_dal.get_stats(conn, user_id)

    violations = rows_to_list(conn.execute(
        'SELECT id, violation_type, severity, status, created_at, resolved_at '
        'FROM violations WHERE user_id = ? ORDER BY id DESC LIMIT 10',
        (user_id,)
    ).fetchall())

    bans = [v for v in violations if v['violation_type'] == 'admin_ban']

    return {
        'id':          user['id'],
        'username':    user['username'],
        'email':       mask_email(user['email']),
        'role':        user['role'],
        'is_active':   bool(user['is_active']),
        'muted_until': user.get('muted_until'),
        'credit_balance': user['credit_balance'],
        'created_at':  user['created_at'],
        'avg_rating':  round(stats['avg_score'], 2) if stats['avg_score'] else None,
        'total_ratings': stats['total'],
        'recent_violations': violations,
        'ban_count':   len(bans),
    }


# ---------------------------------------------------------------------------
# Permission management (super-admin only)
# ---------------------------------------------------------------------------

def grant_permission(conn, granting_admin_id: int, target_admin_id: int,
                     resource: str, can_write: bool = False,
                     scope: dict = None) -> None:
    """Grant or update a fine-grained resource permission for target_admin_id."""
    if resource not in admin_dal.VALID_RESOURCES:
        raise ValueError(
            f'resource must be one of: {", ".join(sorted(admin_dal.VALID_RESOURCES))}.'
        )
    target = user_dal.get_by_id(conn, target_admin_id)
    if not target:
        raise LookupError('Target admin not found.')
    if target['role'] not in ('admin', 'auditor'):
        raise ValueError('Permissions can only be assigned to admins and auditors.')
    if target_admin_id == granting_admin_id:
        raise ValueError('Admins cannot modify their own permissions.')

    admin_dal.upsert_permission(
        conn, target_admin_id, resource,
        can_read=True, can_write=can_write,
        scope=scope, granted_by=granting_admin_id,
    )
    audit_dal.write(conn, 'PERMISSION_GRANTED', user_id=granting_admin_id,
                    entity_type='admin_permissions', entity_id=target_admin_id,
                    details={'resource': resource, 'can_write': can_write,
                              'scope': scope})


def revoke_permission(conn, granting_admin_id: int, target_admin_id: int,
                      resource: str) -> None:
    if target_admin_id == granting_admin_id:
        raise ValueError('Admins cannot modify their own permissions.')
    deleted = admin_dal.delete_permission(conn, target_admin_id, resource)
    if not deleted:
        raise LookupError(
            f'No permission found for that admin on resource "{resource}".'
        )
    audit_dal.write(conn, 'PERMISSION_REVOKED', user_id=granting_admin_id,
                    entity_type='admin_permissions', entity_id=target_admin_id,
                    details={'resource': resource})
