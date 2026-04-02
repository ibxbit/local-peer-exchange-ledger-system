/**
 * matching.js — Peer search, session lifecycle, and queue with live polling.
 *
 * Match States
 * ────────────
 *   searching  → polling every 10 s, spinner shown
 *   found      → matched! session link displayed, polling stopped
 *   retrying   → entry expired/cancelled, user offered to rejoin
 */

import { API } from './api.js';
import { el, qs, qsa, badge, stars, fmtDate,
         showAlert, showModal, closeModal,
         buildPagination, loadingHTML, emptyHTML } from './utils.js';

// ---- HTMX queue status widget -------------------------------------------
// After joining the queue we mount a div with HTMX polling attributes.
// The server controls when polling stops by omitting hx-trigger in the
// matched/expired/cancelled states.

let _activeEntryId = null;  // track so we can restore widget on page re-render

function _mountQueueStatusWidget(entryId, skill) {
  _activeEntryId = entryId;
  const activeDiv = el('active-match-status');
  if (!activeDiv) return;

  // Build a div that HTMX will poll immediately and every 10 s
  const card = document.createElement('div');
  card.id        = 'match-status-card';
  card.className = 'match-status-card state-searching';
  card.setAttribute('hx-get', `/api/matching/queue/${entryId}/status-partial`);
  card.setAttribute('hx-trigger', 'load, every 10s');
  card.setAttribute('hx-target', '#match-status-card');
  card.setAttribute('hx-swap', 'outerHTML');
  card.innerHTML = `
    <div class="state-icon pulse"><div class="spinner" style="margin:0 auto"></div></div>
    <h3 style="color:var(--c-accent);margin-top:.5rem">Searching…</h3>
    <p>Looking for a peer who offers <strong>${_escHtml(skill)}</strong>.</p>
    <small style="color:var(--c-text-sub)">Polls every 10 s via HTMX</small>`;

  activeDiv.innerHTML = '';
  activeDiv.appendChild(card);

  // Initialise HTMX on the dynamically added element
  if (window.htmx) window.htmx.process(card);
}

// ---- Peer Search --------------------------------------------------------

export async function renderMatching(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  vc.innerHTML = `
<div class="page-header">
  <h2>🔍 Find Peers</h2>
  <button class="btn btn-primary" id="btn-open-queue">⏳ Join Match Queue</button>
</div>

<!-- Active match status (shown when HTMX-polling queue) -->
<div id="active-match-status"></div>

<!-- Search filters — HTMX live search on the skill input -->
<div class="filters">
  <div class="filter-group">
    <label>Filter by skill</label>
    <input id="search-skill" name="skill" type="text"
           placeholder="e.g. Python, design…"
           hx-get="/api/matching/peers-partial"
           hx-trigger="input changed delay:400ms, search"
           hx-target="#peer-list"
           hx-indicator="#search-spinner">
  </div>
  <div class="filter-group">
    <label>Tag / Category</label>
    <input id="search-tag" name="tag" type="text"
           placeholder="e.g. programming, arts…"
           hx-get="/api/matching/peers-partial"
           hx-trigger="input changed delay:400ms"
           hx-target="#peer-list"
           hx-include="#search-skill,#search-tag">
  </div>
  <button class="btn btn-primary" id="btn-search" style="align-self:flex-end"
          hx-get="/api/matching/peers-partial"
          hx-target="#peer-list"
          hx-include="#search-skill,#search-tag">
    Search
  </button>
  <span id="search-spinner" class="htmx-indicator" style="align-self:center">
    <div class="spinner" style="width:1.2rem;height:1.2rem"></div>
  </span>
</div>

<!-- Results — initially loaded via HTMX, replaced on search -->
<div id="peer-list"
     hx-get="/api/matching/peers-partial"
     hx-trigger="load"
     hx-indicator="#search-spinner">
  ${loadingHTML('Loading peers…')}
</div>`;

  el('btn-open-queue').addEventListener('click', () => _openQueueModal());

  // Initialise HTMX on dynamically created content
  if (window.htmx) window.htmx.process(vc);

  // Restore active HTMX poll widget if a queue entry is active
  if (_activeEntryId) {
    _mountQueueStatusWidget(_activeEntryId, '');
  }
}

function _peerCard(p) {
  return `
<div class="card card-row">
  <div>
    <strong>${_escHtml(p.username)}</strong>
    <div style="margin-top:.35rem;font-size:.8rem;color:var(--c-text-sub)">
      Offers: <span style="color:var(--c-text)">${_escHtml(p.skills_offered.join(', ')) || '—'}</span>
    </div>
    <div style="font-size:.8rem;color:var(--c-text-sub)">
      Needs: <span style="color:var(--c-text)">${_escHtml(p.skills_needed.join(', ')) || '—'}</span>
    </div>
    ${p.bio ? `<div style="font-size:.8rem;margin-top:.25rem">${_escHtml(p.bio)}</div>` : ''}
  </div>
  <div style="display:flex;gap:.5rem;flex-wrap:wrap">
    <button class="btn btn-sm btn-secondary" onclick="App.viewRepModal(${p.user_id})">
      View Rep
    </button>
    <button class="btn btn-sm btn-primary"
            onclick="App.openRequestSession(${p.user_id},'${_escHtml(p.username)}')">
      Request Session
    </button>
  </div>
</div>`;
}

// ---- Queue modal --------------------------------------------------------
function _openQueueModal() {
  showModal(`
<button class="modal-close">×</button>
<h3>Join Auto-Match Queue</h3>
<p style="font-size:.875rem;color:var(--c-text-sub);margin-bottom:1rem">
  We'll find a peer who offers the skill you need.
  You'll be notified here every 10 seconds.
</p>
<form id="queue-form">
  <div class="form-group">
    <label>Skill you're seeking</label>
    <input id="q-skill" type="text" placeholder="e.g. Python, UX design…" required>
  </div>
  <div class="form-group">
    <label>Priority <small style="color:var(--c-text-sub)">(higher = matched first)</small></label>
    <select id="q-priority">
      <option value="0">Normal</option>
      <option value="5">High</option>
      <option value="10">Urgent</option>
    </select>
  </div>
  <button class="btn btn-primary btn-full" type="submit">Join Queue</button>
</form>`);

  el('queue-form').addEventListener('submit', async e => {
    e.preventDefault();
    const skill = el('q-skill').value.trim();
    if (!skill) return;

    const { ok, data } = await API.post('/matching/queue', {
      skill, priority: +el('q-priority').value,
    });
    if (!ok) { showAlert(data.error, 'error'); return; }

    closeModal();
    showAlert(`Added to queue for "${skill}". Polling every 10 s via HTMX…`, 'info');

    // Mount HTMX-polling status card (replaces old JS polling)
    _mountQueueStatusWidget(data.entry_id, skill);
  });
}

// ---- Request Session modal ----------------------------------------------
export function openRequestSession(participantId, username) {
  showModal(`
<button class="modal-close">×</button>
<h3>Request Session with ${_escHtml(username)}</h3>
<form id="req-sess-form">
  <div class="form-group">
    <label>Topic / Description</label>
    <textarea id="rs-desc" placeholder="What would you like to exchange?"
              rows="3"></textarea>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
    <div class="form-group">
      <label>Duration (min)</label>
      <input id="rs-dur" type="number" min="5" max="480" placeholder="60">
    </div>
    <div class="form-group">
      <label>Credit Offer</label>
      <input id="rs-cred" type="number" min="0" step="0.01" placeholder="0.00">
    </div>
  </div>
  <div class="form-group">
    <label>Scheduled At <small style="color:var(--c-text-sub)">(optional)</small></label>
    <input id="rs-sched" type="datetime-local">
  </div>
  <button class="btn btn-primary btn-full" type="submit">Send Request</button>
</form>`);

  el('req-sess-form').addEventListener('submit', async e => {
    e.preventDefault();
    const { ok, data } = await API.post('/matching/sessions', {
      participant_id:   participantId,
      description:      el('rs-desc').value.trim(),
      duration_minutes: el('rs-dur').value  ? +el('rs-dur').value  : null,
      credit_amount:    el('rs-cred').value ? +el('rs-cred').value : 0,
      scheduled_at:     el('rs-sched').value || null,
    }, { idempotencyKey: API.idemKey() });

    if (!ok) { showAlert(data.error, 'error'); return; }
    showAlert('Session request sent!', 'success');
    closeModal();
  });
}

// ---- Sessions list ------------------------------------------------------
export async function renderSessions(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const statusF = qs('#sess-status')?.value || '';
  const roleF   = qs('#sess-role')?.value   || 'all';

  const { data } = await API.get('/matching/sessions',
    { page, per_page: 15, status: statusF, role: roleF });

  vc.innerHTML = `
<div class="page-header"><h2>📅 My Sessions</h2></div>
<div class="filters">
  <div class="filter-group">
    <label>Role</label>
    <select id="sess-role">
      ${['all','initiator','participant'].map(r =>
        `<option value="${r}" ${r === roleF ? 'selected' : ''}>${r}</option>`
      ).join('')}
    </select>
  </div>
  <div class="filter-group">
    <label>Status</label>
    <select id="sess-status">
      <option value="">All</option>
      ${['pending','active','completed','cancelled'].map(s =>
        `<option ${s === statusF ? 'selected' : ''}>${s}</option>`
      ).join('')}
    </select>
  </div>
  <button class="btn btn-secondary" id="btn-filter-sess" style="align-self:flex-end">Apply</button>
</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr><th>#</th><th>Peer</th><th>Role</th><th>Status</th>
          <th>Credits</th><th>Scheduled</th><th>Actions</th></tr>
    </thead>
    <tbody>
      ${(data.sessions || []).length
        ? data.sessions.map(_sessionRow).join('')
        : `<tr><td colspan="7">${emptyHTML('No sessions.')}</td></tr>`}
    </tbody>
  </table>
</div>
${buildPagination(data.total || 0, page, 15, p => renderSessions(p))}`;

  el('btn-filter-sess').addEventListener('click', () => renderSessions(1));
  qsa('.sess-action').forEach(b =>
    b.addEventListener('click', () =>
      _updateSession(+b.dataset.id, b.dataset.status)));
}

function _sessionRow(s) {
  const user    = API.loadUser();
  const isInit  = s.initiator_id === user?.id;
  const other   = isInit ? s.participant_name : s.initiator_name;
  return `<tr>
    <td>#${s.id}</td>
    <td>${_escHtml(other)}</td>
    <td><small>${isInit ? 'Initiator' : 'Participant'}</small></td>
    <td>${badge(s.status)}</td>
    <td>${s.credit_amount ?? 0}</td>
    <td>${fmtDate(s.scheduled_at)}</td>
    <td>${_sessionActions(s)}</td>
  </tr>`;
}

function _sessionActions(s) {
  if (s.status === 'pending') return `
    <button class="btn btn-sm btn-success sess-action"  data-id="${s.id}" data-status="active">Accept</button>
    <button class="btn btn-sm btn-danger  sess-action"  data-id="${s.id}" data-status="cancelled">Cancel</button>`;
  if (s.status === 'active')  return `
    <button class="btn btn-sm btn-primary sess-action"  data-id="${s.id}" data-status="completed">Complete</button>
    <button class="btn btn-sm btn-danger  sess-action"  data-id="${s.id}" data-status="cancelled">Cancel</button>`;
  if (s.status === 'completed') return `
    <button class="btn btn-sm btn-warn" onclick="App.openRateSession(${s.id})">Rate</button>`;
  return '—';
}

async function _updateSession(sessionId, newStatus) {
  const { ok, data } = await API.put(`/matching/sessions/${sessionId}`,
    { status: newStatus });
  showAlert(ok ? `Session marked as ${newStatus}.` : data.error,
            ok ? 'success' : 'error');
  if (ok) renderSessions();
}

// ---- Rate session modal -------------------------------------------------
export function openRateSession(sessionId) {
  showModal(`
<button class="modal-close">×</button>
<h3>Rate Session #${sessionId}</h3>
<form id="rate-form">
  <div class="form-group">
    <label>Score</label>
    <div class="stars-input" id="rate-stars">
      ${[5,4,3,2,1].map(n => `
        <input type="radio" name="score" id="sc_${n}" value="${n}">
        <label for="sc_${n}" title="${n} star${n>1?'s':''}">★</label>`).join('')}
    </div>
  </div>
  <div class="form-group">
    <label>Comment <small style="color:var(--c-text-sub)">(optional)</small></label>
    <textarea id="rate-comment" rows="3"
              placeholder="Share your experience…"></textarea>
  </div>
  <button class="btn btn-primary btn-full" type="submit">Submit Rating</button>
</form>`);

  // Highlight stars on hover
  const labels = document.querySelectorAll('#rate-stars label');
  labels.forEach(lbl => {
    lbl.addEventListener('mouseenter', () => {
      const v = +lbl.getAttribute('for').replace('sc_', '');
      labels.forEach(l => l.classList.toggle('active', +l.getAttribute('for').replace('sc_','') >= v));
    });
  });
  el('rate-stars').addEventListener('mouseleave', () => {
    labels.forEach(l => l.classList.remove('active'));
  });

  el('rate-form').addEventListener('submit', async e => {
    e.preventDefault();
    const score = document.querySelector('#rate-stars input:checked');
    if (!score) { showAlert('Please select a star rating.', 'warn'); return; }
    const { ok, data } = await API.post('/reputation/rate', {
      session_id: sessionId,
      score:      +score.value,
      comment:    el('rate-comment').value.trim(),
    });
    showAlert(ok ? 'Rating submitted!' : data.error,
              ok ? 'success' : 'error');
    if (ok) closeModal();
  });
}

// ---- Reputation preview modal -------------------------------------------
export async function viewRepModal(userId) {
  const { data } = await API.get(`/reputation/score/${userId}`);
  showModal(`
<button class="modal-close">×</button>
<h3>Reputation: ${_escHtml(data.username || String(userId))}</h3>
<div class="stat-grid" style="grid-template-columns:repeat(2,1fr);margin-top:.75rem">
  <div class="stat-card"><div class="stat-label">Score</div>
    <div class="stat-value">${data.reputation_score ?? '—'}</div></div>
  <div class="stat-card"><div class="stat-label">Avg Rating</div>
    <div class="stat-value">${data.average_rating || '—'}</div></div>
  <div class="stat-card"><div class="stat-label">Sessions</div>
    <div class="stat-value">${data.sessions_completed}</div></div>
  <div class="stat-card"><div class="stat-label">Open Disputes</div>
    <div class="stat-value" style="color:${data.violations_against > 0 ? 'var(--c-danger)' : 'inherit'}">
      ${data.violations_against}</div></div>
</div>`);
}

// ---- Private helpers ----------------------------------------------------
function _escHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
