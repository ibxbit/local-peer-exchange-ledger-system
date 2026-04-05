/**
 * app.js — Entry point, shell rendering, client-side router.
 *
 * Module map
 * ──────────
 *   api.js          HTTP client + token management
 *   utils.js        Shared UI helpers
 *   auth.js         Login / Register
 *   dashboard.js    User dashboard
 *   matching.js     Peer search, sessions, queue + polling
 *   verification.js Identity verification panel
 *   ledger.js       Transaction history + transfer
 *   admin.js        Admin views (dashboard, users, violations, etc.)
 */

import { API }                          from './api.js';

// ---- HTMX: cookies are sent automatically (same-origin, SameSite=Strict).
// No Bearer header injection needed. The listener is retained as a no-op
// stub so any HTMX request that was given an explicit header elsewhere
// still works without error.
document.addEventListener('htmx:configRequest', evt => {
  // httpOnly cookie is forwarded by the browser automatically.
  // Nothing extra to do here.
});
import { renderAuth }                   from './auth.js';
import { renderDashboard }              from './dashboard.js';
import { renderMatching, renderSessions,
         openRequestSession, openRateSession,
         viewRepModal }                 from './matching.js';
import { renderVerificationPanel }      from './verification.js';
import { renderLedger }                 from './ledger.js';
import {
  renderAdminDashboard, renderAdminUsers,
  renderAdminVerifications, renderAdminViolations,
  renderAdminSessions, renderVerifyChains, renderAuditLogs,
  adminManageUser, adminCreditModal,
  reviewVerification, resolveViolation, resolveAppeal,
} from './admin.js';
import { el, qs, qsa, showAlert, showModal, closeModal,
         loadingHTML } from './utils.js';
import { API as _api } from './api.js';

// ---- Expose helpers on App global so onclick= attributes can call them ----
window.App = {
  navigate,
  openRequestSession,
  openRateSession,
  viewRepModal,
  adminManageUser,
  adminCreditModal,
  reviewVerification,
  resolveViolation,
  resolveAppeal,
};

// ---- Route table --------------------------------------------------------
const ROUTES = {
  'dashboard':           renderDashboard,
  'matching':            renderMatching,
  'sessions':            renderSessions,
  'profile':             renderProfile,
  'ledger':              renderLedger,
  'violations':          renderViolations,
  'ratings':             renderRatings,
  'admin-dashboard':     renderAdminDashboard,
  'admin-users':         renderAdminUsers,
  'admin-verifications': renderAdminVerifications,
  'admin-violations':    renderAdminViolations,
  'admin-sessions':      renderAdminSessions,
  'ledger-verify':       renderVerifyChains,
  'audit-logs':          renderAuditLogs,
};

// ---- Navigation ---------------------------------------------------------
export function navigate(view) {
  // Enforce role access
  const user = API.loadUser();
  const adminOnly  = ['admin-dashboard','admin-users','admin-verifications',
                      'admin-violations','admin-sessions'];
  const staffOnly  = ['audit-logs','ledger-verify'];
  if (adminOnly.includes(view) && user?.role !== 'admin') {
    showAlert('Admin access required.', 'error');
    return;
  }
  if (staffOnly.includes(view) && !['admin','auditor'].includes(user?.role)) {
    showAlert('Admin or Auditor access required.', 'error');
    return;
  }

  qsa('[data-view]').forEach(a =>
    a.classList.toggle('active', a.dataset.view === view));

  const fn = ROUTES[view];
  if (fn) fn();
  else el('view-container').innerHTML =
    `<div class="alert alert-error">View "${view}" not found.</div>`;
}

// ---- Profile view -------------------------------------------------------
async function renderProfile() {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const { ok, data: meData } = await API.get('/auth/me');
  if (!ok) { vc.innerHTML = `<div class="alert alert-error">${meData.error}</div>`; return; }
  const user = meData.user;

  const { data: profData } = await API.get('/matching/profile');
  const prof = profData.profile;

  const TIME_SLOTS = [
    'weekday-morning','weekday-afternoon','weekday-evening',
    'weekend-morning','weekend-afternoon','weekend-evening',
  ];
  const profSlots = prof?.preferred_time_slots ?? [];

  vc.innerHTML = `
<div class="page-header"><h2>👤 My Profile</h2></div>

<!-- Identity Verification -->
<div id="ver-panel"></div>

<!-- Matching Profile -->
<div class="card">
  <div class="card-title">Matching Profile</div>
  <form id="match-prof-form">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
      <div class="form-group">
        <label>Skills I Offer <small style="color:var(--c-text-sub)">(comma-separated)</small></label>
        <input id="mp-offered" type="text"
          value="${(prof?.skills_offered ?? []).join(', ')}">
      </div>
      <div class="form-group">
        <label>Skills I Need</label>
        <input id="mp-needed" type="text"
          value="${(prof?.skills_needed ?? []).join(', ')}">
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
      <div class="form-group">
        <label>Tags / Categories <small style="color:var(--c-text-sub)">(comma-separated)</small></label>
        <input id="mp-tags" type="text"
          placeholder="e.g. programming, design, music"
          value="${(prof?.tags ?? []).join(', ')}">
      </div>
      <div class="form-group">
        <label>Category</label>
        <input id="mp-category" type="text"
          placeholder="e.g. Technology, Arts, Languages"
          value="${prof?.category || ''}">
      </div>
    </div>
    <div class="form-group">
      <label>Preferred Time Slots</label>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.35rem">
        ${TIME_SLOTS.map(s => `
          <label style="display:flex;align-items:center;gap:.3rem;cursor:pointer">
            <input type="checkbox" name="mp-slots" value="${s}"
                   ${profSlots.includes(s) ? 'checked' : ''} style="width:auto">
            <span style="font-size:.85rem">${s}</span>
          </label>`).join('')}
      </div>
    </div>
    <div class="form-group">
      <label>Bio</label>
      <textarea id="mp-bio" rows="3">${prof?.bio || ''}</textarea>
    </div>
    <div class="form-group" style="display:flex;align-items:center;gap:.5rem">
      <input type="checkbox" id="mp-active" ${prof?.is_active !== 0 ? 'checked' : ''}
             style="width:auto">
      <label for="mp-active" style="margin:0;color:var(--c-text)">
        Appear in peer search
      </label>
    </div>
    <button class="btn btn-primary" type="submit">Save Matching Profile</button>
  </form>
</div>

<!-- Change Password -->
<div class="card">
  <div class="card-title">Change Password</div>
  <form id="pw-form">
    <div class="form-group">
      <label>Current Password</label>
      <input id="pw-current" type="password" autocomplete="current-password">
    </div>
    <div class="form-group">
      <label>New Password <small style="color:var(--c-text-sub)">(≥12 chars)</small></label>
      <input id="pw-new" type="password" autocomplete="new-password">
    </div>
    <button class="btn btn-secondary" type="submit">Update Password</button>
  </form>
</div>

<!-- Report Violation -->
<div class="card">
  <div class="card-title">Report a Peer Violation</div>
  <form id="report-form">
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.75rem">
      <div class="form-group">
        <label>User ID</label>
        <input id="rv-uid" type="number" min="1" placeholder="ID">
      </div>
      <div class="form-group">
        <label>Type</label>
        <select id="rv-type">
          ${['spam','harassment','fraud','no_show','abuse','other']
            .map(t => `<option>${t}</option>`).join('')}
        </select>
      </div>
      <div class="form-group">
        <label>Severity</label>
        <select id="rv-sev">
          <option>low</option><option>medium</option><option>high</option>
        </select>
      </div>
    </div>
    <div class="form-group">
      <label>Description</label>
      <textarea id="rv-desc" rows="2" placeholder="Describe the violation…"></textarea>
    </div>
    <button class="btn btn-warn" type="submit">Submit Report</button>
  </form>
</div>`;

  // Load verification panel
  await renderVerificationPanel('ver-panel');

  // Matching profile form
  el('match-prof-form').addEventListener('submit', async e => {
    e.preventDefault();
    const checkedSlots = [...document.querySelectorAll('input[name="mp-slots"]:checked')]
      .map(cb => cb.value);
    const { ok, data } = await API.post('/matching/profile', {
      skills_offered:       el('mp-offered').value.split(',').map(s=>s.trim()).filter(Boolean),
      skills_needed:        el('mp-needed').value.split(',').map(s=>s.trim()).filter(Boolean),
      tags:                 el('mp-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
      category:             el('mp-category').value.trim(),
      preferred_time_slots: checkedSlots,
      bio:                  el('mp-bio').value,
      is_active:            el('mp-active').checked,
    });
    showAlert(ok ? 'Matching profile saved.' : data.error,
              ok ? 'success' : 'error');
  });

  // Password form
  el('pw-form').addEventListener('submit', async e => {
    e.preventDefault();
    const { ok, data } = await API.post('/auth/change-password', {
      current_password: el('pw-current').value,
      new_password:     el('pw-new').value,
    });
    showAlert(ok ? 'Password updated.' : data.error, ok ? 'success' : 'error');
    if (ok) { el('pw-current').value = ''; el('pw-new').value = ''; }
  });

  // Report form
  el('report-form').addEventListener('submit', async e => {
    e.preventDefault();
    const { ok, data } = await API.post('/reputation/violations', {
      user_id:        +el('rv-uid').value,
      violation_type: el('rv-type').value,
      severity:       el('rv-sev').value,
      description:    el('rv-desc').value.trim(),
    });
    showAlert(ok ? 'Report submitted.' : data.error, ok ? 'success' : 'error');
    if (ok) el('report-form').reset();
  });
}

// ---- Violations (user view) ---------------------------------------------
async function renderViolations(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const { data } = await API.get('/reputation/violations',
    { page, per_page: 20 });
  const { violations = [], total = 0 } = data;

  const { badge, fmtDate, buildPagination, emptyHTML } = await import('./utils.js');

  vc.innerHTML = `
<div class="page-header"><h2>⚑ Violations</h2></div>
<div class="table-wrap"><table>
  <thead><tr><th>#</th><th>Type</th><th>Against</th><th>Reporter</th>
             <th>Severity</th><th>Status</th><th>Date</th><th>Appeal</th></tr></thead>
  <tbody>
    ${violations.length
      ? violations.map(v => `<tr>
          <td>#${v.id}</td>
          <td>${v.violation_type}</td>
          <td>${v.target_username||'—'}</td>
          <td>${v.reporter_username||'—'}</td>
          <td>${badge(v.severity)}</td>
          <td>${badge(v.status)}</td>
          <td>${fmtDate(v.created_at)}</td>
          <td>${v.user_id === API.loadUser()?.id && v.status === 'open' ? `
            <button class="btn btn-sm btn-secondary"
              onclick="App._openAppeal(${v.id})">Appeal</button>
          ` : '—'}</td>
        </tr>`).join('')
      : `<tr><td colspan="8">${emptyHTML('No violations.')}</td></tr>`}
  </tbody>
</table></div>
${buildPagination(total, page, 20, p => renderViolations(p))}`;

  window.App._openAppeal = (vid) => {
    showModal(`
<button class="modal-close">×</button>
<h3>Appeal Violation #${vid}</h3>
<div class="form-group">
  <label>Reason for Appeal</label>
  <textarea id="appeal-reason" rows="4"
    placeholder="Explain why you believe this violation is incorrect…"></textarea>
</div>
<button class="btn btn-primary btn-full" id="btn-submit-appeal">Submit Appeal</button>`);

    el('btn-submit-appeal').addEventListener('click', async () => {
      const reason = el('appeal-reason').value.trim();
      if (!reason) { showAlert('Reason is required.', 'warn'); return; }
      const { ok, data } = await API.post(
        `/reputation/violations/${vid}/appeal`, { reason });
      showAlert(ok ? 'Appeal submitted.' : data.error,
                ok ? 'success' : 'error');
      if (ok) { closeModal(); renderViolations(); }
    });
  };
}

// ---- Ratings (user view) ------------------------------------------------
async function renderRatings(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const uid = API.loadUser()?.id;
  const [scoreRes, ratingsRes] = await Promise.all([
    API.get(`/reputation/score/${uid}`),
    API.get(`/reputation/ratings/${uid}`, { page, per_page: 10 }),
  ]);

  const score   = scoreRes.data   || {};
  const ratings = ratingsRes.data?.ratings || [];
  const total   = ratingsRes.data?.total   || 0;

  const { stars, badge, fmtDate, buildPagination, emptyHTML } = await import('./utils.js');

  vc.innerHTML = `
<div class="page-header"><h2>⭐ Ratings & Reputation</h2></div>
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">Reputation Score</div>
    <div class="stat-value">${score.reputation_score ?? '—'}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Average Rating</div>
    <div class="stat-value">${score.average_rating || '—'}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Total Ratings</div>
    <div class="stat-value">${score.total_ratings ?? 0}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Positive (4–5★)</div>
    <div class="stat-value" style="color:var(--c-success)">
      ${score.positive_ratings ?? 0}
    </div>
  </div>
</div>
<div class="card">
  <div class="card-title">Received Ratings</div>
  <div class="table-wrap"><table>
    <thead><tr><th>From</th><th>Score</th><th>Comment</th><th>Date</th></tr></thead>
    <tbody>
      ${ratings.length
        ? ratings.map(r => `<tr>
            <td>${r.rater_name}</td>
            <td>${stars(r.score)}</td>
            <td>${r.comment || '—'}</td>
            <td>${fmtDate(r.created_at)}</td>
          </tr>`).join('')
        : `<tr><td colspan="4">${emptyHTML('No ratings received yet.')}</td></tr>`}
    </tbody>
  </table></div>
  ${buildPagination(total, page, 10, p => renderRatings(p))}
</div>`;
}

// ---- App shell ----------------------------------------------------------
function renderAppShell(user) {
  const role = user.role;

  const adminLinks = role === 'admin' ? `
    <div class="sep"></div>
    <a href="#" data-view="admin-dashboard">⚙ Admin Dashboard</a>
    <a href="#" data-view="admin-users">👥 User Mgmt</a>
    <a href="#" data-view="admin-verifications">✔ Verifications</a>
    <a href="#" data-view="admin-violations">⚑ Violations</a>
    <a href="#" data-view="admin-sessions">📋 All Sessions</a>` : '';

  const staffLinks = (role === 'admin' || role === 'auditor') ? `
    <div class="sep"></div>
    <a href="#" data-view="audit-logs">📜 Audit Logs</a>
    <a href="#" data-view="ledger-verify">🔗 Verify Chains</a>` : '';

  document.body.innerHTML = `
<div id="app">
  <nav class="navbar">
    <span class="brand">⇄ PeerExchange</span>
    <div class="nav-right">
      <span class="user-chip">${user.username}</span>
      <span class="role-badge ${role}">${role}</span>
      <button class="btn btn-sm btn-secondary" id="btn-logout">Logout</button>
    </div>
  </nav>
  <div class="layout">
    <aside class="sidebar">
      <a href="#" data-view="dashboard">🏠 Dashboard</a>
      <a href="#" data-view="profile">👤 Profile</a>
      <a href="#" data-view="matching">🔍 Find Peers</a>
      <a href="#" data-view="sessions">📅 Sessions</a>
      <a href="#" data-view="ratings">⭐ Ratings</a>
      <a href="#" data-view="ledger">💳 Ledger</a>
      <a href="#" data-view="violations">⚑ Violations</a>
      ${adminLinks}
      ${staffLinks}
    </aside>
    <main class="main">
      <div id="main-alert"></div>
      <div id="view-container">${loadingHTML()}</div>
    </main>
  </div>
</div>`;

  el('btn-logout').addEventListener('click', async () => {
    // POST /auth/logout clears the httpOnly cookie server-side.
    // API.logout() also clears sessionStorage user data, preventing role leakage.
    await API.logout();
    boot();
  });

  qsa('[data-view]').forEach(a =>
    a.addEventListener('click', e => {
      e.preventDefault();
      navigate(a.dataset.view);
    })
  );

  navigate('dashboard');
}

// ---- Boot ---------------------------------------------------------------
async function boot() {
  // Token lives in an httpOnly cookie — not readable by JS.
  // Probe /auth/me directly: the browser sends the cookie automatically.
  // 200 → already authenticated; 401 → not logged in or cookie expired.
  const { ok, data } = await API.get('/auth/me');
  if (!ok) {
    // Clear any stale user data to prevent role leakage when switching users.
    API.clearUser();
    renderAuth(user => { API.saveUser(user); renderAppShell(user); });
    return;
  }
  const user = data.user;
  API.saveUser(user);
  renderAppShell(user);
}

boot();
