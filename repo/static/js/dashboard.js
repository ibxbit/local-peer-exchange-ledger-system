/**
 * dashboard.js — User dashboard view.
 */

import { API } from './api.js';
import { el, stars, fmtDate, loadingHTML } from './utils.js';

export async function renderDashboard() {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const [meRes, repRes] = await Promise.all([
    API.get('/auth/me'),
    API.get(`/reputation/score/${API.loadUser()?.id}`),
  ]);

  if (!meRes.ok) { vc.innerHTML = `<div class="alert alert-error">${meRes.data.error}</div>`; return; }

  const user = meRes.data.user;
  const rep  = repRes.data || {};

  // Refresh cached user
  API.saveUser({ ...API.loadUser(), ...user });

  vc.innerHTML = `
<div class="page-header"><h2>🏠 Dashboard</h2></div>

<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">Credit Balance</div>
    <div class="stat-value">${(user.credit_balance || 0).toFixed(2)}</div>
    <div class="stat-sub">Available credits</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Reputation Score</div>
    <div class="stat-value ${rep.reputation_score > 0 ? '' : 'dim'}">${rep.reputation_score ?? '—'}</div>
    <div class="stat-sub">${rep.average_rating ? `★ ${rep.average_rating}` : 'No ratings yet'}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Sessions Completed</div>
    <div class="stat-value">${rep.sessions_completed ?? 0}</div>
    <div class="stat-sub">${rep.positive_ratings ?? 0} positive ratings</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Open Disputes</div>
    <div class="stat-value" style="color:${(rep.violations_against??0)>0?'var(--c-danger)':'inherit'}">
      ${rep.violations_against ?? 0}
    </div>
    <div class="stat-sub">${(rep.violations_against??0)>3 ? '⚠ Actions blocked' : 'All clear'}</div>
  </div>
</div>

${(user.credit_balance ?? 0) < 60 ? `
<div class="alert alert-warn">
  ⚠ Your balance (${(user.credit_balance||0).toFixed(2)}) is below 60 credits.
  You cannot initiate sessions or join the queue until your balance is topped up.
</div>` : ''}

<div class="card">
  <div class="card-title">Account</div>
  <table style="width:auto;border:none">
    ${_row('Username',    user.username)}
    ${_row('Email',       user.email)}
    ${_row('Role',        `<span class="role-badge ${user.role}">${user.role}</span>`)}
    ${_row('Member since', fmtDate(user.created_at))}
    ${_row('Status',      user.is_active
      ? '<span class="badge badge-active">Active</span>'
      : '<span class="badge badge-cancelled">Inactive</span>')}
  </table>
</div>

<div class="card">
  <div class="card-title">Quick Actions</div>
  <div style="display:flex;gap:.75rem;flex-wrap:wrap">
    <button class="btn btn-primary"   onclick="App.navigate('matching')">🔍 Find Peers</button>
    <button class="btn btn-secondary" onclick="App.navigate('sessions')">📅 My Sessions</button>
    <button class="btn btn-secondary" onclick="App.navigate('ledger')">💳 Ledger</button>
    <button class="btn btn-secondary" onclick="App.navigate('profile')">👤 Profile</button>
  </div>
</div>`;
}

function _row(label, value) {
  return `<tr>
    <td style="color:var(--c-text-sub);padding:.4rem 2rem .4rem 0;border:none">${label}</td>
    <td style="border:none">${value}</td>
  </tr>`;
}
