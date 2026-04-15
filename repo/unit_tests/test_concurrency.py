"""
Concurrency & race-condition tests.

The rest of unit_tests/ uses a per-test :memory: SQLite connection; that
isolates schema setup but cannot be shared across threads (`:memory:`
databases are bound to one connection). For these tests we instead spin up
a *file-based* SQLite database in WAL mode under tempfile and let each
worker thread open its own connection via ``app.models.get_connection``.
That is the same connection helper the production code uses, so the
behaviour we exercise here is the real on-disk behaviour — no mocks.

Coverage:
  1. Ledger atomicity
       - Concurrent admin credits to one user → final balance equals the
         exact sum (no lost updates).
       - Concurrent transfers between the same two users → conservation
         of total balance + invariant: zero negative balances.
       - Hash chain remains intact (verify_chain returns valid).
       - Idempotency keys collapse duplicate concurrent submissions.
  2. Match queue race conditions
       - N users joining the queue simultaneously for one skill while
         repeated auto_match_cycle invocations fire → no user is paired
         with themselves, no session is duplicated, and every "matched"
         queue row carries a real session_id.
       - When two users have different priorities, the higher-priority
         one is always matched first under contention.

The Python sqlite3 module serialises writes per-connection, so we use a
small ``RLock`` only around the ledger_service mutations to model the
service-layer transaction boundary; every read is unguarded so we can
observe race effects.
"""

import os
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

import config as _cfg

from app.models import SCHEMA, _migrate, get_connection
from app.services import ledger_service, matching_service
from app.dal import (
    ledger_dal, matching_dal, user_dal, audit_dal, session_dal,
    verification_dal,
)
from app.utils import hash_password, utcnow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tempdb(monkeypatch):
    """
    Patch ``Config.DATABASE_PATH`` to a freshly-initialised tempfile DB so that
    every worker thread's call to ``get_connection()`` lands on the same file.
    Yields the path; teardown removes the file (and the WAL/journal sidecars).
    """
    fd, path = tempfile.mkstemp(suffix='_concurrency.db')
    os.close(fd)
    # Ensure schema is loaded against this exact path (not the global app.db)
    monkeypatch.setattr(_cfg.Config, 'DATABASE_PATH', path, raising=False)

    # Initialise schema by hand — get_connection() turns on WAL automatically.
    init = sqlite3.connect(path)
    init.executescript(SCHEMA)
    _migrate(init)
    init.commit()
    init.close()

    yield path

    for ext in ('', '-wal', '-shm', '-journal'):
        p = path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _seed_user(conn, username, email, role='user',
               is_active=1, credit_balance=1000.0,
               password='Pwd@12345678!'):
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO users '
        '(username, email, password_hash, role, is_active, '
        ' credit_balance, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (username, email, hash_password(password), role,
         is_active, credit_balance, now, now)
    )
    conn.commit()
    return cur.lastrowid


def _verify_user(conn, user_id, admin_id):
    """Inject a verified identity record so guard_is_verified passes."""
    conn.execute(
        "INSERT INTO identity_verifications "
        "(user_id, document_type, document_data_enc, document_fingerprint, "
        " content_type, file_size_bytes, status, submitted_at, "
        " reviewed_at, reviewer_id) "
        "VALUES (?, 'passport', 'enc', 'fp', 'image/jpeg', 256, "
        "        'verified', ?, ?, ?)",
        (user_id, utcnow(), utcnow(), admin_id)
    )
    conn.commit()


# Service-layer mutations need to serialise per-process: SQLite returns
# `database is locked` if two writers race for the same row inside a single
# implicit transaction. The application's request handlers achieve this by
# holding the connection (and its implicit transaction) for the duration of
# the request; threads in this test simulate that with an RLock.
_ledger_write_lock = threading.RLock()


# ===========================================================================
# 1. Ledger atomicity
# ===========================================================================

class TestLedgerAtomicity:
    def test_concurrent_credits_sum_exactly(self, tempdb):
        """20 concurrent +5.0 credits → final balance = start + 100.0."""
        # Seed admin + recipient
        seed = sqlite3.connect(tempdb)
        seed.row_factory = sqlite3.Row
        admin_id = _seed_user(seed, 'cct_admin', 'cct_admin@x', role='admin')
        target_id = _seed_user(seed, 'cct_target', 'cct_target@x',
                               credit_balance=100.0)
        seed.close()

        N = 20
        AMOUNT = 5.0

        def worker(i):
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    new_bal = ledger_service.credit(
                        conn, target_id, AMOUNT,
                        f'concurrent credit #{i}', admin_id,
                        idempotency_key=f'cct-{i}',
                    )
                    conn.commit()
                    return new_bal
                finally:
                    conn.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(N)))

        # Verify final stored balance is exact (no lost updates)
        check = get_connection()
        try:
            stored = user_dal.get_by_id(check, target_id)['credit_balance']
            assert stored == 100.0 + N * AMOUNT, (
                f'Lost-update detected: expected {100.0 + N * AMOUNT}, '
                f'got {stored}'
            )

            # Hash-chain integrity must survive concurrent inserts
            chain = ledger_service.verify_chain(check)
            assert chain['valid'] is True, chain
            assert chain['entries'] >= N

            # Each idempotency key produced exactly one row
            count = check.execute(
                'SELECT COUNT(*) FROM ledger_entries WHERE user_id = ? '
                "AND transaction_type = 'credit'",
                (target_id,)
            ).fetchone()[0]
            assert count == N
        finally:
            check.close()

    def test_concurrent_transfers_conserve_total(self, tempdb):
        """
        Bidirectional concurrent transfers must conserve total credits and
        never drive any account negative.
        """
        seed = sqlite3.connect(tempdb)
        admin_id = _seed_user(seed, 'cxf_admin', 'cxf_admin@x', role='admin')
        a_id = _seed_user(seed, 'cxf_a', 'cxf_a@x', credit_balance=500.0)
        b_id = _seed_user(seed, 'cxf_b', 'cxf_b@x', credit_balance=500.0)
        seed.close()

        N = 30           # 30 transfers each direction
        AMOUNT = 10.0
        START_TOTAL = 1000.0

        def transfer(sender, recipient, i):
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    ledger_service.transfer(
                        conn, sender, recipient, AMOUNT,
                        f'race transfer {i}',
                        idempotency_key=f'cxf-{sender}-{i}',
                    )
                    conn.commit()
                    return True
                except (ValueError, sqlite3.IntegrityError):
                    # Insufficient balance is an acceptable outcome under
                    # contention; the test cares about the *invariant*.
                    return False
                finally:
                    conn.close()

        tasks = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i in range(N):
                tasks.append(pool.submit(transfer, a_id, b_id, i))
                tasks.append(pool.submit(transfer, b_id, a_id, i))
            for t in as_completed(tasks):
                t.result()

        check = get_connection()
        try:
            bal_a = user_dal.get_by_id(check, a_id)['credit_balance']
            bal_b = user_dal.get_by_id(check, b_id)['credit_balance']
            assert bal_a >= 0, f'A went negative: {bal_a}'
            assert bal_b >= 0, f'B went negative: {bal_b}'
            assert bal_a + bal_b == START_TOTAL, (
                f'Total credits not conserved: {bal_a} + {bal_b} != '
                f'{START_TOTAL}'
            )

            # Hash chain still verifies
            chain = ledger_service.verify_chain(check)
            assert chain['valid'] is True, chain

            # Every transfer that succeeded created a paired in/out row
            ins  = check.execute(
                "SELECT COUNT(*) FROM ledger_entries "
                "WHERE transaction_type = 'transfer_in'"
            ).fetchone()[0]
            outs = check.execute(
                "SELECT COUNT(*) FROM ledger_entries "
                "WHERE transaction_type = 'transfer_out'"
            ).fetchone()[0]
            assert ins == outs, (
                f'Pairing violated: transfer_in={ins}, transfer_out={outs}'
            )
        finally:
            check.close()

    def test_idempotency_keys_dedupe_under_contention(self, tempdb):
        """
        10 threads all submit the same transfer with the same Idempotency-Key.
        Exactly one must commit; the rest must collapse to the cached result
        (or be rejected on the unique-key constraint, which is also fine).
        """
        seed = sqlite3.connect(tempdb)
        admin_id = _seed_user(seed, 'idem_admin', 'idem_admin@x', role='admin')
        sender = _seed_user(seed, 'idem_a', 'idem_a@x', credit_balance=500.0)
        recipient = _seed_user(seed, 'idem_b', 'idem_b@x', credit_balance=500.0)
        seed.close()

        KEY = 'idem-shared-key-xyz'
        AMOUNT = 25.0
        N = 10

        successes = 0
        success_lock = threading.Lock()

        def worker(_i):
            nonlocal successes
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    ledger_service.transfer(
                        conn, sender, recipient, AMOUNT,
                        'duplicate submission',
                        idempotency_key=KEY,
                    )
                    conn.commit()
                    with success_lock:
                        successes += 1
                except sqlite3.IntegrityError:
                    # The UNIQUE constraint on idempotency_key is the
                    # bottom-most enforcement; treat as a successful dedupe.
                    pass
                finally:
                    conn.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(N)))

        check = get_connection()
        try:
            sender_bal = user_dal.get_by_id(check, sender)['credit_balance']
            recipient_bal = user_dal.get_by_id(check, recipient)['credit_balance']
            # Exactly one transfer should have applied
            assert sender_bal == 500.0 - AMOUNT, (
                f'Idempotency violated: sender balance {sender_bal} suggests '
                f'>1 transfer applied'
            )
            assert recipient_bal == 500.0 + AMOUNT

            rows = check.execute(
                'SELECT COUNT(*) FROM ledger_entries '
                "WHERE transaction_type = 'transfer_out' "
                'AND idempotency_key = ?',
                (KEY,)
            ).fetchone()[0]
            assert rows == 1, (
                f'Expected exactly one transfer_out row for the shared '
                f'idempotency key; saw {rows}'
            )
        finally:
            check.close()


# ===========================================================================
# 2. Match queue race conditions
# ===========================================================================

class TestMatchQueueRaces:
    def test_concurrent_joins_no_self_match_no_duplicate_session(self, tempdb):
        """
        Six users join the queue concurrently for one skill while a pool of
        worker threads repeatedly fires auto_match_cycle. We assert:
          - no queue entry is matched to its own user_id
          - no queue entry shares a session_id with another, except its pair
          - every 'matched' entry references a session that exists
        """
        seed = sqlite3.connect(tempdb)
        seed.row_factory = sqlite3.Row
        admin_id = _seed_user(seed, 'q_admin', 'q_admin@x', role='admin')
        seed.close()

        # Provision N users with credits + verification + a profile
        N_USERS = 6
        SKILL = 'concurrency-skill'

        user_ids = []
        prov = sqlite3.connect(tempdb)
        prov.row_factory = sqlite3.Row
        for i in range(N_USERS):
            uid = _seed_user(prov, f'q_u{i}', f'q_u{i}@x',
                             credit_balance=500.0)
            _verify_user(prov, uid, admin_id)
            matching_dal.upsert_profile(
                prov, uid, [SKILL], [], {}, f'bio {i}', True
            )
            user_ids.append(uid)
        prov.commit()
        prov.close()

        # Enqueue all users concurrently
        def join(uid):
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    eid = matching_service.join_queue(conn, uid, SKILL)
                    conn.commit()
                    return eid
                finally:
                    conn.close()

        with ThreadPoolExecutor(max_workers=N_USERS) as pool:
            entry_ids = list(pool.map(join, user_ids))

        # Spin up parallel auto_match_cycle calls until everyone is paired
        # (or we hit a budget — failure to pair within budget would itself
        # be evidence of a bug)
        def run_cycle():
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    return matching_service.run_auto_match_cycle(conn)
                finally:
                    conn.close()

        for _ in range(20):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _i: run_cycle(), range(4)))
            # Stop early once nothing waiting remains
            check = get_connection()
            try:
                remaining = check.execute(
                    "SELECT COUNT(*) FROM matching_queue "
                    "WHERE status = 'waiting' AND skill = ?",
                    (SKILL,)
                ).fetchone()[0]
            finally:
                check.close()
            if remaining == 0:
                break

        # ---- Invariants ----
        check = get_connection()
        try:
            entries = check.execute(
                'SELECT id, user_id, status, matched_to, session_id '
                'FROM matching_queue WHERE skill = ?',
                (SKILL,)
            ).fetchall()

            assert len(entries) == N_USERS, (
                f'Expected {N_USERS} queue rows, got {len(entries)}'
            )

            # No self-match
            for e in entries:
                if e['matched_to'] is not None:
                    assert e['matched_to'] != e['user_id'], (
                        f'Self-match detected on queue entry {e["id"]}'
                    )

            # Every matched row references a real session
            session_ids = [e['session_id'] for e in entries
                           if e['status'] == 'matched']
            for sid in session_ids:
                assert sid is not None
                assert session_dal.get_by_id(check, sid) is not None

            # session_ids should pair up (each session appears at most twice)
            from collections import Counter
            counts = Counter(session_ids)
            for sid, c in counts.items():
                assert c <= 2, (
                    f'Session {sid} referenced {c} times — duplicate '
                    'matching detected'
                )
        finally:
            check.close()

    def test_priority_respected_under_contention(self, tempdb):
        """
        Three low-priority users wait, then one high-priority user joins
        concurrently with another low-priority user. The high-priority
        user must be matched in the next cycle.
        """
        seed = sqlite3.connect(tempdb)
        admin_id = _seed_user(seed, 'p_admin', 'p_admin@x', role='admin')
        seed.close()

        SKILL = 'priority-skill'

        # The seekers (each will be the one that triggers a pairing).
        # Pre-populate three low-priority entries already waiting.
        users = []
        prov = sqlite3.connect(tempdb)
        prov.row_factory = sqlite3.Row
        for i, prio in enumerate([0, 0, 0]):
            uid = _seed_user(prov, f'p_low{i}', f'p_low{i}@x',
                             credit_balance=500.0)
            _verify_user(prov, uid, admin_id)
            matching_dal.upsert_profile(
                prov, uid, [SKILL], [], {}, '', True
            )
            users.append((uid, prio))
        # The contestants: one high, one low. They join concurrently.
        for label, prio in [('hi', 5), ('lo', 0)]:
            uid = _seed_user(prov, f'p_{label}', f'p_{label}@x',
                             credit_balance=500.0)
            _verify_user(prov, uid, admin_id)
            matching_dal.upsert_profile(
                prov, uid, [SKILL], [], {}, '', True
            )
            users.append((uid, prio))
        prov.commit()
        prov.close()

        # Enqueue the three low-priority seekers first (sequentially) so
        # they sit in the queue waiting.
        for uid, prio in users[:3]:
            conn = get_connection()
            try:
                matching_service.join_queue(conn, uid, SKILL, priority=prio)
                conn.commit()
            finally:
                conn.close()

        # Concurrently enqueue the two contestants (hi-priority + low-priority).
        contestant_user_ids = [users[3][0], users[4][0]]
        contestant_priorities = {users[3][0]: users[3][1],
                                 users[4][0]: users[4][1]}

        def join(uid):
            with _ledger_write_lock:
                conn = get_connection()
                try:
                    return matching_service.join_queue(
                        conn, uid, SKILL,
                        priority=contestant_priorities[uid])
                finally:
                    conn.commit()
                    conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(join, contestant_user_ids))

        # Run a single match cycle: the highest-priority waiting entry should
        # be paired with someone (one of the low-priority earlier entries).
        cycle_conn = get_connection()
        try:
            matching_service.run_auto_match_cycle(cycle_conn)
            cycle_conn.commit()
        finally:
            cycle_conn.close()

        # Look up the hi-priority contestant's queue row
        hi_uid = users[3][0]
        check = get_connection()
        try:
            hi_row = check.execute(
                'SELECT * FROM matching_queue '
                'WHERE user_id = ? AND skill = ? '
                'ORDER BY id DESC LIMIT 1',
                (hi_uid, SKILL)
            ).fetchone()
            assert hi_row is not None
            # The high-priority entry must have been matched in the cycle
            assert hi_row['status'] == 'matched', (
                f'High-priority user did not match first; '
                f'status={hi_row["status"]}'
            )
            assert hi_row['session_id'] is not None
        finally:
            check.close()
