"""
Peer Matching blueprint.
- POST/PUT /api/matching/profile          — create or update matching profile
- GET       /api/matching/profile         — own profile
- GET       /api/matching/search          — search by skill overlap
- POST      /api/matching/sessions        — request a session (idempotent)
- GET       /api/matching/sessions        — list own sessions
- PUT       /api/matching/sessions/<id>   — update session status
"""

import json
from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import (
    utcnow, login_required,
    check_idempotency, store_idempotency,
    write_audit_log,
)

matching_bp = Blueprint('matching', __name__)


# ---------------------------------------------------------------------------
# Matching profile
# ---------------------------------------------------------------------------

@matching_bp.route('/profile', methods=['GET'])
@login_required
def get_profile():
    with db() as conn:
        profile = row_to_dict(conn.execute(
            'SELECT * FROM matching_profiles WHERE user_id = ?', (g.user_id,)
        ).fetchone())
    if not profile:
        return jsonify({'profile': None}), 200
    profile['skills_offered'] = json.loads(profile['skills_offered'])
    profile['skills_needed'] = json.loads(profile['skills_needed'])
    profile['availability'] = json.loads(profile['availability'])
    return jsonify({'profile': profile}), 200


@matching_bp.route('/profile', methods=['POST', 'PUT'])
@login_required
def upsert_profile():
    data = request.get_json(force=True) or {}
    skills_offered = data.get('skills_offered', [])
    skills_needed = data.get('skills_needed', [])
    availability = data.get('availability', {})
    bio = (data.get('bio') or '').strip()[:500]
    is_active = bool(data.get('is_active', True))

    if not isinstance(skills_offered, list) or not isinstance(skills_needed, list):
        return jsonify({'error': 'skills_offered and skills_needed must be arrays.'}), 400
    if not isinstance(availability, dict):
        return jsonify({'error': 'availability must be an object.'}), 400

    # Sanitize skill strings
    skills_offered = [str(s).strip()[:64] for s in skills_offered if str(s).strip()][:20]
    skills_needed = [str(s).strip()[:64] for s in skills_needed if str(s).strip()][:20]

    now = utcnow()
    with db() as conn:
        existing = conn.execute(
            'SELECT id FROM matching_profiles WHERE user_id = ?', (g.user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                'UPDATE matching_profiles SET skills_offered=?, skills_needed=?, '
                'availability=?, bio=?, is_active=?, updated_at=? WHERE user_id=?',
                (json.dumps(skills_offered), json.dumps(skills_needed),
                 json.dumps(availability), bio, 1 if is_active else 0,
                 now, g.user_id)
            )
            action = 'MATCHING_PROFILE_UPDATED'
        else:
            conn.execute(
                'INSERT INTO matching_profiles '
                '(user_id, skills_offered, skills_needed, availability, bio, '
                'is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (g.user_id, json.dumps(skills_offered), json.dumps(skills_needed),
                 json.dumps(availability), bio, 1 if is_active else 0, now, now)
            )
            action = 'MATCHING_PROFILE_CREATED'
        write_audit_log(conn, action, user_id=g.user_id,
                        entity_type='matching_profile', entity_id=g.user_id)

    return jsonify({'message': 'Profile saved.'}), 200


# ---------------------------------------------------------------------------
# Search for matches
# ---------------------------------------------------------------------------

@matching_bp.route('/search', methods=['GET'])
@login_required
def search():
    skill = (request.args.get('skill') or '').strip().lower()
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(50, int(request.args.get('per_page', 10)))
    offset = (page - 1) * per_page

    with db() as conn:
        # Fetch all active profiles except own
        rows = rows_to_list(conn.execute(
            'SELECT mp.*, u.username '
            'FROM matching_profiles mp JOIN users u ON mp.user_id = u.id '
            'WHERE mp.is_active = 1 AND mp.user_id != ? AND u.is_active = 1',
            (g.user_id,)
        ).fetchall())

    if skill:
        # Filter by skill in offered list (server-side, since JSON in SQLite)
        filtered = []
        for r in rows:
            offered = json.loads(r['skills_offered'])
            if any(skill in s.lower() for s in offered):
                filtered.append(r)
        rows = filtered

    total = len(rows)
    rows = rows[offset: offset + per_page]

    result = []
    for r in rows:
        result.append({
            'user_id': r['user_id'],
            'username': r['username'],
            'skills_offered': json.loads(r['skills_offered']),
            'skills_needed': json.loads(r['skills_needed']),
            'bio': r['bio'],
        })

    return jsonify({'profiles': result, 'total': total}), 200


# ---------------------------------------------------------------------------
# Sessions (peer exchange)
# ---------------------------------------------------------------------------

@matching_bp.route('/sessions', methods=['POST'])
@login_required
def request_session():
    data = request.get_json(force=True) or {}
    participant_id = data.get('participant_id')
    description = (data.get('description') or '').strip()[:1000]
    duration_minutes = data.get('duration_minutes')
    credit_amount = data.get('credit_amount', 0.0)
    scheduled_at = data.get('scheduled_at')
    idempotency_key = request.headers.get('Idempotency-Key') or data.get('idempotency_key')

    if not participant_id:
        return jsonify({'error': 'participant_id is required.'}), 400
    if participant_id == g.user_id:
        return jsonify({'error': 'Cannot create a session with yourself.'}), 400

    try:
        credit_amount = float(credit_amount)
        if credit_amount < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'credit_amount must be a non-negative number.'}), 400

    now = utcnow()

    with db() as conn:
        # Idempotency check
        exists, cached = check_idempotency(conn, idempotency_key, g.user_id)
        if exists:
            return jsonify(cached['body']), cached['status']

        # Validate participant exists and is active
        participant = conn.execute(
            'SELECT id FROM users WHERE id = ? AND is_active = 1', (participant_id,)
        ).fetchone()
        if not participant:
            return jsonify({'error': 'Participant not found or inactive.'}), 404

        cur = conn.execute(
            'INSERT INTO sessions '
            '(initiator_id, participant_id, status, description, '
            'duration_minutes, credit_amount, scheduled_at, '
            'created_at, updated_at, idempotency_key) '
            'VALUES (?, ?, "pending", ?, ?, ?, ?, ?, ?, ?)',
            (g.user_id, participant_id, description,
             duration_minutes, credit_amount, scheduled_at,
             now, now, idempotency_key)
        )
        session_id = cur.lastrowid
        write_audit_log(conn, 'SESSION_REQUESTED', user_id=g.user_id,
                        entity_type='session', entity_id=session_id)

        body = {'message': 'Session request sent.', 'session_id': session_id}
        store_idempotency(conn, idempotency_key, g.user_id, 201, body)

    return jsonify(body), 201


@matching_bp.route('/sessions', methods=['GET'])
@login_required
def list_sessions():
    role_filter = request.args.get('role', 'all')  # initiator | participant | all
    status_filter = request.args.get('status')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page

    with db() as conn:
        query = (
            'SELECT s.*, '
            'u1.username as initiator_name, u2.username as participant_name '
            'FROM sessions s '
            'JOIN users u1 ON s.initiator_id = u1.id '
            'JOIN users u2 ON s.participant_id = u2.id '
            'WHERE 1=1'
        )
        params = []
        if role_filter == 'initiator':
            query += ' AND s.initiator_id = ?'
            params.append(g.user_id)
        elif role_filter == 'participant':
            query += ' AND s.participant_id = ?'
            params.append(g.user_id)
        else:
            query += ' AND (s.initiator_id = ? OR s.participant_id = ?)'
            params += [g.user_id, g.user_id]
        if status_filter:
            query += ' AND s.status = ?'
            params.append(status_filter)

        total = conn.execute(
            f'SELECT COUNT(*) FROM ({query})', params
        ).fetchone()[0]
        query += ' ORDER BY s.id DESC LIMIT ? OFFSET ?'
        params += [per_page, offset]
        rows = rows_to_list(conn.execute(query, params).fetchall())

    # Remove internal idempotency key from output
    for r in rows:
        r.pop('idempotency_key', None)

    return jsonify({'sessions': rows, 'total': total}), 200


@matching_bp.route('/sessions/<int:session_id>', methods=['PUT'])
@login_required
def update_session(session_id: int):
    data = request.get_json(force=True) or {}
    new_status = data.get('status')

    valid_transitions = {
        'pending': ['active', 'cancelled'],
        'active': ['completed', 'cancelled'],
    }

    with db() as conn:
        session = row_to_dict(conn.execute(
            'SELECT * FROM sessions WHERE id = ?', (session_id,)
        ).fetchone())
        if not session:
            return jsonify({'error': 'Session not found.'}), 404

        # Only participants in the session can update it
        if g.user_id not in (session['initiator_id'], session['participant_id']) \
                and g.role != 'admin':
            return jsonify({'error': 'Access denied.'}), 403

        if new_status:
            allowed = valid_transitions.get(session['status'], [])
            if new_status not in allowed:
                return jsonify({
                    'error': f'Cannot transition from {session["status"]} to {new_status}.'
                }), 409

            now = utcnow()
            extra = {}
            if new_status == 'active':
                extra['started_at'] = now
            elif new_status == 'completed':
                extra['completed_at'] = now

            set_parts = ['status = ?', 'updated_at = ?']
            vals = [new_status, now]
            for k, v in extra.items():
                set_parts.append(f'{k} = ?')
                vals.append(v)
            vals.append(session_id)

            conn.execute(
                f'UPDATE sessions SET {", ".join(set_parts)} WHERE id = ?', vals
            )
            write_audit_log(conn, f'SESSION_{new_status.upper()}',
                            user_id=g.user_id, entity_type='session',
                            entity_id=session_id,
                            details={'old_status': session['status']})

    return jsonify({'message': f'Session updated to {new_status}.'}), 200


@matching_bp.route('/sessions/<int:session_id>', methods=['GET'])
@login_required
def get_session(session_id: int):
    with db() as conn:
        row = row_to_dict(conn.execute(
            'SELECT s.*, u1.username as initiator_name, u2.username as participant_name '
            'FROM sessions s '
            'JOIN users u1 ON s.initiator_id = u1.id '
            'JOIN users u2 ON s.participant_id = u2.id '
            'WHERE s.id = ?',
            (session_id,)
        ).fetchone())

    if not row:
        return jsonify({'error': 'Session not found.'}), 404

    if g.user_id not in (row['initiator_id'], row['participant_id']) \
            and g.role not in ('admin', 'auditor'):
        return jsonify({'error': 'Access denied.'}), 403

    row.pop('idempotency_key', None)
    return jsonify({'session': row}), 200
