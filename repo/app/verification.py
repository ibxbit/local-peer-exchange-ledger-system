"""
Identity Verification blueprint.
Sensitive document data is AES-256-GCM encrypted at rest.
- POST /api/verification/submit       — user submits verification
- GET  /api/verification/status       — user views own status
- GET  /api/verification              — admin: list all pending/all
- PUT  /api/verification/<id>/review  — admin: approve/reject
"""

from flask import Blueprint, request, jsonify, g
from app.models import db, row_to_dict, rows_to_list
from app.utils import (
    utcnow, login_required, admin_required,
    encrypt_data, decrypt_data,
    write_audit_log, mask_document,
)

verification_bp = Blueprint('verification', __name__)

ALLOWED_DOC_TYPES = ('passport', 'national_id', 'drivers_license', 'utility_bill')


@verification_bp.route('/submit', methods=['POST'])
@login_required
def submit_verification():
    data = request.get_json(force=True) or {}
    doc_type = (data.get('document_type') or '').strip().lower()
    doc_data = (data.get('document_data') or '').strip()

    if doc_type not in ALLOWED_DOC_TYPES:
        return jsonify({'error': f'document_type must be one of: {", ".join(ALLOWED_DOC_TYPES)}.'}), 400
    if not doc_data:
        return jsonify({'error': 'document_data is required.'}), 400
    if len(doc_data) > 4096:
        return jsonify({'error': 'document_data exceeds maximum length.'}), 400

    enc = encrypt_data(doc_data)
    now = utcnow()

    with db() as conn:
        # Only one pending/verified per user; allow re-submit if rejected
        existing = conn.execute(
            'SELECT id, status FROM identity_verifications WHERE user_id = ? '
            'ORDER BY id DESC LIMIT 1',
            (g.user_id,)
        ).fetchone()
        if existing and existing['status'] in ('pending', 'verified'):
            return jsonify({
                'error': f'Verification already {existing["status"]}. '
                         'Cannot submit a new one.'
            }), 409

        cur = conn.execute(
            'INSERT INTO identity_verifications '
            '(user_id, document_type, document_data_enc, status, submitted_at) '
            'VALUES (?, ?, ?, "pending", ?)',
            (g.user_id, doc_type, enc, now)
        )
        vid = cur.lastrowid
        write_audit_log(conn, 'VERIFICATION_SUBMITTED', user_id=g.user_id,
                        entity_type='identity_verification', entity_id=vid,
                        details={'document_type': doc_type})

    return jsonify({'message': 'Verification submitted.', 'verification_id': vid}), 201


@verification_bp.route('/status', methods=['GET'])
@login_required
def get_status():
    with db() as conn:
        row = row_to_dict(conn.execute(
            'SELECT id, document_type, status, submitted_at, reviewed_at, notes '
            'FROM identity_verifications WHERE user_id = ? ORDER BY id DESC LIMIT 1',
            (g.user_id,)
        ).fetchone())

    if not row:
        return jsonify({'status': 'not_submitted'}), 200

    row['document_type'] = mask_document(row['document_type'])
    return jsonify({'verification': row}), 200


@verification_bp.route('', methods=['GET'])
@admin_required
def list_verifications():
    status_filter = request.args.get('status', 'pending')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))
    offset = (page - 1) * per_page

    with db() as conn:
        if status_filter == 'all':
            rows = rows_to_list(conn.execute(
                'SELECT v.id, v.user_id, u.username, v.document_type, v.status, '
                'v.submitted_at, v.reviewed_at, v.notes '
                'FROM identity_verifications v JOIN users u ON v.user_id = u.id '
                'ORDER BY v.id DESC LIMIT ? OFFSET ?',
                (per_page, offset)
            ).fetchall())
            total = conn.execute('SELECT COUNT(*) FROM identity_verifications').fetchone()[0]
        else:
            rows = rows_to_list(conn.execute(
                'SELECT v.id, v.user_id, u.username, v.document_type, v.status, '
                'v.submitted_at, v.reviewed_at, v.notes '
                'FROM identity_verifications v JOIN users u ON v.user_id = u.id '
                'WHERE v.status = ? ORDER BY v.id DESC LIMIT ? OFFSET ?',
                (status_filter, per_page, offset)
            ).fetchall())
            total = conn.execute(
                'SELECT COUNT(*) FROM identity_verifications WHERE status = ?',
                (status_filter,)
            ).fetchone()[0]

    # Never expose encrypted data; mask doc type
    for r in rows:
        r['document_type'] = mask_document(r['document_type'])

    return jsonify({'verifications': rows, 'total': total}), 200


@verification_bp.route('/<int:vid>/review', methods=['PUT'])
@admin_required
def review_verification(vid: int):
    data = request.get_json(force=True) or {}
    decision = data.get('decision')  # 'verified' or 'rejected'
    notes = (data.get('notes') or '').strip()

    if decision not in ('verified', 'rejected'):
        return jsonify({'error': 'decision must be "verified" or "rejected".'}), 400

    now = utcnow()
    with db() as conn:
        row = row_to_dict(conn.execute(
            'SELECT * FROM identity_verifications WHERE id = ?', (vid,)
        ).fetchone())
        if not row:
            return jsonify({'error': 'Verification not found.'}), 404
        if row['status'] != 'pending':
            return jsonify({'error': 'Only pending verifications can be reviewed.'}), 409

        conn.execute(
            'UPDATE identity_verifications SET status = ?, reviewed_at = ?, '
            'reviewer_id = ?, notes = ? WHERE id = ?',
            (decision, now, g.user_id, notes, vid)
        )
        write_audit_log(conn, f'VERIFICATION_{decision.upper()}',
                        user_id=g.user_id,
                        entity_type='identity_verification', entity_id=vid,
                        details={'target_user_id': row['user_id'], 'decision': decision})

    return jsonify({'message': f'Verification {decision}.'}), 200
