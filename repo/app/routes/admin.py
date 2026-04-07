"""
Admin routes — analytics, user management, violations, appeals, bans, permissions.

Permission model enforced here:
  - @admin_required          → role must be 'admin' or 'auditor'
  - require_permission(...)  → fine-grained resource check (super-admin bypasses)
  - Auditor write attempts return 403 before reaching require_permission.

Fine-grained session scoping (rooms / time slots):
  Restricted admins on the 'sessions' resource have a scope dict like:
    {"scheduled_after": "2025-04-01", "scheduled_before": "2025-06-30"}
  This is extracted from admin_permissions.scope and passed to session_dal.list_all.
"""

from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import (admin_required, auditor_or_admin_required,
                        check_idempotency, store_idempotency, mask_email)
from app.services import admin_service, ledger_service
from app.dal import (
    session_dal, user_dal, violation_dal, admin_dal, rating_dal, resource_dal,
)

admin_bp = Blueprint('admin', __name__)


def _is_write_allowed() -> bool:
    """Auditors are always read-only."""
    return g.role == 'admin'


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@admin_bp.route('/analytics', methods=['GET'])
@auditor_or_admin_required
def analytics():
    with db() as conn:
        data = admin_service.get_analytics(conn)
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@admin_bp.route('/users', methods=['GET'])
@auditor_or_admin_required
def list_users():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'users')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        base_rows, total = user_dal.list_users(
            conn,
            role=request.args.get('role'),
            search=request.args.get('search', '').strip(),
            limit=per_page, offset=(page - 1) * per_page,
        )
        rows = []
        for u in base_rows:
            stats  = rating_dal.get_stats(conn, u['id'])
            sess   = conn.execute(
                'SELECT COUNT(*) FROM sessions WHERE initiator_id=? OR participant_id=?',
                (u['id'], u['id'])
            ).fetchone()[0]
            open_v = conn.execute(
                "SELECT COUNT(*) FROM violations WHERE user_id=? AND status='open'",
                (u['id'],)
            ).fetchone()[0]
            rows.append({
                **{k: v for k, v in u.items()
                   if k not in ('password_hash', 'lockout_until')},
                'email':           mask_email(u['email']),
                'is_active':       bool(u['is_active']),
                'avg_rating':      round(stats['avg_score'], 2) if stats['avg_score'] else None,
                'session_count':   sess,
                'open_violations': open_v,
            })
    return jsonify({'users': rows, 'total': total, 'page': page}), 200


@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@auditor_or_admin_required
def get_user(user_id):
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'users')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        detail = admin_service.get_user_detail(conn, user_id)
    if not detail:
        return jsonify({'error': 'User not found.'}), 404
    return jsonify(detail), 200


@admin_bp.route('/users/<int:user_id>/ban', methods=['PUT'])
@admin_required
def ban_user(user_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'bans', write=True)
            admin_service.ban_user(conn, g.user_id, user_id,
                                    reason=(d.get('reason') or '').strip())
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'User banned.'}), 200


@admin_bp.route('/users/<int:user_id>/unban', methods=['PUT'])
@admin_required
def unban_user(user_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'bans', write=True)
            admin_service.unban_user(conn, g.user_id, user_id,
                                      reason=(d.get('reason') or '').strip())
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'User reinstated.'}), 200


@admin_bp.route('/users/<int:user_id>/mute', methods=['PUT'])
@admin_required
def mute_user(user_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    muted_until = d.get('muted_until')
    if not muted_until:
        return jsonify({'error': 'muted_until (ISO-8601) is required.'}), 400
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'mutes', write=True)
            admin_service.mute_user(conn, g.user_id, user_id, muted_until,
                                     reason=d.get('reason'))
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except LookupError as e:
            return jsonify({'error': str(e)}), 404
    return jsonify({'message': 'User muted.'}), 200


@admin_bp.route('/users/<int:user_id>/unmute', methods=['PUT'])
@admin_required
def unmute_user(user_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'mutes', write=True)
            admin_service.unmute_user(conn, g.user_id, user_id)
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
    return jsonify({'message': 'User unmuted.'}), 200


# ---------------------------------------------------------------------------
# Sessions (with time-slot scope)
# ---------------------------------------------------------------------------

@admin_bp.route('/resources', methods=['GET'])
@auditor_or_admin_required
def list_resources():
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'sessions')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        is_active = request.args.get('is_active')
        active_filter = None
        if is_active in ('0', '1'):
            active_filter = int(is_active)

        rows, total = resource_dal.list_resources(
            conn,
            building=request.args.get('building'),
            room=request.args.get('room'),
            time_slot=request.args.get('time_slot'),
            is_active=active_filter,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
    return jsonify({'resources': rows, 'total': total, 'page': page}), 200


@admin_bp.route('/resources', methods=['POST'])
@admin_required
def create_resource():
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403

    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'sessions', write=True)
            rid = admin_service.create_schedule_resource(
                conn,
                g.user_id,
                building=d.get('building'),
                room=d.get('room'),
                time_slot=d.get('time_slot'),
            )
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Resource created.', 'resource_id': rid}), 201


@admin_bp.route('/resources/<int:resource_id>', methods=['PUT'])
@admin_required
def update_resource(resource_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403

    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'sessions', write=True)
            admin_service.update_schedule_resource(
                conn,
                g.user_id,
                resource_id,
                building=d.get('building'),
                room=d.get('room'),
                time_slot=d.get('time_slot'),
                is_active=d.get('is_active'),
            )
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except LookupError as e:
            return jsonify({'error': str(e)}), 404
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Resource updated.'}), 200


@admin_bp.route('/resources/<int:resource_id>', methods=['DELETE'])
@admin_required
def deactivate_resource(resource_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403

    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'sessions', write=True)
            admin_service.deactivate_schedule_resource(conn, g.user_id, resource_id)
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except LookupError as e:
            return jsonify({'error': str(e)}), 404
    return jsonify({'message': 'Resource deactivated.'}), 200

@admin_bp.route('/sessions', methods=['GET'])
@auditor_or_admin_required
def list_sessions():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        try:
            scope = admin_service.require_permission(conn, g.user_id, 'sessions')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        # Extract resource-dimension constraints from scope
        # (fine-grained admin access control: building / room / time-slot)
        scheduled_after  = None
        scheduled_before = None
        buildings        = None
        rooms            = None
        time_slots       = None
        if scope:
            scheduled_after  = scope.get('scheduled_after')
            scheduled_before = scope.get('scheduled_before')
            buildings        = scope.get('buildings')   # list[str] | None
            rooms            = scope.get('rooms')       # list[str] | None
            time_slots       = scope.get('time_slots')  # list[str] | None

        rows, total = session_dal.list_all(
            conn,
            status=request.args.get('status'),
            scheduled_after=scheduled_after,
            scheduled_before=scheduled_before,
            buildings=buildings,
            rooms=rooms,
            time_slots=time_slots,
            limit=per_page, offset=(page - 1) * per_page,
        )
    return jsonify({'sessions': rows, 'total': total}), 200


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------

@admin_bp.route('/violations', methods=['GET'])
@auditor_or_admin_required
def list_violations():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        try:
            scope = admin_service.require_permission(conn, g.user_id, 'violations')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        # Scoped admins may be restricted to certain severity levels
        status_filter   = request.args.get('status')
        severity_filter = request.args.get('severity')
        if scope and 'severity' in scope:
            allowed_severities = scope['severity']
            if severity_filter and severity_filter not in allowed_severities:
                return jsonify({'violations': [], 'total': 0}), 200
            if not severity_filter:
                severity_filter = allowed_severities  # list → multi-value filter

        query = (
            'SELECT v.*, u1.username as target_username, '
            'u2.username as reporter_username '
            'FROM violations v '
            'JOIN users u1 ON v.user_id    = u1.id '
            'JOIN users u2 ON v.reported_by = u2.id '
            'WHERE 1=1'
        )
        params = []
        if status_filter:
            query += ' AND v.status = ?'
            params.append(status_filter)
        if severity_filter:
            if isinstance(severity_filter, list):
                placeholders = ', '.join('?' * len(severity_filter))
                query += f' AND v.severity IN ({placeholders})'
                params.extend(severity_filter)
            else:
                query += ' AND v.severity = ?'
                params.append(severity_filter)

        total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
        query += ' ORDER BY v.id DESC LIMIT ? OFFSET ?'
        from app.models import rows_to_list
        rows = rows_to_list(conn.execute(query, params + [per_page, (page-1)*per_page]).fetchall())

    return jsonify({'violations': rows, 'total': total}), 200


@admin_bp.route('/violations/<int:vid>', methods=['GET'])
@auditor_or_admin_required
def get_violation(vid):
    with db() as conn:
        try:
            scope = admin_service.require_permission(conn, g.user_id, 'violations')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        detail = admin_service.get_violation_detail(conn, vid)
        if not detail:
            return jsonify({'error': 'Violation not found.'}), 404

        # Enforce severity scope
        if scope and 'severity' in scope:
            if detail['severity'] not in scope['severity']:
                return jsonify({'error': 'Access to this violation is outside your scope.'}), 403

    return jsonify(detail), 200


@admin_bp.route('/violations/<int:vid>/escalate', methods=['PUT'])
@admin_required
def escalate_violation(vid):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'violations', write=True)
            admin_service.escalate_violation(
                conn, g.user_id, vid,
                new_severity=(d.get('severity') or '').strip(),
                reason=(d.get('reason') or '').strip(),
            )
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Violation severity updated.'}), 200


# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------

@admin_bp.route('/appeals', methods=['GET'])
@auditor_or_admin_required
def list_appeals():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'appeals')
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403

        from app.dal import violation_dal as vdal
        rows, total = vdal.list_appeals(
            conn, status=request.args.get('status'),
            limit=per_page, offset=(page - 1) * per_page,
        )
    return jsonify({'appeals': rows, 'total': total}), 200


@admin_bp.route('/appeals/<int:appeal_id>/resolve', methods=['PUT'])
@admin_required
def resolve_appeal(appeal_id):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    with db() as conn:
        try:
            admin_service.require_permission(conn, g.user_id, 'appeals', write=True)
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        from app.services import rating_service
        try:
            rating_service.resolve_appeal(
                conn, g.user_id, appeal_id,
                decision=d.get('decision'),
                notes=(d.get('notes') or '').strip(),
            )
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Appeal resolved.'}), 200


# ---------------------------------------------------------------------------
# Permission management (super-admin only — must have no rows themselves)
# ---------------------------------------------------------------------------

@admin_bp.route('/permissions/<int:target_admin_id>', methods=['GET'])
@admin_required
def list_permissions(target_admin_id):
    with db() as conn:
        # Only super-admins (no restriction rows) may manage permissions
        if admin_dal.has_any_permission(conn, g.user_id):
            return jsonify({'error': 'Only super-admins can manage permissions.'}), 403
        perms = admin_dal.list_for_admin(conn, target_admin_id)
    return jsonify({'admin_id': target_admin_id, 'permissions': perms}), 200


@admin_bp.route('/permissions/<int:target_admin_id>/<resource>', methods=['PUT'])
@admin_required
def grant_permission(target_admin_id, resource):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    d = request.get_json(force=True) or {}
    with db() as conn:
        if admin_dal.has_any_permission(conn, g.user_id):
            return jsonify({'error': 'Only super-admins can manage permissions.'}), 403
        try:
            admin_service.grant_permission(
                conn, g.user_id, target_admin_id, resource,
                can_write=bool(d.get('can_write', False)),
                scope=d.get('scope'),  # None or dict
            )
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': f'Permission granted: {resource}.'}), 200


@admin_bp.route('/permissions/<int:target_admin_id>/<resource>', methods=['DELETE'])
@admin_required
def revoke_permission(target_admin_id, resource):
    if not _is_write_allowed():
        return jsonify({'error': 'Auditors cannot perform write operations.'}), 403
    with db() as conn:
        if admin_dal.has_any_permission(conn, g.user_id):
            return jsonify({'error': 'Only super-admins can manage permissions.'}), 403
        try:
            admin_service.revoke_permission(conn, g.user_id, target_admin_id, resource)
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
    return jsonify({'message': f'Permission revoked: {resource}.'}), 200
