"""Logging filter that redacts sensitive data and normalises format strings."""

import logging
import re

# Patterns that identify sensitive field values in log messages.
# Each pattern matches a key=value or "key": "value" style occurrence.
_REDACT_PATTERNS = [
    re.compile(r'(password["\s:=]+)[^\s,\'"&}]+', re.IGNORECASE),
    re.compile(r'(token["\s:=]+)[^\s,\'"&}]+', re.IGNORECASE),
    re.compile(r'(secret["\s:=]+)[^\s,\'"&}]+', re.IGNORECASE),
    re.compile(r'(authorization:\s*\S+\s+)[^\s,\'"&}]+', re.IGNORECASE),
    re.compile(r'(api[_-]?key["\s:=]+)[^\s,\'"&}]+', re.IGNORECASE),
]

_REPLACEMENT = r'\g<1>[REDACTED]'


def _redact(message: str) -> str:
    """Return *message* with sensitive field values replaced by [REDACTED]."""
    for pattern in _REDACT_PATTERNS:
        message = pattern.sub(_REPLACEMENT, message)
    return message


class SensitiveDataFilter(logging.Filter):
    """Logging filter that:

    1. Replaces ``%d`` format specifiers with ``%s`` so that uvicorn.access
       log records (which pass the HTTP status code as a string in some
       environments) do not raise ``TypeError: %d format: a real number is
       required, not str``.
    2. Redacts common sensitive field values (passwords, tokens, secrets …)
       from the rendered log message.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            # Normalise %d → %s so the record formats correctly whether the
            # status-code argument arrives as an int or a str.
            if isinstance(record.msg, str):
                record.msg = record.msg.replace('%d', '%s')

            record.msg = _redact(str(record.msg))

            # Redact sensitive data inside string arguments as well.
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: _redact(str(v)) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        _redact(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:  # pragma: no cover – never drop a log record
            pass

        return True
