# API Specification

## Overview
- **Base URL:** `http://localhost:8000`
- **API Prefix:** `/api`
- **Auth:** JWT bearer token (`Authorization: Bearer <token>`)
- **Content Type:** `application/json`
- **Roles:** `user`, `admin`, `auditor`

## Authentication
- `POST /api/auth/register` - Register account
- `POST /api/auth/login` - Login and receive JWT
- `GET /api/auth/me` - Current user profile
- `POST /api/auth/change-password` - Change password

## Users
- `GET /api/users` - List users (admin)
- `GET /api/users/{user_id}` - Get user detail
- `PUT /api/users/{user_id}` - Update user profile/admin-editable fields
- `PUT /api/users/{user_id}/role` - Update role (admin)
- `PUT /api/users/{user_id}/status` - Activate/deactivate or status updates
- `GET /api/users/{user_id}/reputation` - User reputation summary

## Real-Name Verification
- `POST /api/verification/submit` - Submit verification request with document metadata
- `GET /api/verification/status` - Get caller verification status
- `GET /api/verification` - List verification requests (admin view)
- `PUT /api/verification/{verification_id}/review` - Approve/reject verification
- `GET /api/verification/{verification_id}/document` - Admin-only secure document access

## Matching
- `GET /api/matching/profile` - Read matching preferences/profile
- `POST|PUT /api/matching/profile` - Create/update matching profile
- `GET /api/matching/search` - Search potential peers
- `POST /api/matching/sessions` - Create session/exchange request
- `GET /api/matching/sessions` - List sessions
- `GET /api/matching/sessions/{session_id}` - Session detail
- `PUT /api/matching/sessions/{session_id}` - Update session status
- `POST /api/matching/queue` - Join matching queue
- `GET /api/matching/queue` - List queue entries
- `GET /api/matching/queue/{entry_id}` - Queue entry detail
- `PUT /api/matching/queue/{entry_id}/cancel` - Cancel queue entry
- `POST /api/matching/queue/match` - Trigger matching run
- `POST /api/matching/block` - Add temporary block/do-not-match
- `DELETE /api/matching/block/{blocked_id}` - Remove block
- `GET /api/matching/block` - List blocks

## Reputation and Violations
- `POST /api/reputation/rate` - Submit post-session rating
- `GET /api/reputation/ratings/{user_id}` - Ratings for user
- `GET /api/reputation/score/{user_id}` - Composite score
- `POST /api/reputation/violations` - Report violation
- `GET /api/reputation/violations` - List violations
- `PUT /api/reputation/violations/{violation_id}/resolve` - Resolve violation
- `POST /api/reputation/violations/{violation_id}/appeal` - Submit appeal
- `GET /api/reputation/appeals` - List appeals
- `PUT /api/reputation/appeals/{appeal_id}/resolve` - Resolve appeal

## Ledger
- `GET /api/ledger` - Ledger entries/history
- `GET /api/ledger/balance` - Balance summary
- `POST /api/ledger/credit` - Credit user (admin)
- `POST /api/ledger/debit` - Debit user (admin/authorized)
- `POST /api/ledger/transfer` - Peer transfer
- `GET /api/ledger/verify` - Verify ledger hash chain integrity
- `POST /api/ledger/invoices` - Create invoice
- `GET /api/ledger/invoices` - List invoices
- `GET /api/ledger/invoices/{invoice_id}` - Invoice detail
- `POST /api/ledger/invoices/{invoice_id}/pay` - Mark paid / settle
- `POST /api/ledger/invoices/{invoice_id}/void` - Void invoice
- `POST /api/ledger/invoices/{invoice_id}/refund` - Refund via reversing entry
- `POST /api/ledger/invoices/{invoice_id}/adjust` - Adjustment entry
- `POST /api/ledger/invoices/mark-overdue` - Overdue processing job endpoint

## Payments (Offline)
- `POST /api/payments/submit` - Submit cash/check/ACH reference
- `POST /api/payments/{payment_id}/confirm` - Admin confirm payment
- `POST /api/payments/{payment_id}/refund` - Admin refund payment
- `GET /api/payments` - List payments
- `GET /api/payments/{payment_id}` - Payment detail

## Audit
- `GET /api/audit/logs` - List immutable audit events
- `GET /api/audit/logs/summary` - Aggregated audit stats
- `GET /api/audit/logs/verify` - Verify audit hash chain

## Analytics
- `GET /api/analytics/kpis` - KPI dashboard
- `GET /api/analytics/export` - CSV export (kpi/daily)
- `GET /api/analytics/reports` - List generated reports
- `POST /api/analytics/reports/generate` - Generate report (manual trigger)
- `GET /api/analytics/reports/{report_date}` - Download/view report

## Admin
- `GET /api/admin/analytics` - Admin analytics overview
- `GET /api/admin/users` - Admin user listing
- `GET /api/admin/users/{user_id}` - Admin user detail
- `PUT /api/admin/users/{user_id}/ban` - Ban user
- `PUT /api/admin/users/{user_id}/unban` - Unban user
- `PUT /api/admin/users/{user_id}/mute` - Mute user
- `PUT /api/admin/users/{user_id}/unmute` - Unmute user
- `GET /api/admin/sessions` - Session moderation list
- `GET /api/admin/violations` - Moderation violation list
- `GET /api/admin/violations/{violation_id}` - Violation detail
- `PUT /api/admin/violations/{violation_id}/escalate` - Escalate violation
- `GET /api/admin/appeals` - Appeals queue
- `PUT /api/admin/appeals/{appeal_id}/resolve` - Resolve appeal
- `GET /api/admin/permissions/{target_admin_id}` - Permission map
- `PUT /api/admin/permissions/{target_admin_id}/{resource}` - Grant/update permission
- `DELETE /api/admin/permissions/{target_admin_id}/{resource}` - Revoke permission
