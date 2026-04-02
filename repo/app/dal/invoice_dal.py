"""
Invoice DAL — pure SQL wrappers, no business logic.
Invoices are immutable once issued; only status fields and amount_paid are updated.
"""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow


def _next_invoice_number(conn) -> str:
    count = conn.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]
    return f'INV-{count + 1:06d}'


def create(conn, issuer_id: int, payer_id: int, amount: float,
           due_date: str, notes: str = None,
           session_id: int = None) -> dict:
    """Insert a draft invoice and return it as a dict."""
    now = utcnow()
    invoice_number = _next_invoice_number(conn)
    conn.execute(
        'INSERT INTO invoices '
        '(invoice_number, issuer_id, payer_id, session_id, amount, '
        ' amount_paid, status, due_date, notes, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, 0.0, "draft", ?, ?, ?, ?)',
        (invoice_number, issuer_id, payer_id, session_id,
         amount, due_date, notes, now, now)
    )
    return get_by_number(conn, invoice_number)


def get_by_id(conn, invoice_id: int) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT i.*, '
        'u1.username as issuer_name, u2.username as payer_name '
        'FROM invoices i '
        'JOIN users u1 ON i.issuer_id = u1.id '
        'JOIN users u2 ON i.payer_id  = u2.id '
        'WHERE i.id = ?',
        (invoice_id,)
    ).fetchone())


def get_by_number(conn, invoice_number: str) -> dict | None:
    return row_to_dict(conn.execute(
        'SELECT i.*, '
        'u1.username as issuer_name, u2.username as payer_name '
        'FROM invoices i '
        'JOIN users u1 ON i.issuer_id = u1.id '
        'JOIN users u2 ON i.payer_id  = u2.id '
        'WHERE i.invoice_number = ?',
        (invoice_number,)
    ).fetchone())


def issue(conn, invoice_id: int, issued_at: str) -> None:
    conn.execute(
        "UPDATE invoices SET status = 'issued', issued_at = ?, updated_at = ? "
        'WHERE id = ?',
        (issued_at, utcnow(), invoice_id)
    )


def set_paid(conn, invoice_id: int, amount_paid: float,
             paid_at: str) -> None:
    conn.execute(
        "UPDATE invoices SET status = 'paid', amount_paid = ?, "
        'paid_at = ?, updated_at = ? WHERE id = ?',
        (amount_paid, paid_at, utcnow(), invoice_id)
    )


def set_refunded(conn, invoice_id: int, amount_paid: float) -> None:
    status = 'refunded' if amount_paid <= 0 else 'paid'
    conn.execute(
        'UPDATE invoices SET status = ?, amount_paid = ?, updated_at = ? '
        'WHERE id = ?',
        (status, amount_paid, utcnow(), invoice_id)
    )


def void(conn, invoice_id: int) -> None:
    conn.execute(
        "UPDATE invoices SET status = 'voided', voided_at = ?, updated_at = ? "
        'WHERE id = ?',
        (utcnow(), utcnow(), invoice_id)
    )


def mark_overdue(conn) -> int:
    """
    Mark issued invoices whose due_date has passed midnight UTC as overdue.
    Overdue condition: due_date < today (UTC date string, YYYY-MM-DD).
    Returns the number of rows updated.
    """
    now = utcnow()
    # SQLite date('now') gives today's UTC date string; comparisons work lexicographically.
    cur = conn.execute(
        "UPDATE invoices SET status = 'overdue', updated_at = ? "
        "WHERE status = 'issued' AND due_date < date('now')",
        (now,)
    )
    return cur.rowcount


def list_invoices(conn, user_id: int = None, role: str = 'any',
                  status: str = None, limit: int = 20,
                  offset: int = 0) -> tuple[list, int]:
    query = (
        'SELECT i.id, i.invoice_number, i.amount, i.amount_paid, i.status, '
        'i.due_date, i.issued_at, i.paid_at, i.notes, i.created_at, '
        'u1.username as issuer_name, u2.username as payer_name '
        'FROM invoices i '
        'JOIN users u1 ON i.issuer_id = u1.id '
        'JOIN users u2 ON i.payer_id  = u2.id '
        'WHERE 1=1'
    )
    params = []
    if user_id:
        if role == 'issuer':
            query += ' AND i.issuer_id = ?'
            params.append(user_id)
        elif role == 'payer':
            query += ' AND i.payer_id = ?'
            params.append(user_id)
        else:
            query += ' AND (i.issuer_id = ? OR i.payer_id = ?)'
            params += [user_id, user_id]
    if status:
        query += ' AND i.status = ?'
        params.append(status)
    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY i.id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total
