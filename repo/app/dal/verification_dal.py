"""Identity Verification DAL."""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow

# Columns safe to return to any authenticated caller — ciphertext excluded.
_SAFE_COLS = (
    'v.id, v.user_id, v.document_type, v.document_fingerprint, '
    'v.content_type, v.file_size_bytes, v.status, '
    'v.submitted_at, v.reviewed_at, v.notes'
)


def get_latest_for_user(conn, user_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        f'SELECT {_SAFE_COLS} '
        'FROM identity_verifications v '
        'WHERE v.user_id = ? ORDER BY v.id DESC LIMIT 1',
        (user_id,)
    ).fetchone())


def get_by_id(conn, vid: int) -> dict | None:
    """Return record without ciphertext (safe for display / review)."""
    return row_to_dict(conn.execute(
        f'SELECT {_SAFE_COLS} '
        'FROM identity_verifications v WHERE v.id = ?',
        (vid,)
    ).fetchone())


def get_encrypted_document(conn, vid: int) -> dict | None:
    """
    Return ciphertext + metadata for admin decrypt only.
    Deliberately kept separate from get_by_id so the ciphertext
    is never accidentally included in listing queries.
    """
    return row_to_dict(conn.execute(
        'SELECT id, user_id, document_type, document_data_enc, '
        'document_fingerprint, content_type, file_size_bytes '
        'FROM identity_verifications WHERE id = ?',
        (vid,)
    ).fetchone())


def create(conn, user_id: int, document_type: str,
           document_data_enc: str, document_fingerprint: str,
           content_type: str, file_size_bytes: int) -> int:
    cur = conn.execute(
        'INSERT INTO identity_verifications '
        '(user_id, document_type, document_data_enc, document_fingerprint, '
        ' content_type, file_size_bytes, status, submitted_at) '
        'VALUES (?, ?, ?, ?, ?, ?, "pending", ?)',
        (user_id, document_type, document_data_enc, document_fingerprint,
         content_type, file_size_bytes, utcnow())
    )
    return cur.lastrowid


def update_review(conn, vid: int, decision: str,
                  reviewer_id: int, notes: str) -> None:
    conn.execute(
        'UPDATE identity_verifications SET status = ?, reviewed_at = ?, '
        'reviewer_id = ?, notes = ? WHERE id = ?',
        (decision, utcnow(), reviewer_id, notes, vid)
    )


def list_verifications(conn, status: str = None,
                       limit: int = 20, offset: int = 0) -> tuple[list, int]:
    base = (
        'FROM identity_verifications v JOIN users u ON v.user_id = u.id '
        'WHERE 1=1'
    )
    params = []
    if status and status != 'all':
        base += ' AND v.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) {base}', params).fetchone()[0]
    rows = rows_to_list(conn.execute(
        f'SELECT {_SAFE_COLS}, u.username {base} '
        'ORDER BY v.id DESC LIMIT ? OFFSET ?',
        params + [limit, offset]
    ).fetchall())
    return rows, total
