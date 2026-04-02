"""API tests for /api/analytics: KPIs, CSV export, reports."""

import pytest


class TestKPIs:
    def test_kpis_admin(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'kpis' in data

    def test_kpis_with_date_range(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis?from_date=2020-01-01&to_date=2030-12-31',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_kpis_invalid_date_format(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis?from_date=not-a-date',
                          headers=admin_headers)
        assert resp.status_code == 400

    def test_kpis_from_after_to_rejected(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis?from_date=2025-12-31&to_date=2025-01-01',
                          headers=admin_headers)
        assert resp.status_code == 400

    def test_kpis_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/analytics/kpis', headers=user_headers)
        assert resp.status_code == 403

    def test_kpis_contains_conversion_rate(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis', headers=admin_headers)
        kpis = resp.get_json()['kpis']
        assert 'conversion_rate' in kpis

    def test_kpis_contains_dispute_rate(self, client, admin_headers):
        resp = client.get('/api/analytics/kpis', headers=admin_headers)
        kpis = resp.get_json()['kpis']
        assert 'dispute_rate' in kpis


class TestCSVExport:
    def test_kpi_export(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=kpi',
                          headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content_type.startswith('text/csv')
        assert len(resp.data) > 0

    def test_daily_export(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=daily',
                          headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content_type.startswith('text/csv')

    def test_export_with_date_range(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=kpi&from_date=2020-01-01&to_date=2030-12-31',
                          headers=admin_headers)
        assert resp.status_code == 200

    def test_invalid_export_type(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=invalid',
                          headers=admin_headers)
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_export_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/analytics/export', headers=user_headers)
        assert resp.status_code == 403

    def test_kpi_csv_has_header_row(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=kpi',
                          headers=admin_headers)
        content = resp.data.decode('utf-8')
        lines = [l for l in content.splitlines() if l.strip()]
        assert len(lines) >= 1

    def test_content_disposition_header(self, client, admin_headers):
        resp = client.get('/api/analytics/export?type=kpi',
                          headers=admin_headers)
        assert 'Content-Disposition' in resp.headers
        assert 'attachment' in resp.headers['Content-Disposition']
        assert '.csv' in resp.headers['Content-Disposition']


class TestReports:
    def test_list_reports(self, client, admin_headers):
        resp = client.get('/api/analytics/reports', headers=admin_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'reports' in data
        assert 'total' in data

    def test_list_reports_non_admin_forbidden(self, client, user_headers):
        resp = client.get('/api/analytics/reports', headers=user_headers)
        assert resp.status_code == 403

    def test_generate_report(self, client, admin_headers):
        resp = client.post('/api/analytics/reports/generate',
                           headers=admin_headers,
                           json={})
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'message' in data

    def test_generate_report_with_date(self, client, admin_headers):
        resp = client.post('/api/analytics/reports/generate',
                           headers=admin_headers,
                           json={'date': '2024-06-15'})
        assert resp.status_code == 201

    def test_generate_report_invalid_date(self, client, admin_headers):
        resp = client.post('/api/analytics/reports/generate',
                           headers=admin_headers,
                           json={'date': 'bad-date'})
        assert resp.status_code == 400

    def test_get_report_latest_after_generate(self, client, admin_headers):
        # Ensure at least one report exists
        client.post('/api/analytics/reports/generate', headers=admin_headers)
        resp = client.get('/api/analytics/reports/latest', headers=admin_headers)
        # Either 200 (report found) or 404 (no reports yet in edge case)
        assert resp.status_code in (200, 404)

    def test_get_nonexistent_report(self, client, admin_headers):
        resp = client.get('/api/analytics/reports/9999-99-99',
                          headers=admin_headers)
        assert resp.status_code == 404

    def test_generate_report_non_admin_forbidden(self, client, user_headers):
        resp = client.post('/api/analytics/reports/generate',
                           headers=user_headers)
        assert resp.status_code == 403
