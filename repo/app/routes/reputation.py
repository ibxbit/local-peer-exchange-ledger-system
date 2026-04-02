"""Reputation, ratings, violations, appeals routes."""

from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import login_required, admin_required
from app.services import rating_service
from app.dal import rating_dal, violation_dal

reputation_bp = Blueprint('reputation', __name__)


@reputation_bp.route('/rate', methods=['POST'])
@login_required
def rate_session():
    d = request.get_json(force=True) or {}
    try:
        score = int(d.get('score', 0))
        if score < 1 or score > 5:
            raise ValueError('score must be between 1 and 5.')
        with db() as conn:
            rating_service.submit_rating(
                conn, g.user_id,
                session_id=d.get('session_id'),
                score=score,
                comment=(d.get('comment') or '').strip()[:500],
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Rating submitted.'}), 201


@reputation_bp.route('/ratings/<int:user_id>', methods=['GET'])
@login_required
def list_ratings(user_id):
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(50, int(request.args.get('per_page', 10)))
    with db() as conn:
        rows, total = rating_dal.list_for_user(
            conn, user_id, per_page, (page - 1) * per_page
        )
    return jsonify({'ratings': rows, 'total': total}), 200


@reputation_bp.route('/score/<int:user_id>', methods=['GET'])
@login_required
def reputation_score(user_id):
    with db() as conn:
        score = rating_service.get_reputation_score(conn, user_id)
    return jsonify(score), 200


# ---- Violations ---------------------------------------------------------

@reputation_bp.route('/violations', methods=['POST'])
@login_required
def report_violation():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            vid = rating_service.report_violation(
                conn, g.user_id,
                target_id=d.get('user_id'),
                violation_type=(d.get('violation_type') or '').strip(),
                description=(d.get('description') or '').strip(),
                severity=d.get('severity', 'low'),
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Violation report submitted.', 'violation_id': vid}), 201


@reputation_bp.route('/violations', methods=['GET'])
@login_required
def list_violations():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    with db() as conn:
        rows, total = violation_dal.list_violations(
            conn,
            user_id=g.user_id,
            status=request.args.get('status'),
            limit=per_page,
            offset=(page - 1) * per_page,
            include_all=(g.role == 'admin'),
        )
    return jsonify({'violations': rows, 'total': total}), 200


@reputation_bp.route('/violations/<int:vid>/resolve', methods=['PUT'])
@admin_required
def resolve_violation(vid):
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            rating_service.resolve_violation(
                conn, g.user_id, vid,
                decision=d.get('decision'),
                notes=(d.get('notes') or '').strip(),
            )
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Violation resolved.'}), 200


# ---- Appeals ------------------------------------------------------------

@reputation_bp.route('/violations/<int:violation_id>/appeal', methods=['POST'])
@login_required
def file_appeal(violation_id):
    d = request.get_json(force=True) or {}
    reason = (d.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'reason is required.'}), 400
    try:
        with db() as conn:
            aid = rating_service.file_appeal(conn, g.user_id, violation_id, reason)
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Appeal filed.', 'appeal_id': aid}), 201


@reputation_bp.route('/appeals', methods=['GET'])
@admin_required
def list_appeals():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    with db() as conn:
        rows, total = violation_dal.list_appeals(
            conn,
            status=request.args.get('status'),
            limit=per_page, offset=(page - 1) * per_page,
        )
    return jsonify({'appeals': rows, 'total': total}), 200


@reputation_bp.route('/appeals/<int:appeal_id>/resolve', methods=['PUT'])
@admin_required
def resolve_appeal(appeal_id):
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            rating_service.resolve_appeal(
                conn, g.user_id, appeal_id,
                decision=d.get('decision'),
                notes=(d.get('notes') or '').strip(),
            )
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Appeal resolved.'}), 200
