"""
Offline Payment DAL.

All writes go through this module.
Statuses:  pending → confirmed → refunded
           pending → failed
"""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def create(conn, user_id: int, amount: float, payment_type: str,
           reference_number: str, signature: str,
           notes: str = None) -> int:
    """Insert a new pending payment and return its id."""
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO offline_payments '
        '(user_id, amount, payment_type, reference_number, signature, '
        ' notes, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, amount, payment_type, reference_number, signature,
         notes, now, now)
    )
    return cur.lastrowid


def get_by_id(conn, payment_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT * FROM offline_payments WHERE id = ?', (payment_id,)
    ).fetchone())


def list_payments(conn, user_id: int = None, status: str = None,
                  payment_type: str = None,
                  limit: int = 50, offset: int = 0) -> tuple[list, int]:
    query = 'SELECT * FROM offline_payments WHERE 1=1'
    params = []
    if user_id:
        query += ' AND user_id = ?'
        params.append(int(user_id))
    if status:
        query += ' AND status = ?'
        params.append(status)
    if payment_type:
        query += ' AND payment_type = ?'
        params.append(payment_type)

    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


def set_confirmed(conn, payment_id: int, admin_id: int,
                  ledger_entry_id: int) -> None:
    conn.execute(
        'UPDATE offline_payments '
        'SET status = ?, confirmed_by = ?, confirmed_at = ?, '
        '    ledger_entry_id = ?, updated_at = ? '
        'WHERE id = ?',
        ('confirmed', admin_id, utcnow(), ledger_entry_id, utcnow(), payment_id)
    )


def set_failed(conn, payment_id: int) -> None:
    conn.execute(
        'UPDATE offline_payments SET status = ?, updated_at = ? WHERE id = ?',
        ('failed', utcnow(), payment_id)
    )


def set_refunded(conn, payment_id: int, refund_entry_id: int) -> None:
    conn.execute(
        'UPDATE offline_payments '
        'SET status = ?, refund_entry_id = ?, updated_at = ? '
        'WHERE id = ?',
        ('refunded', refund_entry_id, utcnow(), payment_id)
    )
