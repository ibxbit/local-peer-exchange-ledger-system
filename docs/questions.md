# Questions (Business Gaps)

Question: How to handle expired matches?

Hypothesis: Auto-cancel after 3 mins per prompt.

Solution: Implemented queue/session lifecycle APIs and cancellation controls; operational auto-expiry should be enforced by periodic cleanup and/or scheduler-backed job.

---

Question: How should retry and cooldown be enforced for repeated matching attempts?

Hypothesis: Retry up to 3 times with 30-second cooldown and user-level throttling.

Solution: Matching queue, cancel, and match trigger endpoints are implemented; retry/cooldown policy should be centralized in matching service logic with server-side counters and timestamps.

---

Question: What blocks a user from entering the matching flow?

Hypothesis: Muted/banned users, low-credit users, and users with too many open disputes are blocked.

Solution: Admin moderation endpoints (ban/mute) and reputation/violation workflows are implemented; matching admission checks should enforce these gates before queue insert.

---

Question: How should "do not match" preferences behave?

Hypothesis: Temporary per-user block list prevents pairing with selected peers.

Solution: Implemented block management endpoints (`add`, `remove`, `list`) in matching module; pairing algorithm should always filter blocked peers bidirectionally.

---

Question: How to keep identity document access secure while still reviewable?

Hypothesis: Only admins can access verification documents, with encryption-at-rest and audited access.

Solution: Verification submission/status/review/document endpoints are implemented; secure storage and strict admin-only retrieval are part of current architecture intent.

---

Question: How should post-session trust signals influence future interactions?

Hypothesis: A composite reputation score should incorporate ratings and moderation outcomes.

Solution: Implemented reputation rating, score retrieval, violation reporting, and appeal resolution APIs; score policy should remain transparent and deterministic.

---

Question: How to guarantee ledger edits are traceable and non-destructive?

Hypothesis: Posted financial events should be immutable; corrections happen through adjusting/reversing entries only.

Solution: Ledger supports invoice pay/void/refund/adjust actions and integrity verification endpoint; workflow aligns with append-only financial traceability.

---

Question: How are overdue invoices identified and processed?

Hypothesis: Net-15 default with overdue marking by scheduled or manual processing.

Solution: Implemented `POST /api/ledger/invoices/mark-overdue` endpoint and analytics/reporting support to operationalize overdue tracking.

---

Question: How can admins prove logs were not tampered with?

Hypothesis: Audit trail should be immutable and hash-chain verifiable.

Solution: Audit listing, summary, and chain verification endpoints are implemented (`/api/audit/logs`, `/summary`, `/verify`).

---

Question: How should offline payment confirmations and refunds be reconciled with balances?

Hypothesis: Payment confirmation credits user account; refunds reverse prior effects.

Solution: Implemented payment submit/confirm/refund APIs with ledger-facing behavior and auditability constraints.

---

Question: What KPI outputs are required for operations visibility?

Hypothesis: Conversion, AOV, repurchase, and dispute rates should be available with export/report options.

Solution: Implemented analytics KPI and export endpoints plus report list/generation endpoints; calculation definitions should remain documented and stable for stakeholders.
