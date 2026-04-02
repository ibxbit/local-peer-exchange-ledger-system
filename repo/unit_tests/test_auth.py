"""Unit tests for auth_service: register, login, lockout, password change."""

import pytest
from app.services import auth_service
from app.dal import user_dal


class TestRegister:
    def test_success(self, conn):
        result = auth_service.register(
            conn, 'newuser', 'new@test.com', 'NewPass@123456!'
        )
        assert 'user_id' in result
        assert result['user_id'] > 0

    def test_duplicate_username(self, conn):
        auth_service.register(conn, 'dupuser', 'dup1@test.com', 'Pass@123456!')
        with pytest.raises(LookupError, match='Username already taken'):
            auth_service.register(conn, 'dupuser', 'dup2@test.com', 'Pass@123456!')

    def test_duplicate_email(self, conn):
        auth_service.register(conn, 'user_a', 'same@test.com', 'Pass@123456!')
        with pytest.raises(LookupError, match='Email already registered'):
            auth_service.register(conn, 'user_b', 'same@test.com', 'Pass@123456!')

    def test_invalid_email(self, conn):
        with pytest.raises(ValueError, match='Invalid email'):
            auth_service.register(conn, 'badmail', 'notanemail', 'Pass@123456!')

    def test_weak_password(self, conn):
        with pytest.raises(ValueError):
            auth_service.register(conn, 'weakpw', 'weak@test.com', 'short')

    def test_invalid_username_too_short(self, conn):
        with pytest.raises(ValueError):
            auth_service.register(conn, 'ab', 'ok@test.com', 'Pass@123456!')

    def test_invalid_username_special_chars(self, conn):
        with pytest.raises(ValueError):
            auth_service.register(conn, 'bad user!', 'ok@test.com', 'Pass@123456!')

    def test_audit_log_written(self, conn):
        auth_service.register(conn, 'audituser', 'audit@test.com', 'Pass@123456!')
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'USER_REGISTERED'"
        ).fetchone()
        assert row is not None


class TestLogin:
    def _register(self, conn, username='loginuser', email='login@test.com',
                  password='Login@123456!'):
        return auth_service.register(conn, username, email, password)

    def test_success_returns_token(self, conn):
        self._register(conn)
        result = auth_service.login(conn, 'loginuser', 'Login@123456!')
        assert 'token' in result
        assert result['user']['username'] == 'loginuser'

    def test_wrong_password(self, conn):
        self._register(conn)
        with pytest.raises(ValueError, match='Invalid credentials'):
            auth_service.login(conn, 'loginuser', 'wrongpassword')

    def test_nonexistent_user(self, conn):
        with pytest.raises(ValueError, match='Invalid credentials'):
            auth_service.login(conn, 'ghost', 'Pass@123456!')

    def test_failed_attempts_incremented(self, conn):
        self._register(conn)
        for _ in range(3):
            try:
                auth_service.login(conn, 'loginuser', 'wrong')
            except ValueError:
                pass
        user = user_dal.get_by_username(conn, 'loginuser')
        assert user['failed_login_attempts'] == 3

    def test_lockout_after_max_attempts(self, conn):
        self._register(conn)
        from config import Config
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            try:
                auth_service.login(conn, 'loginuser', 'wrong')
            except (ValueError, PermissionError):
                pass
        with pytest.raises(PermissionError, match='locked'):
            auth_service.login(conn, 'loginuser', 'Login@123456!')

    def test_inactive_account_blocked(self, conn):
        self._register(conn)
        user = user_dal.get_by_username(conn, 'loginuser')
        user_dal.update_fields(conn, user['id'], is_active=0)
        with pytest.raises(PermissionError, match='disabled'):
            auth_service.login(conn, 'loginuser', 'Login@123456!')

    def test_success_resets_failed_attempts(self, conn):
        self._register(conn)
        try:
            auth_service.login(conn, 'loginuser', 'wrong')
        except ValueError:
            pass
        auth_service.login(conn, 'loginuser', 'Login@123456!')
        user = user_dal.get_by_username(conn, 'loginuser')
        assert user['failed_login_attempts'] == 0

    def test_login_audit_logged(self, conn):
        self._register(conn)
        auth_service.login(conn, 'loginuser', 'Login@123456!')
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'LOGIN_SUCCESS'"
        ).fetchone()
        assert row is not None

    def test_failed_login_audit_logged(self, conn):
        self._register(conn)
        try:
            auth_service.login(conn, 'loginuser', 'wrongpass')
        except ValueError:
            pass
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'LOGIN_FAILED'"
        ).fetchone()
        assert row is not None


class TestChangePassword:
    def _setup(self, conn):
        auth_service.register(conn, 'cpuser', 'cp@test.com', 'OldPass@123456!')
        user = user_dal.get_by_username(conn, 'cpuser')
        return user['id']

    def test_change_password_success(self, conn):
        uid = self._setup(conn)
        auth_service.change_password(conn, uid, 'OldPass@123456!', 'NewPass@789012!')
        # Old password should no longer work
        with pytest.raises(ValueError):
            auth_service.change_password(conn, uid, 'OldPass@123456!', 'Anything@1!')

    def test_wrong_current_password(self, conn):
        uid = self._setup(conn)
        with pytest.raises(ValueError, match='incorrect'):
            auth_service.change_password(conn, uid, 'WRONG@123456!', 'NewPass@789!')

    def test_weak_new_password_rejected(self, conn):
        uid = self._setup(conn)
        with pytest.raises(ValueError):
            auth_service.change_password(conn, uid, 'OldPass@123456!', 'weak')

    def test_audit_logged(self, conn):
        uid = self._setup(conn)
        auth_service.change_password(conn, uid, 'OldPass@123456!', 'NewPass@789012!')
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'PASSWORD_CHANGED'"
        ).fetchone()
        assert row is not None
