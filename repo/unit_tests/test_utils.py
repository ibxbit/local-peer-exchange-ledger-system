"""Unit tests for utility functions: hashing, HMAC, password, document validation."""

import pytest
from app.utils import (
    sha256, sha256_bytes,
    hash_ledger_entry, hash_audit_entry,
    sign_payment_payload, verify_payment_signature,
    validate_password, hash_password, verify_password,
    validate_document_upload, mask_email,
    encrypt_data, decrypt_data, encrypt_bytes, decrypt_bytes,
)


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

class TestSha256:
    def test_deterministic(self):
        assert sha256('hello') == sha256('hello')

    def test_different_inputs_differ(self):
        assert sha256('hello') != sha256('world')

    def test_bytes_variant(self):
        result = sha256_bytes(b'hello')
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)


# ---------------------------------------------------------------------------
# Ledger and audit hash chaining
# ---------------------------------------------------------------------------

class TestHashChaining:
    def test_ledger_hash_deterministic(self):
        entry = {
            'user_id': 1, 'transaction_type': 'credit',
            'amount': 10.0, 'balance_after': 110.0,
            'created_at': '2024-01-01T00:00:00',
            'created_by': 2, 'description': 'test',
        }
        h1 = hash_ledger_entry(entry, None)
        h2 = hash_ledger_entry(entry, None)
        assert h1 == h2

    def test_ledger_hash_changes_with_prev(self):
        entry = {
            'user_id': 1, 'transaction_type': 'credit',
            'amount': 10.0, 'balance_after': 110.0,
            'created_at': '2024-01-01T00:00:00',
            'created_by': 2, 'description': '',
        }
        h_no_prev  = hash_ledger_entry(entry, None)
        h_with_prev = hash_ledger_entry(entry, 'abc123')
        assert h_no_prev != h_with_prev

    def test_audit_hash_deterministic(self):
        entry = {
            'user_id': 1, 'action': 'LOGIN_SUCCESS',
            'entity_type': 'user', 'entity_id': 1,
            'created_at': '2024-01-01T00:00:00',
        }
        assert hash_audit_entry(entry, None) == hash_audit_entry(entry, None)

    def test_audit_hash_changes_with_prev(self):
        entry = {
            'user_id': 1, 'action': 'LOGIN_SUCCESS',
            'entity_type': 'user', 'entity_id': 1,
            'created_at': '2024-01-01T00:00:00',
        }
        assert hash_audit_entry(entry, None) != hash_audit_entry(entry, 'prevhash')


# ---------------------------------------------------------------------------
# HMAC payment signing
# ---------------------------------------------------------------------------

class TestPaymentHmac:
    PAYLOAD = {
        'user_id': 1, 'amount': 100.0,
        'payment_type': 'cash', 'reference_number': 'REF001',
        'created_at': '2024-01-01T00:00:00',
    }

    def test_sign_is_hex_string(self):
        sig = sign_payment_payload(self.PAYLOAD)
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_deterministic(self):
        assert sign_payment_payload(self.PAYLOAD) == sign_payment_payload(self.PAYLOAD)

    def test_verify_valid_signature(self):
        sig = sign_payment_payload(self.PAYLOAD)
        assert verify_payment_signature(self.PAYLOAD, sig) is True

    def test_verify_wrong_signature_rejected(self):
        assert verify_payment_signature(self.PAYLOAD, 'a' * 64) is False

    def test_verify_tampered_payload_rejected(self):
        sig = sign_payment_payload(self.PAYLOAD)
        tampered = {**self.PAYLOAD, 'amount': 999.0}
        assert verify_payment_signature(tampered, sig) is False

    def test_key_order_invariant(self):
        """JSON sort_keys=True means key order in dict doesn't matter."""
        p1 = {'amount': 10.0, 'user_id': 1}
        p2 = {'user_id': 1, 'amount': 10.0}
        assert sign_payment_payload(p1) == sign_payment_payload(p2)


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

class TestPasswordValidation:
    def test_strong_password_valid(self):
        ok, _ = validate_password('Str0ng!Passw0rd')
        assert ok is True

    def test_too_short(self):
        ok, msg = validate_password('Short@1!')
        assert ok is False
        assert 'characters' in msg

    def test_no_uppercase(self):
        ok, _ = validate_password('lowercase1@special')
        assert ok is False

    def test_no_lowercase(self):
        ok, _ = validate_password('UPPERCASE1@SPECIAL')
        assert ok is False

    def test_no_digit(self):
        ok, _ = validate_password('NoDigitHere@@@!')
        assert ok is False

    def test_no_special_char(self):
        ok, _ = validate_password('NoSpecialChar1234')
        assert ok is False

    def test_hash_and_verify(self):
        pw = 'Correct@Horse123'
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True
        assert verify_password('wrong', hashed) is False


# ---------------------------------------------------------------------------
# Document upload validation
# ---------------------------------------------------------------------------

class TestDocumentValidation:
    JPEG_MAGIC = b'\xff\xd8\xff' + b'\x00' * 100
    PNG_MAGIC  = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
    PDF_MAGIC  = b'%PDF' + b'\x00' * 100

    def test_jpeg_accepted(self):
        ok, err, mime = validate_document_upload(self.JPEG_MAGIC)
        assert ok is True
        assert mime == 'image/jpeg'

    def test_png_accepted(self):
        ok, err, mime = validate_document_upload(self.PNG_MAGIC)
        assert ok is True
        assert mime == 'image/png'

    def test_pdf_accepted(self):
        ok, err, mime = validate_document_upload(self.PDF_MAGIC)
        assert ok is True
        assert mime == 'application/pdf'

    def test_unknown_type_rejected(self):
        ok, err, mime = validate_document_upload(b'UNKNOWN' + b'\x00' * 100)
        assert ok is False
        assert 'Unsupported' in err

    def test_empty_rejected(self):
        ok, err, _ = validate_document_upload(b'')
        assert ok is False

    def test_too_large_rejected(self):
        big = b'\xff\xd8\xff' + b'\x00' * (5 * 1024 * 1024 + 1)
        ok, err, _ = validate_document_upload(big)
        assert ok is False
        assert 'size' in err.lower() or 'MB' in err


# ---------------------------------------------------------------------------
# Encryption round-trip
# ---------------------------------------------------------------------------

class TestEncryption:
    def test_string_roundtrip(self):
        plain = 'sensitive text'
        token = encrypt_data(plain)
        assert decrypt_data(token) == plain

    def test_bytes_roundtrip(self):
        data = b'\x00\x01\x02binary\xff'
        token = encrypt_bytes(data)
        assert decrypt_bytes(token) == data

    def test_different_nonces(self):
        """Two encryptions of the same plaintext must produce different ciphertext."""
        t1 = encrypt_data('same')
        t2 = encrypt_data('same')
        assert t1 != t2


# ---------------------------------------------------------------------------
# Email masking
# ---------------------------------------------------------------------------

class TestMaskEmail:
    def test_standard_email(self):
        masked = mask_email('alice@example.com')
        assert masked == 'al***@example.com'

    def test_short_local(self):
        masked = mask_email('a@x.com')
        assert '***' in masked

    def test_invalid_no_at(self):
        assert mask_email('notanemail') == '***'
