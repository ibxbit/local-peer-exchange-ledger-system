"""Validate that the daily scheduler produced today's expected report.

Intended usage: run this after 02:00 local time in CI/staging to confirm that
the 2:00 AM scheduler job fired and wrote yesterday's report.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import requests


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == '':
        raise SystemExit(f'Missing required environment variable: {name}')
    return value


def main() -> int:
    base_url = _env('PEX_BASE_URL', 'http://127.0.0.1:5000')
    admin_user = _env('PEX_ADMIN_USER', 'admin')
    admin_password = _env('PEX_ADMIN_PASSWORD')

    expected_report_date = (dt.date.today() - dt.timedelta(days=1)).isoformat()

    login = requests.post(
        f'{base_url}/api/auth/login',
        json={'username': admin_user, 'password': admin_password},
        timeout=20,
    )
    if login.status_code != 200:
        print(f'FAIL: admin login failed ({login.status_code}).', file=sys.stderr)
        return 1

    token = login.json().get('token')
    if not token:
        print('FAIL: login response missing token.', file=sys.stderr)
        return 1

    reports = requests.get(
        f'{base_url}/api/analytics/reports',
        headers={'Authorization': f'Bearer {token}'},
        timeout=20,
    )
    if reports.status_code != 200:
        print(f'FAIL: cannot list reports ({reports.status_code}).', file=sys.stderr)
        return 1

    rows = reports.json().get('reports', [])
    if not rows:
        print('FAIL: no daily reports were found.', file=sys.stderr)
        return 1

    latest = rows[0].get('report_date')
    if latest != expected_report_date:
        print(
            'FAIL: latest report_date mismatch: '
            f'expected={expected_report_date}, got={latest}',
            file=sys.stderr,
        )
        return 1

    print(f'PASS: scheduler report found for {expected_report_date}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
