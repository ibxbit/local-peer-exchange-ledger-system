/**
 * admin.js — Admin dashboard, user management, verifications, violations, sessions.
 */

import { API } from './api.js';
import { el, qs, badge, stars, fmtDate, maskEmail,
         showAlert, showModal, closeModal,
         buildPagination, loadingHTML, emptyHTML } from './utils.js';

// ---- Analytics ----------------------------------------------------------

export async function renderAdminDashboard() {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const { ok, data } = await API.get('/admin/analytics');
  if (!ok) { vc.innerHTML = `<div class="alert alert-error">${data.error}</div>`; return; }

  const u  = data.users   || {};
  const s  = data.sessions|| {};
  const l  = data.ledger  || {};
  const m  = data.moderation || {};
  const r  = data.reputation || {};

  vc.innerHTML = `
<div class="page-header"><h2>⚙ Admin Dashboard</h2>
  <button class="btn btn-secondary" onclick="App.navigate('admin-dashboard')">↺ Refresh</button>
</div>

<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">Total Users</div>
    <div class="stat-value">${u.total ?? '—'}</div>
    <div class="stat-sub">${u.active ?? 0} active</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">New (7 days)</div>
    <div class="stat-value">${u.registered_last_7d ?? '—'}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Sessions</div>
    <div class="stat-value">${s.total ?? '—'}</div>
    <div class="stat-sub">${s.by_status?.completed ?? 0} completed</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Ledger Volume</div>
    <div class="stat-value">${(l.total_volume ?? 0).toFixed(0)}</div>
    <div class="stat-sub">${l.total_entries ?? 0} entries</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Pending Verifications</div>
    <div class="stat-value" style="color:var(--c-warn)">${m.pending_verifications ?? 0}</div>
    <div class="stat-sub">
      <a href="#" onclick="App.navigate('admin-verifications')" style="color:var(--c-accent)">Review →</a>
    </div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Open Violations</div>
    <div class="stat-value" style="color:var(--c-danger)">${m.open_violations ?? 0}</div>
    <div class="stat-sub">
      ${m.pending_appeals ?? 0} pending appeals ·
      <a href="#" onclick="App.navigate('admin-violations')" style="color:var(--c-accent)">Review →</a>
    </div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Platform Avg Rating</div>
    <div class="stat-value">${r.platform_avg_rating ?? '—'}</div>
  </div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
  <div class="card">
    <div class="card-title">Users by Role</div>
    <div style="display:flex;gap:1rem;flex-wrap:wrap">
      ${Object.entries(u.by_role || {}).map(([role, count]) =>
        `<div><span class="role-badge ${role}">${role}</span>
              <strong style="margin-left:.4rem">${count}</strong></div>`
      ).join('')}
    </div>
  </div>
  <div class="card">
    <div class="card-title">Sessions by Status</div>
    <div style="display:flex;gap:1rem;flex-wrap:wrap">
      ${Object.entries(s.by_status || {}).map(([status, count]) =>
        `<div>${badge(status)} <strong style="margin-left:.25rem">${count}</strong></div>`
      ).join('')}
    </div>
  </div>
</div>`;
}

// ---- User Management ----------------------------------------------------

export async function renderAdminUsers(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const search = qs('#au-search')?.value || '';
  const role   = qs('#au-role')?.value   || '';
  const { data } = await API.get('/admin/users', { page, per_page: 20, search, role });

  vc.innerHTML = `
<div class="page-header"><h2>👥 User Management</h2></div>
<div class="filters">
  <div class="filter-group">
    <label>Search</label>
    <input id="au-search" type="text" placeholder="username or email…" value="${search}">
  </div>
  <div class="filter-group">
    <label>Role</label>
    <select id="au-role">
      <option value="">All Roles</option>
      ${['user','admin','auditor'].map(r =>
        `<option ${r === role ? 'selected' : ''}>${r}</option>`).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-au-search" style="align-self:flex-end">Search</button>
</div>
<div class="table-wrap">
  <table>
    <thead><tr>
      <th>ID</th><th>Username</th><th>Email</th><th>Role</th>
      <th>Status</th><th>Balance</th><th>Sessions</th>
      <th>Rating</th><th>Disputes</th><th>Actions</th>
    </tr></thead>
    <tbody>
      ${(data.users || []).length
        ? data.users.map(_adminUserRow).join('')
        : `<tr><td colspan="10">${emptyHTML('No users.')}</td></tr>`}
    </tbody>
  </table>
</div>
${buildPagination(data.total || 0, page, 20, p => renderAdminUsers(p))}`;

  el('btn-au-search').addEventListener('click', () => renderAdminUsers(1));
  el('au-search').addEventListener('keypress',  e => { if (e.key === 'Enter') renderAdminUsers(1); });
}

function _adminUserRow(u) {
  const statusBadge = u.is_active
    ? badge('active', 'Active') : badge('cancelled', 'Inactive');
  return `<tr>
    <td>#${u.id}</td>
    <td><strong>${u.username}</strong></td>
    <td style="font-size:.8rem">${u.email}</td>
    <td><span class="role-badge ${u.role}">${u.role}</span></td>
    <td>${statusBadge}</td>
    <td>${(u.credit_balance ?? 0).toFixed(2)}</td>
    <td>${u.session_count ?? 0}</td>
    <td>${u.avg_rating ? `${(+u.avg_rating).toFixed(1)} ★` : '—'}</td>
    <td style="color:${(u.open_violations??0)>0?'var(--c-danger)':'inherit'}">
      ${u.open_violations ?? 0}
    </td>
    <td style="display:flex;gap:.35rem;flex-wrap:wrap">
      <button class="btn btn-sm btn-secondary"
        onclick="App.adminManageUser(${u.id},'${u.username}','${u.role}',${u.is_active})">
        Manage
      </button>
      <button class="btn btn-sm btn-primary"
        onclick="App.adminCreditModal(${u.id},'${u.username}')">
        Credits
      </button>
    </td>
  </tr>`;
}

export function adminManageUser(userId, username, role, isActive) {
  showModal(`
<button class="modal-close">×</button>
<h3>Manage: ${username}</h3>
<div style="display:flex;flex-direction:column;gap:.75rem;margin-top:.75rem">

  <div class="form-group">
    <label>Change Role</label>
    <select id="mgr-role">
      ${['user','admin','auditor'].map(r =>
        `<option value="${r}" ${r === role ? 'selected' : ''}>${r}</option>`).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-change-role">Update Role</button>

  <button class="btn ${isActive ? 'btn-warn' : 'btn-success'}" id="btn-toggle">
    ${isActive ? 'Deactivate User' : 'Activate User'}
  </button>

  <button class="btn btn-secondary" id="btn-mute-user">Mute User</button>
  <button class="btn btn-danger"    id="btn-ban-user">Ban User</button>
</div>`);

  el('btn-change-role').addEventListener('click', async () => {
    const { ok, data } = await API.put(`/users/${userId}/role`,
      { role: el('mgr-role').value });
    showAlert(ok ? 'Role updated.' : data.error, ok ? 'success' : 'error');
    if (ok) { closeModal(); renderAdminUsers(); }
  });

  el('btn-toggle').addEventListener('click', async () => {
    const { ok, data } = await API.put(`/users/${userId}/status`,
      { is_active: !isActive });
    showAlert(ok ? 'Status updated.' : data.error, ok ? 'success' : 'error');
    if (ok) { closeModal(); renderAdminUsers(); }
  });

  el('btn-mute-user').addEventListener('click', () => {
    closeModal();
    showModal(`
<button class="modal-close">×</button>
<h3>Mute ${username}</h3>
<div class="form-group">
  <label>Muted Until <small>(ISO-8601 datetime)</small></label>
  <input id="mute-until" type="datetime-local">
</div>
<div class="form-group">
  <label>Reason</label>
  <input id="mute-reason" type="text" placeholder="Reason…">
</div>
<button class="btn btn-warn btn-full" id="btn-confirm-mute">Confirm Mute</button>`);

    el('btn-confirm-mute').addEventListener('click', async () => {
      const dt = el('mute-until').value;
      if (!dt) { showAlert('Select a datetime.', 'warn'); return; }
      const { ok, data } = await API.put(`/admin/users/${userId}/mute`, {
        muted_until: new Date(dt).toISOString(),
        reason:      el('mute-reason').value.trim(),
      });
      showAlert(ok ? 'User muted.' : data.error, ok ? 'success' : 'error');
      if (ok) { closeModal(); renderAdminUsers(); }
    });
  });

  el('btn-ban-user').addEventListener('click', () => {
    closeModal();
    showModal(`
<button class="modal-close">×</button>
<h3>Ban ${username}</h3>
<div class="form-group">
  <label>Reason for ban</label>
  <textarea id="ban-reason" rows="3" placeholder="Required…"></textarea>
</div>
<button class="btn btn-danger btn-full" id="btn-confirm-ban">Confirm Ban</button>`);

    el('btn-confirm-ban').addEventListener('click', async () => {
      const reason = el('ban-reason').value.trim();
      if (!reason) { showAlert('Reason is required.', 'warn'); return; }
      const { ok, data } = await API.put(`/admin/users/${userId}/ban`, { reason });
      showAlert(ok ? 'User banned.' : data.error, ok ? 'success' : 'error');
      if (ok) { closeModal(); renderAdminUsers(); }
    });
  });
}

export function adminCreditModal(userId, username) {
  showModal(`
<button class="modal-close">×</button>
<h3>Adjust Credits: ${username}</h3>
<form id="credit-form">
  <div class="form-group">
    <label>Action</label>
    <select id="cred-action">
      <option value="credit">Add Credits</option>
      <option value="debit">Debit Credits</option>
    </select>
  </div>
  <div class="form-group">
    <label>Amount</label>
    <input id="cred-amt" type="number" min="0.01" step="0.01" placeholder="0.00">
  </div>
  <div class="form-group">
    <label>Description</label>
    <input id="cred-desc" type="text" placeholder="Reason…">
  </div>
  <button class="btn btn-primary btn-full" type="submit">Apply</button>
</form>`);

  el('credit-form').addEventListener('submit', async e => {
    e.preventDefault();
    const action = el('cred-action').value;
    const { ok, data } = await API.post(`/ledger/${action}`, {
      user_id:     userId,
      amount:      +el('cred-amt').value,
      description: el('cred-desc').value.trim(),
    }, { idempotencyKey: API.idemKey() });
    showAlert(
      ok ? `Credits adjusted. New balance: ${data.new_balance?.toFixed(2)}` : data.error,
      ok ? 'success' : 'error'
    );
    if (ok) closeModal();
  });
}

// ---- Verifications ------------------------------------------------------

export async function renderAdminVerifications(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const statusF = qs('#ver-filter')?.value || 'pending';
  const { data } = await API.get('/verification',
    { status: statusF, page, per_page: 20 });

  vc.innerHTML = `
<div class="page-header"><h2>✔ Identity Verifications</h2></div>
<div class="filters">
  <div class="filter-group">
    <label>Status</label>
    <select id="ver-filter">
      ${['pending','verified','rejected','all'].map(s =>
        `<option ${s === statusF ? 'selected' : ''}>${s}</option>`).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-ver-filter" style="align-self:flex-end">Filter</button>
</div>
<div class="table-wrap">
  <table>
    <thead><tr><th>ID</th><th>User</th><th>Document</th><th>Status</th>
               <th>Submitted</th><th>Reviewed</th><th>Actions</th></tr></thead>
    <tbody>
      ${(data.verifications || []).length
        ? data.verifications.map(v => `<tr>
            <td>#${v.id}</td>
            <td>${v.username}</td>
            <td>${v.document_type}</td>
            <td>${badge(v.status)}</td>
            <td>${fmtDate(v.submitted_at)}</td>
            <td>${fmtDate(v.reviewed_at)}</td>
            <td>${v.status === 'pending' ? `
              <button class="btn btn-sm btn-success"
                onclick="App.reviewVerification(${v.id},'verified')">Approve</button>
              <button class="btn btn-sm btn-danger"
                onclick="App.reviewVerification(${v.id},'rejected')">Reject</button>
            ` : '—'}</td>
          </tr>`).join('')
        : `<tr><td colspan="7">${emptyHTML('No verifications.')}</td></tr>`}
    </tbody>
  </table>
</div>
${buildPagination(data.total || 0, page, 20, p => renderAdminVerifications(p))}`;

  el('btn-ver-filter').addEventListener('click', () => renderAdminVerifications(1));
}

export function reviewVerification(vid, decision) {
  showModal(`
<button class="modal-close">×</button>
<h3>${decision === 'verified' ? '✅ Approve' : '❌ Reject'} Verification #${vid}</h3>
<div class="form-group">
  <label>Notes <small style="color:var(--c-text-sub)">(optional)</small></label>
  <textarea id="ver-notes" rows="3"
    placeholder="${decision === 'rejected' ? 'Explain the rejection reason…' : 'Optional notes…'}">
  </textarea>
</div>
<button class="btn ${decision === 'verified' ? 'btn-success' : 'btn-danger'} btn-full"
        id="btn-confirm-ver">Confirm ${decision}</button>`);

  el('btn-confirm-ver').addEventListener('click', async () => {
    const { ok, data } = await API.put(`/verification/${vid}/review`, {
      decision, notes: el('ver-notes').value.trim(),
    });
    showAlert(ok ? `Verification ${decision}.` : data.error,
              ok ? 'success' : 'error');
    if (ok) { closeModal(); renderAdminVerifications(); }
  });
}

// ---- Violations + Appeals -----------------------------------------------

export async function renderAdminViolations(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const tab = qs('[data-vtab].active')?.dataset.vtab || 'violations';

  vc.innerHTML = `
<div class="page-header"><h2>⚑ Violations & Appeals</h2></div>
<div style="display:flex;gap:.5rem;margin-bottom:1rem">
  <button class="btn ${tab==='violations'?'btn-primary':'btn-secondary'} btn-sm"
          data-vtab="violations" id="vtab-v">Violations</button>
  <button class="btn ${tab==='appeals'?'btn-primary':'btn-secondary'} btn-sm"
          data-vtab="appeals"    id="vtab-a">Appeals</button>
</div>
<div id="vtab-content">${loadingHTML()}</div>`;

  el('vtab-v').addEventListener('click', () => _loadViolationsTab(page));
  el('vtab-a').addEventListener('click', () => _loadAppealsTab(page));

  tab === 'appeals' ? _loadAppealsTab(page) : _loadViolationsTab(page);
}

async function _loadViolationsTab(page = 1) {
  document.querySelectorAll('[data-vtab]').forEach(b =>
    b.classList.toggle('btn-primary', b.dataset.vtab === 'violations'));
  document.querySelectorAll('[data-vtab]').forEach(b =>
    b.classList.toggle('btn-secondary', b.dataset.vtab !== 'violations'));

  const statusF = qs('#viol-status')?.value || 'open';
  const { data } = await API.get('/reputation/violations',
    { status: statusF, page, per_page: 20 });
  const c = el('vtab-content');

  c.innerHTML = `
<div class="filters">
  <div class="filter-group">
    <label>Status</label>
    <select id="viol-status">
      ${['open','resolved','dismissed',''].map(s =>
        `<option value="${s}" ${s===statusF?'selected':''}>${s||'All'}</option>`).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-viol-filter" style="align-self:flex-end">Filter</button>
</div>
<div class="table-wrap"><table>
  <thead><tr><th>#</th><th>Against</th><th>Reporter</th><th>Type</th>
             <th>Severity</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead>
  <tbody>
    ${(data.violations||[]).length
      ? data.violations.map(v => `<tr>
          <td>#${v.id}</td>
          <td>${v.target_username}</td><td>${v.reporter_username}</td>
          <td>${v.violation_type}</td>
          <td>${badge(v.severity)}</td>
          <td>${badge(v.status)}</td>
          <td>${fmtDate(v.created_at)}</td>
          <td>${v.status==='open' ? `
            <button class="btn btn-sm btn-success"
              onclick="App.resolveViolation(${v.id},'resolved')">Resolve</button>
            <button class="btn btn-sm btn-ghost"
              onclick="App.resolveViolation(${v.id},'dismissed')">Dismiss</button>
          ` : '—'}</td>
        </tr>`).join('')
      : `<tr><td colspan="8">${emptyHTML()}</td></tr>`}
  </tbody>
</table></div>
${buildPagination(data.total||0, page, 20, p => _loadViolationsTab(p))}`;

  el('btn-viol-filter')?.addEventListener('click', () => _loadViolationsTab(1));
}

async function _loadAppealsTab(page = 1) {
  document.querySelectorAll('[data-vtab]').forEach(b =>
    b.classList.toggle('btn-primary', b.dataset.vtab === 'appeals'));
  document.querySelectorAll('[data-vtab]').forEach(b =>
    b.classList.toggle('btn-secondary', b.dataset.vtab !== 'appeals'));

  const { data } = await API.get('/reputation/appeals', { page, per_page: 20 });
  const c = el('vtab-content');

  c.innerHTML = `
<div class="table-wrap"><table>
  <thead><tr><th>#</th><th>Appellant</th><th>Violation</th><th>Type</th>
             <th>Status</th><th>Filed</th><th>Actions</th></tr></thead>
  <tbody>
    ${(data.appeals||[]).length
      ? data.appeals.map(a => `<tr>
          <td>#${a.id}</td><td>${a.appellant_username}</td>
          <td>#${a.violation_id}</td><td>${a.violation_type}</td>
          <td>${badge(a.status)}</td><td>${fmtDate(a.created_at)}</td>
          <td>${a.status==='pending' ? `
            <button class="btn btn-sm btn-success"
              onclick="App.resolveAppeal(${a.id},'upheld')">Uphold</button>
            <button class="btn btn-sm btn-danger"
              onclick="App.resolveAppeal(${a.id},'denied')">Deny</button>
          ` : '—'}</td>
        </tr>`).join('')
      : `<tr><td colspan="7">${emptyHTML()}</td></tr>`}
  </tbody>
</table></div>
${buildPagination(data.total||0, page, 20, p => _loadAppealsTab(p))}`;
}

export function resolveViolation(vid, decision) {
  showModal(`
<button class="modal-close">×</button>
<h3>${decision === 'resolved' ? 'Resolve' : 'Dismiss'} Violation #${vid}</h3>
<div class="form-group">
  <label>Notes <small>(optional)</small></label>
  <textarea id="resolve-notes" rows="3" placeholder="Resolution notes…"></textarea>
</div>
<button class="btn btn-primary btn-full" id="btn-confirm-resolve">Confirm</button>`);

  el('btn-confirm-resolve').addEventListener('click', async () => {
    const { ok, data } = await API.put(`/reputation/violations/${vid}/resolve`,
      { decision, notes: el('resolve-notes').value.trim() });
    showAlert(ok ? `Violation ${decision}.` : data.error,
              ok ? 'success' : 'error');
    if (ok) { closeModal(); renderAdminViolations(); }
  });
}

export function resolveAppeal(aid, decision) {
  showModal(`
<button class="modal-close">×</button>
<h3>${decision === 'upheld' ? 'Uphold' : 'Deny'} Appeal #${aid}</h3>
<div class="form-group">
  <label>Notes</label>
  <textarea id="appeal-notes" rows="3" placeholder="Review notes…"></textarea>
</div>
<button class="btn btn-primary btn-full" id="btn-confirm-appeal">Confirm</button>`);

  el('btn-confirm-appeal').addEventListener('click', async () => {
    const { ok, data } = await API.put(`/reputation/appeals/${aid}/resolve`,
      { decision, notes: el('appeal-notes').value.trim() });
    showAlert(ok ? `Appeal ${decision}.` : data.error,
              ok ? 'success' : 'error');
    if (ok) { closeModal(); renderAdminViolations(); }
  });
}

// ---- Admin Sessions -----------------------------------------------------

export async function renderAdminSessions(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const statusF = qs('#as-status')?.value || '';
  const { data } = await API.get('/admin/sessions',
    { status: statusF, page, per_page: 20 });

  vc.innerHTML = `
<div class="page-header"><h2>📋 All Sessions</h2></div>
<div class="filters">
  <div class="filter-group">
    <label>Status</label>
    <select id="as-status">
      <option value="">All</option>
      ${['pending','active','completed','cancelled'].map(s =>
        `<option ${s===statusF?'selected':''}>${s}</option>`).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-as-filter" style="align-self:flex-end">Filter</button>
</div>
<div class="table-wrap">
  <table>
    <thead><tr><th>#</th><th>Initiator</th><th>Participant</th><th>Status</th>
               <th>Credits</th><th>Duration</th><th>Created</th><th>Completed</th></tr></thead>
    <tbody>
      ${(data.sessions||[]).length
        ? data.sessions.map(s => `<tr>
            <td>#${s.id}</td>
            <td>${s.initiator}</td><td>${s.participant}</td>
            <td>${badge(s.status)}</td>
            <td>${s.credit_amount ?? 0}</td>
            <td>${s.duration_minutes ? s.duration_minutes + ' min' : '—'}</td>
            <td>${fmtDate(s.created_at)}</td>
            <td>${fmtDate(s.completed_at)}</td>
          </tr>`).join('')
        : `<tr><td colspan="8">${emptyHTML()}</td></tr>`}
    </tbody>
  </table>
</div>
${buildPagination(data.total||0, page, 20, p => renderAdminSessions(p))}`;

  el('btn-as-filter')?.addEventListener('click', () => renderAdminSessions(1));
}

// ---- Chain Verification -------------------------------------------------

export async function renderVerifyChains() {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML('Verifying chains…');

  const [ledger, audit] = await Promise.all([
    API.get('/ledger/verify'),
    API.get('/audit/logs/verify'),
  ]);

  const _card = (title, res) => `
  <div class="card">
    <div class="card-title">${title}</div>
    <div class="alert alert-${res.data?.valid ? 'success' : 'error'}">
      ${res.data?.valid ? '✔' : '✘'} ${res.data?.message || 'Unknown'}
      ${res.data?.entries != null ? ` — ${res.data.entries} entries` : ''}
    </div>
  </div>`;

  vc.innerHTML = `
<div class="page-header"><h2>🔗 Chain Integrity</h2></div>
${_card('Ledger Chain', ledger)}
${_card('Audit Log Chain', audit)}
<div class="card">
  <div class="card-title">How it works</div>
  <p style="font-size:.875rem;color:var(--c-text-sub);line-height:1.75">
    Each ledger entry and audit log entry stores a
    <code>SHA-256(own_data ‖ previous_hash)</code> hash, forming an immutable chain.
    Any modification to a past record invalidates all subsequent hashes,
    making tampering instantly detectable.
  </p>
</div>`;
}

// ---- Audit Logs ---------------------------------------------------------

export async function renderAuditLogs(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const action = qs('#al-action')?.value || '';
  const uid    = qs('#al-uid')?.value    || '';

  const { data } = await API.get('/audit/logs',
    { page, per_page: 50, action, user_id: uid });

  vc.innerHTML = `
<div class="page-header"><h2>📜 Audit Logs</h2></div>
<div class="filters">
  <div class="filter-group">
    <label>Action contains</label>
    <input id="al-action" type="text" placeholder="e.g. LOGIN, SESSION…" value="${action}">
  </div>
  <div class="filter-group">
    <label>User ID</label>
    <input id="al-uid" type="number" placeholder="e.g. 2" value="${uid}">
  </div>
  <button class="btn btn-secondary" id="btn-al-filter" style="align-self:flex-end">Filter</button>
</div>
<div class="table-wrap">
  <table>
    <thead><tr><th>#</th><th>User</th><th>Action</th><th>Entity</th>
               <th>Details</th><th>IP</th><th>Date</th><th title="SHA-256">Hash</th></tr></thead>
    <tbody>
      ${(data.logs||[]).length
        ? data.logs.map(l => `<tr>
            <td>${l.id}</td>
            <td>${l.username || l.user_id || '—'}</td>
            <td style="font-family:monospace;font-size:.78rem">${l.action}</td>
            <td>${l.entity_type || '—'}${l.entity_id ? ` #${l.entity_id}` : ''}</td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;font-size:.78rem">
              ${typeof l.details === 'object' ? JSON.stringify(l.details) : (l.details || '—')}
            </td>
            <td>${l.ip_address || '—'}</td>
            <td>${fmtDate(l.created_at)}</td>
            <td class="hash-cell" title="${l.log_hash}">${l.log_hash?.slice(0,10)}…</td>
          </tr>`).join('')
        : `<tr><td colspan="8">${emptyHTML()}</td></tr>`}
    </tbody>
  </table>
</div>
${buildPagination(data.total||0, page, 50, p => renderAuditLogs(p))}`;

  el('btn-al-filter')?.addEventListener('click', () => renderAuditLogs(1));
}
