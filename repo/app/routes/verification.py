"""
Identity verification routes.

Storage flow:
  1. User uploads file via multipart/form-data (field: 'document').
  2. Magic bytes are checked — JPEG/PNG/PDF only; max 5 MB; no OCR.
  3. SHA-256 fingerprint computed from raw bytes.
  4. Raw bytes encrypted with AES-256-GCM; ciphertext stored in DB.
  5. Fingerprint, content_type, file_size_bytes stored alongside ciphertext.

Access control:
  - Users: submit their own doc, view own masked status.
  - Admins: list queue (masked), review, decrypt individual document
            (GET /<id>/document) — every access audit-logged.
  - Ciphertext never leaves the server in list/status/review responses.
"""

import base64

from flask import Blueprint, request, jsonify, g, Response
from app.models import db
from app.utils import (
    login_required, admin_required,
    encrypt_bytes, decrypt_bytes,
    sha256_bytes, validate_document_upload,
    mask_document,
)
from app.dal import audit_dal, verification_dal

verification_bp = Blueprint('verification', __name__)

ALLOWED_DOC_TYPES = ('passport', 'national_id', 'drivers_license', 'utility_bill')


# ---------------------------------------------------------------------------
# User-facing
# ---------------------------------------------------------------------------

@verification_bp.route('/submit', methods=['POST'])
@login_required
def submit():
    """
    Accept multipart/form-data:
      - document_type  (form field)
      - document        (file field)
    Validates by magic bytes; stores AES-256-GCM ciphertext + SHA-256 fingerprint.
    """
    doc_type = (request.form.get('document_type') or '').strip().lower()
    if doc_type not in ALLOWED_DOC_TYPES:
        return jsonify({
            'error': f'document_type must be one of: {", ".join(ALLOWED_DOC_TYPES)}.'
        }), 400

    uploaded = request.files.get('document')
    if not uploaded:
        return jsonify({'error': "'document' file field is required."}), 400

    raw = uploaded.read()

    ok, err, detected_mime = validate_document_upload(raw)
    if not ok:
        return jsonify({'error': err}), 400

    fingerprint = sha256_bytes(raw)
    ciphertext  = encrypt_bytes(raw)

    with db() as conn:
        existing = verification_dal.get_latest_for_user(conn, g.user_id)
        if existing and existing['status'] in ('pending', 'verified'):
            return jsonify({
                'error': f'Verification already {existing["status"]}.'
            }), 409

        vid = verification_dal.create(
            conn, g.user_id, doc_type, ciphertext,
            fingerprint, detected_mime, len(raw),
        )
        audit_dal.write(conn, 'VERIFICATION_SUBMITTED', user_id=g.user_id,
                        entity_type='identity_verification', entity_id=vid,
                        details={'document_type': doc_type,
                                 'content_type': detected_mime,
                                 'file_size_bytes': len(raw),
                                 'fingerprint': fingerprint})

    return jsonify({
        'message': 'Verification submitted.',
        'verification_id': vid,
        'fingerprint': fingerprint,
    }), 201


@verification_bp.route('/status', methods=['GET'])
@login_required
def status():
    """User views their own verification status — no ciphertext, doc type masked."""
    with db() as conn:
        row = verification_dal.get_latest_for_user(conn, g.user_id)
    if not row:
        return jsonify({'status': 'not_submitted'}), 200

    row['document_type'] = mask_document(row['document_type'])
    return jsonify({'verification': row}), 200


# ---------------------------------------------------------------------------
# Admin-facing
# ---------------------------------------------------------------------------

@verification_bp.route('', methods=['GET'])
@admin_required
def list_verifications():
    """
    Admin: list verification queue.
    Document type is masked; ciphertext is never included.
    Access is audit-logged.
    """
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 20)))

    with db() as conn:
        rows, total = verification_dal.list_verifications(
            conn, status=request.args.get('status', 'pending'),
            limit=per_page, offset=(page - 1) * per_page,
        )
        audit_dal.write(conn, 'VERIFICATION_LIST_ACCESSED', user_id=g.user_id,
                        entity_type='identity_verification',
                        details={'status_filter': request.args.get('status', 'pending'),
                                 'result_count': len(rows)})

    for r in rows:
        r['document_type'] = mask_document(r['document_type'])

    return jsonify({'verifications': rows, 'total': total}), 200


@verification_bp.route('/<int:vid>/review', methods=['PUT'])
@admin_required
def review(vid):
    """Admin approves or rejects a pending verification."""
    d = request.get_json(force=True) or {}
    decision = d.get('decision')
    if decision not in ('verified', 'rejected'):
        return jsonify({'error': 'decision must be "verified" or "rejected".'}), 400

    with db() as conn:
        row = verification_dal.get_by_id(conn, vid)
        if not row:
            return jsonify({'error': 'Verification not found.'}), 404
        if row['status'] != 'pending':
            return jsonify({'error': 'Only pending verifications can be reviewed.'}), 409

        verification_dal.update_review(conn, vid, decision, g.user_id,
                                       (d.get('notes') or '').strip())
        audit_dal.write(conn, f'VERIFICATION_{decision.upper()}',
                        user_id=g.user_id,
                        entity_type='identity_verification', entity_id=vid,
                        details={'target_user_id': row['user_id'],
                                 'decision': decision})

    return jsonify({'message': f'Verification {decision}.'}), 200


@verification_bp.route('/<int:vid>/document', methods=['GET'])
@admin_required
def get_document(vid):
    """
    Admin-only: decrypt and return the raw document for manual review.

    Every call is audit-logged regardless of outcome.
    The decrypted bytes are returned as the original file (Content-Type preserved)
    so the admin UI can display the image or PDF inline.
    Ciphertext is never exposed in the response — only the decrypted payload.
    """
    with db() as conn:
        rec = verification_dal.get_encrypted_document(conn, vid)
        # Log the access attempt before returning anything
        audit_dal.write(conn, 'VERIFICATION_DOCUMENT_ACCESSED', user_id=g.user_id,
                        entity_type='identity_verification', entity_id=vid,
                        details={
                            'found': rec is not None,
                            'target_user_id': rec['user_id'] if rec else None,
                        })

    if not rec:
        return jsonify({'error': 'Verification not found.'}), 404

    try:
        raw = decrypt_bytes(rec['document_data_enc'])
    except Exception:
        return jsonify({'error': 'Document could not be decrypted.'}), 500

    # Integrity check: re-derive fingerprint and compare
    expected_fp = sha256_bytes(raw)
    stored_fp   = rec.get('document_fingerprint')
    if stored_fp and expected_fp != stored_fp:
        # Fingerprint mismatch — ciphertext may have been tampered with
        with db() as conn:
            audit_dal.write(conn, 'VERIFICATION_FINGERPRINT_MISMATCH',
                            user_id=g.user_id,
                            entity_type='identity_verification', entity_id=vid,
                            details={'expected': expected_fp, 'stored': stored_fp})
        return jsonify({'error': 'Document integrity check failed.'}), 500

    mime = rec.get('content_type') or 'application/octet-stream'
    return Response(raw, status=200, mimetype=mime,
                    headers={'Content-Disposition': 'inline'})
