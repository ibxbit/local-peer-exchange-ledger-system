"""
Ledger DAL — INSERT ONLY.
Never issues UPDATE or DELETE on ledger_entries.
"""

from app.models import row_to_dict, rows_to_list
from app.utils import utcnow, hash_ledger_entry


def get_last_hash(conn) -> str | None:
    row = conn.execute(
        'SELECT entry_hash FROM ledger_entries ORDER BY id DESC LIMIT 1'
    ).fetchone()
    return row['entry_hash'] if row else None


def insert_entry(conn, user_id: int, transaction_type: str,
                 amount: float, balance_after: float,
                 created_by: int, description: str = None,
                 reference_id: int = None, reference_type: str = None,
                 idempotency_key: str = None) -> str:
    """
    Insert a ledger entry and return the new entry_hash.
    Raises on any constraint violation (duplicate idempotency_key, etc.).
    """
    now = utcnow()
    previous_hash = get_last_hash(conn)
    entry_data = {
        'user_id': user_id,
        'transaction_type': transaction_type,
        'amount': amount,
        'balance_after': balance_after,
        'created_at': now,
        'created_by': created_by,
        'description': description or '',
    }
    entry_hash = hash_ledger_entry(entry_data, previous_hash)

    conn.execute(
        'INSERT INTO ledger_entries '
        '(entry_hash, previous_hash, user_id, transaction_type, amount, '
        'balance_after, reference_id, reference_type, description, '
        'created_at, created_by, idempotency_key) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (entry_hash, previous_hash, user_id, transaction_type, amount,
         balance_after, reference_id, reference_type, description,
         now, created_by, idempotency_key)
    )
    return entry_hash


def list_entries(conn, user_id: int = None,
                 limit: int = 20, offset: int = 0,
                 privileged: bool = False) -> tuple[list, int]:
    if privileged:
        query = (
            'SELECT l.id, l.user_id, u.username, l.transaction_type, '
            'l.amount, l.balance_after, l.description, l.created_at, '
            'l.entry_hash, l.reference_type, l.reference_id '
            'FROM ledger_entries l JOIN users u ON l.user_id = u.id '
            'WHERE 1=1'
        )
        params = []
        if user_id:
            query += ' AND l.user_id = ?'
            params.append(user_id)
    else:
        query = (
            'SELECT id, transaction_type, amount, balance_after, '
            'description, created_at, reference_type, reference_id '
            'FROM ledger_entries WHERE user_id = ?'
        )
        params = [user_id]

    total = conn.execute(f'SELECT COUNT(*) FROM ({query})', params).fetchone()[0]
    query += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    rows = rows_to_list(conn.execute(query, params + [limit, offset]).fetchall())
    return rows, total


def get_all_ordered(conn) -> list:
    """Return all entries ordered by id ASC (for chain verification)."""
    return rows_to_list(conn.execute(
        'SELECT * FROM ledger_entries ORDER BY id ASC'
    ).fetchall())
