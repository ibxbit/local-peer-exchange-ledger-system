"""
Shared fixtures for unit tests.
Each test gets an isolated in-memory SQLite database with the full schema applied.
No Flask application context is required; services are called directly.
"""

import sys
import os

# Ensure repo root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pytest

from app.models import SCHEMA, _migrate
from app.utils import hash_password, utcnow


@pytest.fixture
def conn():
    """In-memory SQLite connection with the full schema loaded."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    _migrate(c)
    c.commit()
    yield c
    c.close()


def _insert_user(conn, username, email, password, role='user',
                 is_active=1, credit_balance=200.0):
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


@pytest.fixture
def admin_id(conn):
    return _insert_user(conn, 'admin', 'admin@test.com',
                        'Admin@123456!', role='admin', credit_balance=1000.0)


@pytest.fixture
def user_id(conn):
    return _insert_user(conn, 'alice', 'alice@test.com',
                        'Alice@123456!', credit_balance=500.0)


@pytest.fixture
def user2_id(conn):
    return _insert_user(conn, 'bob', 'bob@test.com',
                        'Bob@123456789!', credit_balance=300.0)


@pytest.fixture
def completed_session(conn, user_id, user2_id):
    """A completed session between user_id (initiator) and user2_id (participant)."""
    now = utcnow()
    cur = conn.execute(
        'INSERT INTO sessions '
        '(initiator_id, participant_id, status, description, '
        ' credit_amount, created_at, updated_at, completed_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, user2_id, 'completed', 'Test session',
         50.0, now, now, now)
    )
    conn.commit()
    return cur.lastrowid
