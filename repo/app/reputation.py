"""
Reputation / Ratings blueprint.
- POST /api/reputation/rate           — rate a completed session
- GET  /api/reputation/ratings/<uid>  — list ratings for a user
- GET  /api/reputation/score/<uid>    — reputation score summary
- GET  /api/reputation/violations     — report / list violations
- POST /api/violations/report         — (mounted on reputation bp)
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import utcnow, login_required, admin_required, write_audit_log

reputation_bp = Blueprint('reputation', __name__)


@reputation_bp.route('/rate', methods=['POST'])
@login_required
def rate_session():
    data = request.get_json(force=True) or {}
    session_id = data.get('session_id')
    score = data.get('score')
    comment = (data.get('comment') or '').strip()[:500]

    if not session_id:
        return jsonify({'error': 'session_id is required.'}), 400
    try:
        score = int(score)
        if score < 1 or score > 5:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'score must be an integer between 1 and 5.'}), 400

    with db() as conn:
        session = row_to_dict(conn.execute(
            'SELECT * FROM sessions WHERE id = ?', (session_id,)
        ).fetchone())
        if not session:
            return jsonify({'error': 'Session not found.'}), 404
        if session['status'] != 'completed':
            return jsonify({'error': 'Can only rate completed sessions.'}), 409
        if g.user_id not in (session['initiator_id'], session['participant_id']):
            return jsonify({'error': 'You are not a participant in this session.'}), 403

        # Who are we rating?
        ratee_id = (session['participant_id']
                    if g.user_id == session['initiator_id']
                    else session['initiator_id'])

        # Duplicate check
        existing = conn.execute(
            'SELECT id FROM ratings WHERE rater_id = ? AND session_id = ?',
            (g.user_id, session_id)
        ).fetchone()
        if existing:
            return jsonify({'error': 'You have already rated this session.'}), 409

        cur = conn.execute(
            'INSERT INTO ratings (rater_id, ratee_id, session_id, score, comment, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (g.user_id, ratee_id, session_id, score, comment, utcnow())
        )
        write_audit_log(conn, 'RATING_SUBMITTED', user_id=g.user_id,
                        entity_type='rating', entity_id=cur.lastrowid,
                        details={'ratee_id': ratee_id, 'score': score})

    return jsonify({'message': 'Rating submitted.'}), 201


@reputation_bp.route('/ratings/<int:user_id>', methods=['GET'])
@login_required
def list_ratings(user_id: int):
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(50, int(request.args.get('per_page', 10)))
    offset = (page - 1) * per_page

    with db() as conn:
        rows = rows_to_list(conn.execute(
            'SELECT r.id, r.score, r.comment, r.created_at, '
            'u.username as rater_name '
            'FROM ratings r JOIN users u ON r.rater_id = u.id '
            'WHERE r.ratee_id = ? '
            'ORDER BY r.id DESC LIMIT ? OFFSET ?',
            (user_id, per_page, offset)
        ).fetchall())
        total = conn.execute(
            'SELECT COUNT(*) FROM ratings WHERE ratee_id = ?', (user_id,)
        ).fetchone()[0]

    return jsonify({'ratings': rows, 'total': total}), 200


@reputation_bp.route('/score/<int:user_id>', methods=['GET'])
@login_required
def reputation_score(user_id: int):
    with db() as conn:
        stats = row_to_dict(conn.execute(
            'SELECT COUNT(*) as total, AVG(score) as avg_score, '
            'SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END) as positive '
            'FROM ratings WHERE ratee_id = ?',
            (user_id,)
        ).fetchone())
        sessions = conn.execute(
            'SELECT COUNT(*) FROM sessions '
            'WHERE (initiator_id = ? OR participant_id = ?) AND status = "completed"',
            (user_id, user_id)
        ).fetchone()[0]
        violations = conn.execute(
            'SELECT COUNT(*) FROM violations WHERE user_id = ? AND status = "resolved"',
            (user_id,)
        ).fetchone()[0]

    avg = round(stats['avg_score'], 2) if stats['avg_score'] else 0.0
    # Simple reputation score: avg * 20 + sessions * 2 - violations * 10
    rep_score = round(avg * 20 + sessions * 2 - violations * 10, 1)

    return jsonify({
        'user_id': user_id,
        'average_rating': avg,
        'total_ratings': stats['total'],
        'positive_ratings': stats['positive'],
        'sessions_completed': sessions,
        'violations_against': violations,
        'reputation_score': max(0, rep_score),
    }), 200


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------

@reputation_bp.route('/violations', methods=['POST'])
@login_required
def report_violation():
    data = request.get_json(force=True) or {}
    target_id = data.get('user_id')
    violation_type = (data.get('violation_type') or '').strip()
    description = (data.get('description') or '').strip()
    severity = data.get('severity', 'low')

    if not target_id or not violation_type or not description:
        return jsonify({'error': 'user_id, violation_type, and description are required.'}), 400
    if target_id == g.user_id:
        return jsonify({'error': 'Cannot report yourself.'}), 400
    if severity not in ('low', 'medium', 'high'):
        return jsonify({'error': 'severity must be low, medium, or high.'}), 400

    ALLOWED_TYPES = ('spam', 'harassment', 'fraud', 'no_show', 'abuse', 'other')
    if violation_type not in ALLOWED_TYPES:
        return jsonify({'error': f'violation_type must be one of: {", ".join(ALLOWED_TYPES)}.'}), 400

    with db() as conn:
        target = conn.execute(
            'SELECT id FROM users WHERE id = ?', (target_id,)
        ).fetchone()
        if not target:
            return jsonify({'error': 'Target user not found.'}), 404

        cur = conn.execute(
            'INSERT INTO violations '
            '(user_id, reported_by, violation_type, description, severity, '
            'status, created_at) VALUES (?, ?, ?, ?, ?, "open", ?)',
            (target_id, g.user_id, violation_type, description, severity, utcnow())
        )
        write_audit_log(conn, 'VIOLATION_REPORTED', user_id=g.user_id,
                        entity_type='violation', entity_id=cur.lastrowid,
                        details={'target_id': target_id, 'type': violation_type})

    return jsonify({'message': 'Violation report submitted.'}), 201


@reputation_bp.route('/violations', methods=['GET'])
@login_required
def list_violations():
    # Admins see all; users see reports they filed or against them
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page

    with db() as conn:
        if g.role == 'admin':
            status_filter = request.args.get('status')
            query = (
                'SELECT v.*, u1.username as target_username, '
                'u2.username as reporter_username '
                'FROM violations v '
                'JOIN users u1 ON v.user_id = u1.id '
                'JOIN users u2 ON v.reported_by = u2.id '
                'WHERE 1=1'
            )
            params = []
            if status_filter:
                query += ' AND v.status = ?'
                params.append(status_filter)
            total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
            query += ' ORDER BY v.id DESC LIMIT ? OFFSET ?'
            params += [per_page, offset]
        else:
            query = (
                'SELECT v.*, u1.username as target_username, '
                'u2.username as reporter_username '
                'FROM violations v '
                'JOIN users u1 ON v.user_id = u1.id '
                'JOIN users u2 ON v.reported_by = u2.id '
                'WHERE (v.user_id = ? OR v.reported_by = ?)'
            )
            params = [g.user_id, g.user_id]
            total = conn.execute(
                'SELECT COUNT(*) FROM violations WHERE user_id = ? OR reported_by = ?',
                (g.user_id, g.user_id)
            ).fetchone()[0]
            query += ' ORDER BY v.id DESC LIMIT ? OFFSET ?'
            params += [per_page, offset]

        rows = rows_to_list(conn.execute(query, params).fetchall())

    return jsonify({'violations': rows, 'total': total}), 200


@reputation_bp.route('/violations/<int:vid>/resolve', methods=['PUT'])
@admin_required
def resolve_violation(vid: int):
    data = request.get_json(force=True) or {}
    decision = data.get('decision')  # 'resolved' or 'dismissed'
    notes = (data.get('notes') or '').strip()

    if decision not in ('resolved', 'dismissed'):
        return jsonify({'error': 'decision must be "resolved" or "dismissed".'}), 400

    with db() as conn:
        row = conn.execute('SELECT * FROM violations WHERE id = ?', (vid,)).fetchone()
        if not row:
            return jsonify({'error': 'Violation not found.'}), 404
        if row['status'] != 'open':
            return jsonify({'error': 'Violation is already closed.'}), 409

        conn.execute(
            'UPDATE violations SET status = ?, resolution_notes = ?, '
            'resolved_at = ?, resolved_by = ? WHERE id = ?',
            (decision, notes, utcnow(), g.user_id, vid)
        )
        write_audit_log(conn, f'VIOLATION_{decision.upper()}',
                        user_id=g.user_id, entity_type='violation', entity_id=vid,
                        details={'decision': decision})

    return jsonify({'message': f'Violation {decision}.'}), 200
