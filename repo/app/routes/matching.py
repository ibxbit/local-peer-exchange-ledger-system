"""Matching routes — profiles, search, sessions, queue, blacklist, HTMX partials."""

from html import escape as _esc
from flask import Blueprint, request, jsonify, g
from app.models import db
from app.utils import login_required, admin_required, check_idempotency, store_idempotency
from app.services import matching_service
from app.dal import matching_dal, session_dal

matching_bp = Blueprint('matching', __name__)

_HTML = 'text/html; charset=utf-8'


# ---- Profile ------------------------------------------------------------

@matching_bp.route('/profile', methods=['GET'])
@login_required
def get_profile():
    import json
    with db() as conn:
        profile = matching_dal.get_profile(conn, g.user_id)
    if not profile:
        return jsonify({'profile': None}), 200
    profile['skills_offered']       = json.loads(profile['skills_offered'])
    profile['skills_needed']        = json.loads(profile['skills_needed'])
    profile['availability']         = json.loads(profile['availability'])
    profile['tags']                 = json.loads(profile.get('tags') or '[]')
    profile['preferred_time_slots'] = json.loads(profile.get('preferred_time_slots') or '[]')
    return jsonify({'profile': profile}), 200


@matching_bp.route('/profile', methods=['POST', 'PUT'])
@login_required
def upsert_profile():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            matching_service.save_profile(
                conn, g.user_id,
                skills_offered=d.get('skills_offered', []),
                skills_needed=d.get('skills_needed', []),
                availability=d.get('availability', {}),
                bio=d.get('bio', ''),
                is_active=d.get('is_active', True),
                tags=d.get('tags', []),
                preferred_time_slots=d.get('preferred_time_slots', []),
                category=d.get('category', ''),
            )
    except (ValueError, PermissionError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Profile saved.'}), 200


# ---- Search -------------------------------------------------------------

@matching_bp.route('/search', methods=['GET'])
@login_required
def search():
    skill     = request.args.get('skill', '')
    tag       = request.args.get('tag', '')
    time_slot = request.args.get('time_slot', '')
    page      = max(1, int(request.args.get('page', 1)))
    per_page  = min(50, int(request.args.get('per_page', 10)))
    with db() as conn:
        profiles = matching_service.search_peers(conn, g.user_id, skill, tag, time_slot)
    total  = len(profiles)
    offset = (page - 1) * per_page
    return jsonify({
        'profiles': profiles[offset: offset + per_page],
        'total': total,
    }), 200


# ---- HTMX: peer search partial ------------------------------------------

@matching_bp.route('/peers-partial', methods=['GET'])
@login_required
def peers_partial():
    """HTMX endpoint — returns an HTML fragment of peer search result cards."""
    skill     = request.args.get('skill', '')
    tag       = request.args.get('tag', '')
    time_slot = request.args.get('time_slot', '')
    page      = max(1, int(request.args.get('page', 1)))
    per_page  = min(50, int(request.args.get('per_page', 10)))

    with db() as conn:
        profiles = matching_service.search_peers(conn, g.user_id, skill, tag, time_slot)

    offset        = (page - 1) * per_page
    page_profiles = profiles[offset: offset + per_page]

    if not page_profiles:
        html = '<p class="empty-state" style="color:var(--c-text-sub);text-align:center;padding:2rem">No matching peers found. Try a different skill or tag.</p>'
    else:
        cards = []
        for p in page_profiles:
            username = _esc(p['username'])
            offered  = _esc(', '.join(p['skills_offered']) or '—')
            needed   = _esc(', '.join(p['skills_needed'])  or '—')
            bio_html = f'<div class="peer-bio" style="font-size:.8rem;margin-top:.25rem">{_esc(p["bio"])}</div>' if p.get('bio') else ''
            tags_html = ''
            if p.get('tags'):
                tags_html = '<div style="margin-top:.25rem">' + ''.join(
                    f'<span class="badge badge-info" style="margin-right:.25rem">{_esc(t)}</span>'
                    for t in p['tags']
                ) + '</div>'
            slots_html = ''
            if p.get('preferred_time_slots'):
                slots_html = (
                    '<div style="font-size:.75rem;color:var(--c-text-sub);margin-top:.2rem">'
                    'Available: ' + ', '.join(_esc(s) for s in p['preferred_time_slots']) +
                    '</div>'
                )
            cat_html = (
                f'<span class="badge badge-default" style="margin-right:.25rem">{_esc(p["category"])}</span>'
                if p.get('category') else ''
            )
            uid_val = p['user_id']
            cards.append(
                '<div class="card card-row">'
                '<div>'
                f'<strong>{username}</strong>{" " + cat_html if cat_html else ""}'
                f'<div style="margin-top:.35rem;font-size:.8rem;color:var(--c-text-sub)">Offers: <span style="color:var(--c-text)">{offered}</span></div>'
                f'<div style="font-size:.8rem;color:var(--c-text-sub)">Needs: <span style="color:var(--c-text)">{needed}</span></div>'
                f'{tags_html}{slots_html}{bio_html}'
                '</div>'
                '<div style="display:flex;gap:.5rem;flex-wrap:wrap">'
                f'<button class="btn btn-sm btn-secondary" onclick="App.viewRepModal({uid_val})">View Rep</button>'
                f'<button class="btn btn-sm btn-primary" onclick="App.openRequestSession({uid_val},\'{username}\')">Request Session</button>'
                '</div></div>'
            )
        html = '\n'.join(cards)

    return html, 200, {'Content-Type': _HTML}


# ---- HTMX: queue status partial -----------------------------------------

@matching_bp.route('/queue/<int:entry_id>/status-partial', methods=['GET'])
@login_required
def queue_status_partial(entry_id):
    """HTMX endpoint — returns an HTML fragment reflecting current queue entry state.

    While status == 'waiting' the fragment includes hx-trigger="every 10s" so
    HTMX keeps polling.  Once matched/expired/cancelled the polling attributes
    are omitted and the browser stops auto-refreshing.
    """
    with db() as conn:
        entry = matching_dal.get_queue_entry(conn, entry_id)
    if not entry:
        return '<div class="alert alert-error">Queue entry not found.</div>', 200, {'Content-Type': _HTML}
    if entry['user_id'] != g.user_id and g.role != 'admin':
        return '<div class="alert alert-error">Access denied.</div>', 200, {'Content-Type': _HTML}

    status       = entry['status']
    skill        = _esc(entry.get('skill') or '')
    retry_count  = entry.get('retry_count') or 0
    max_retries  = matching_service.MAX_RETRIES

    if status == 'matched':
        html = (
            f'<div class="match-status-card state-found" id="match-status-card">'
            f'<div class="state-icon">✅</div>'
            f'<h3 style="color:var(--c-success)">Match Found!</h3>'
            f'<p>You\'ve been matched for <strong>{skill}</strong>.</p>'
            f'<a class="btn btn-success btn-lg" href="#" onclick="App.navigate(\'sessions\')">View Session →</a>'
            f'</div>'
        )
    elif status in ('expired', 'cancelled', 'failed'):
        html = (
            f'<div class="match-status-card state-retrying" id="match-status-card">'
            f'<div class="state-icon">🔄</div>'
            f'<h3 style="color:var(--c-warn)">No Match Yet</h3>'
            f'<p>Your queue entry {_esc(status)}. Try joining again.</p>'
            f'<button class="btn btn-warn btn-lg" onclick="App.navigate(\'matching\')">Rejoin Queue</button>'
            f'<div class="match-poll-meta">Skill: {skill}</div>'
            f'</div>'
        )
    else:  # waiting — keep polling
        html = (
            f'<div class="match-status-card state-searching" id="match-status-card"'
            f'  hx-get="/api/matching/queue/{entry_id}/status-partial"'
            f'  hx-trigger="every 10s"'
            f'  hx-target="#match-status-card"'
            f'  hx-swap="outerHTML">'
            f'<div class="state-icon pulse"><div class="spinner" style="margin:0 auto"></div></div>'
            f'<h3 style="color:var(--c-accent);margin-top:.5rem">Searching…</h3>'
            f'<p>Looking for a peer who offers <strong>{skill}</strong>.</p>'
            f'<small style="color:var(--c-text-sub)">Attempt {retry_count}/{max_retries} · polls every 10 s</small><br>'
            f'<button class="btn btn-ghost btn-sm" style="margin-top:.5rem"'
            f'  hx-put="/api/matching/queue/{entry_id}/cancel"'
            f'  hx-target="#match-status-card"'
            f'  hx-swap="outerHTML"'
            f'  hx-confirm="Cancel this queue search?">Cancel</button>'
            f'</div>'
        )

    return html, 200, {'Content-Type': _HTML}


# ---- HTMX: sessions table partial ---------------------------------------

@matching_bp.route('/sessions-partial', methods=['GET'])
@login_required
def sessions_partial():
    """HTMX endpoint — returns an HTML fragment of session table rows."""
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(50, int(request.args.get('per_page', 15)))
    status_f = request.args.get('status', '')
    role_f   = request.args.get('role', 'all')

    with db() as conn:
        rows, total = session_dal.list_for_user(
            conn, g.user_id, role=role_f, status=status_f or None,
            limit=per_page, offset=(page - 1) * per_page,
        )

    if not rows:
        return '<tr><td colspan="7" style="text-align:center;color:var(--c-text-sub);padding:2rem">No sessions found.</td></tr>', 200, {'Content-Type': _HTML}

    status_badges = {
        'pending': 'warn', 'active': 'info', 'completed': 'success', 'cancelled': 'danger',
    }
    tr_list = []
    for s in rows:
        user_id  = g.user_id
        is_init  = s.get('initiator_id') == user_id
        peer     = _esc(s['participant_name'] if is_init else s['initiator_name'])
        role_lbl = 'Initiator' if is_init else 'Participant'
        st       = s.get('status', '')
        sb       = status_badges.get(st, 'default')
        sched    = _esc(s.get('scheduled_at') or '—')
        actions  = ''
        if st == 'pending':
            actions = (
                f'<button class="btn btn-sm btn-success sess-action" data-id="{s["id"]}" data-status="active">Accept</button> '
                f'<button class="btn btn-sm btn-danger sess-action" data-id="{s["id"]}" data-status="cancelled">Cancel</button>'
            )
        elif st == 'active':
            actions = (
                f'<button class="btn btn-sm btn-primary sess-action" data-id="{s["id"]}" data-status="completed">Complete</button> '
                f'<button class="btn btn-sm btn-danger sess-action" data-id="{s["id"]}" data-status="cancelled">Cancel</button>'
            )
        elif st == 'completed':
            sid_val = s['id']
            actions = f'<button class="btn btn-sm btn-warn" onclick="App.openRateSession({sid_val})">Rate</button>'
        tr_list.append(
            f'<tr>'
            f'<td>#{s["id"]}</td>'
            f'<td>{peer}</td>'
            f'<td><small>{role_lbl}</small></td>'
            f'<td><span class="badge badge-{sb}">{_esc(st)}</span></td>'
            f'<td>{s.get("credit_amount") or 0}</td>'
            f'<td>{sched}</td>'
            f'<td>{actions}</td>'
            f'</tr>'
        )
    return '\n'.join(tr_list), 200, {'Content-Type': _HTML}


# ---- Sessions -----------------------------------------------------------

@matching_bp.route('/sessions', methods=['POST'])
@login_required
def request_session():
    d    = request.get_json(force=True) or {}
    ikey = request.headers.get('Idempotency-Key') or d.get('idempotency_key')
    try:
        credit_amount = float(d.get('credit_amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'credit_amount must be a number.'}), 400

    with db() as conn:
        if ikey:
            exists, cached = check_idempotency(conn, ikey, g.user_id)
            if exists:
                return jsonify(cached['body']), cached['status']
        try:
            session_id = matching_service.request_session(
                conn,
                initiator_id=g.user_id,
                participant_id=d.get('participant_id'),
                description=(d.get('description') or '').strip()[:1000],
                duration_minutes=d.get('duration_minutes'),
                credit_amount=credit_amount,
                scheduled_at=d.get('scheduled_at'),
                idempotency_key=ikey,
                building=(d.get('building') or '').strip()[:64] or None,
                room=(d.get('room') or '').strip()[:64] or None,
                time_slot=(d.get('time_slot') or '').strip()[:64] or None,
            )
        except PermissionError as e:
            return jsonify({'error': str(e)}), 403
        except (ValueError, LookupError) as e:
            return jsonify({'error': str(e)}), 400
        body = {'message': 'Session request sent.', 'session_id': session_id}
        if ikey:
            store_idempotency(conn, ikey, g.user_id, 201, body)
    return jsonify(body), 201


@matching_bp.route('/sessions', methods=['GET'])
@login_required
def list_sessions():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    with db() as conn:
        rows, total = session_dal.list_for_user(
            conn, g.user_id,
            role=request.args.get('role', 'all'),
            status=request.args.get('status'),
            limit=per_page,
            offset=(page - 1) * per_page,
        )
    return jsonify({'sessions': rows, 'total': total}), 200


@matching_bp.route('/sessions/<int:session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    with db() as conn:
        s = session_dal.get_by_id(conn, session_id)
    if not s:
        return jsonify({'error': 'Session not found.'}), 404
    if g.user_id not in (s['initiator_id'], s['participant_id']) \
            and g.role not in ('admin', 'auditor'):
        return jsonify({'error': 'Access denied.'}), 403
    s.pop('idempotency_key', None)
    return jsonify({'session': s}), 200


@matching_bp.route('/sessions/<int:session_id>', methods=['PUT'])
@login_required
def update_session(session_id):
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            matching_service.update_session_status(
                conn, session_id, g.user_id,
                new_status=d.get('status'),
                actor_role=g.role,
                cancel_reason=d.get('cancel_reason'),
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except (ValueError, LookupError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Session updated.'}), 200


# ---- Queue --------------------------------------------------------------

@matching_bp.route('/queue/<int:entry_id>', methods=['GET'])
@login_required
def get_queue_entry(entry_id):
    with db() as conn:
        entry = matching_dal.get_queue_entry(conn, entry_id)
    if not entry:
        return jsonify({'error': 'Queue entry not found.'}), 404
    if entry['user_id'] != g.user_id and g.role != 'admin':
        return jsonify({'error': 'Access denied.'}), 403
    return jsonify({'entry': dict(entry)}), 200


@matching_bp.route('/queue/<int:entry_id>/cancel', methods=['PUT'])
@login_required
def cancel_queue_entry(entry_id):
    try:
        with db() as conn:
            matching_service.cancel_queue_entry(conn, entry_id, g.user_id, g.role)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    return jsonify({'message': 'Queue entry cancelled.'}), 200


@matching_bp.route('/queue', methods=['POST'])
@login_required
def join_queue():
    d = request.get_json(force=True) or {}
    try:
        with db() as conn:
            entry_id = matching_service.join_queue(
                conn, g.user_id,
                skill=d.get('skill', ''),
                priority=int(d.get('priority', 0)),
                expires_at=d.get('expires_at'),
            )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Added to queue.', 'entry_id': entry_id}), 201


@matching_bp.route('/queue/match', methods=['POST'])
@login_required
def auto_match():
    d = request.get_json(force=True, silent=True) or {}
    try:
        with db() as conn:
            result = matching_service.auto_match(conn, g.user_id,
                                                  skill=d.get('skill', ''))
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not result:
        return jsonify({'message': 'No match found yet.', 'matched': False}), 200
    return jsonify({'matched': True, 'session_id': result['session_id'],
                    'matched_with': result['user_id']}), 201


@matching_bp.route('/queue', methods=['GET'])
@login_required
def list_queue():
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(50, int(request.args.get('per_page', 20)))
    with db() as conn:
        rows, total = matching_dal.list_queue(
            conn,
            user_id=g.user_id if g.role == 'user' else None,
            status=request.args.get('status'),
            limit=per_page, offset=(page - 1) * per_page,
        )
    return jsonify({'queue': rows, 'total': total}), 200


# ---- Blacklist ----------------------------------------------------------

@matching_bp.route('/block', methods=['POST'])
@login_required
def block_user():
    d = request.get_json(force=True) or {}
    blocked_id = d.get('user_id')
    if not blocked_id:
        return jsonify({'error': 'user_id is required.'}), 400
    try:
        with db() as conn:
            matching_service.block_user(conn, g.user_id, int(blocked_id),
                                         d.get('reason'))
    except (ValueError, PermissionError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'User blocked.'}), 200


@matching_bp.route('/block/temporary', methods=['POST'])
@login_required
def block_user_temporary():
    """Create a temporary do-not-match entry with an expiry."""
    d = request.get_json(force=True) or {}
    blocked_id = d.get('user_id')
    if not blocked_id:
        return jsonify({'error': 'user_id is required.'}), 400
    try:
        with db() as conn:
            matching_service.block_user_temporary(
                conn, g.user_id, int(blocked_id),
                reason=d.get('reason'),
                duration_hours=d.get('duration_hours'),
                expires_at=d.get('expires_at'),
            )
    except (ValueError, PermissionError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Temporary block created.'}), 200


@matching_bp.route('/block/<int:blocked_id>', methods=['DELETE'])
@login_required
def unblock_user(blocked_id):
    with db() as conn:
        matching_service.unblock_user(conn, g.user_id, blocked_id)
    return jsonify({'message': 'User unblocked.'}), 200


@matching_bp.route('/block', methods=['GET'])
@login_required
def list_blocks():
    with db() as conn:
        blocks = matching_dal.list_blocks(conn, g.user_id)
    return jsonify({'blocks': blocks}), 200
