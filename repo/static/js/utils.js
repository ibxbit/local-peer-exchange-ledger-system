/**
 * utils.js — Shared UI helpers.
 */

// ---- DOM shortcuts -----------------------------------------------------
export const el  = id  => document.getElementById(id);
export const qs  = sel => document.querySelector(sel);
export const qsa = sel => document.querySelectorAll(sel);

// ---- Formatting --------------------------------------------------------
export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}

export function fmtBalance(n) {
  return typeof n === 'number' ? n.toFixed(2) : '—';
}

/**
 * Mask an email: "alice@example.com" → "al***@example.com"
 * (Server already masks, but we apply client-side too for belt-and-suspenders.)
 */
export function maskEmail(email) {
  if (!email || !email.includes('@')) return '***';
  const [local, domain] = email.split('@');
  const visible = local.slice(0, Math.min(2, local.length));
  return `${visible}***@${domain}`;
}

// ---- Badge & stars -----------------------------------------------------
export function badge(status, label) {
  const s = (status || '').toLowerCase().replace(/\s/g, '_');
  return `<span class="badge badge-${s}">${label || status || '—'}</span>`;
}

export function stars(score, total = 5) {
  if (score == null || score === '') return '<span class="stars">—</span>';
  const n    = Math.round(+score);
  const full = '★'.repeat(Math.min(n, total));
  const empty= '☆'.repeat(Math.max(0, total - n));
  return `<span class="stars">${full}${empty}</span> <small>${(+score).toFixed(1)}</small>`;
}

// ---- Alert banner ------------------------------------------------------
let _alertTimer = null;
export function showAlert(html, type = 'info', containerId = 'main-alert') {
  const c = el(containerId);
  if (!c) return;
  if (_alertTimer) clearTimeout(_alertTimer);
  c.innerHTML = `<div class="alert alert-${type}">${html}</div>`;
  _alertTimer = setTimeout(() => { c.innerHTML = ''; }, 5500);
}

// ---- Modal -------------------------------------------------------------
export function showModal(html, onClose) {
  let overlay = el('modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'modal-overlay';
    overlay.className = 'modal-overlay';
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `<div class="modal" role="dialog">${html}</div>`;
  overlay.style.display = 'flex';

  const closeBtn = overlay.querySelector('.modal-close');
  const doClose  = () => { closeModal(); onClose?.(); };
  closeBtn?.addEventListener('click', doClose);
  overlay.addEventListener('click', e => { if (e.target === overlay) doClose(); });
}

export function closeModal() {
  const o = el('modal-overlay');
  if (o) o.style.display = 'none';
}

// ---- Pagination control ------------------------------------------------
export function buildPagination(total, page, perPage, onPage) {
  const totalPages = Math.ceil(total / perPage);
  if (totalPages <= 1) return '';
  const prevDis = page <= 1 ? 'disabled' : '';
  const nextDis = page >= totalPages ? 'disabled' : '';
  const wrap = `
    <div class="pagination">
      <span class="pg-info">Page ${page} of ${totalPages} &nbsp;(${total} total)</span>
      <button class="btn btn-sm btn-secondary" ${prevDis} data-pg="${page-1}">‹ Prev</button>
      <button class="btn btn-sm btn-secondary" ${nextDis} data-pg="${page+1}">Next ›</button>
    </div>`;
  // Attach after next tick (element must be in DOM)
  setTimeout(() => {
    qsa('[data-pg]').forEach(b => {
      if (!b.disabled) b.addEventListener('click', () => onPage(+b.dataset.pg));
    });
  }, 0);
  return wrap;
}

// ---- Star-rating input widget ------------------------------------------
export function buildStarInput(name = 'score', defaultVal = 0) {
  const stars = [5, 4, 3, 2, 1];
  const radios = stars.map(n => `
    <input type="radio" name="${name}" id="${name}_${n}" value="${n}"
           ${n === defaultVal ? 'checked' : ''}>
    <label for="${name}_${n}" title="${n} star${n > 1 ? 's' : ''}" data-v="${n}">★</label>
  `).join('');
  return `<div class="stars-input" id="${name}-widget">${radios}</div>`;
}

/** Read the selected star value from a star widget */
export function readStarInput(name = 'score') {
  const checked = qs(`input[name="${name}"]:checked`);
  return checked ? +checked.value : null;
}

// ---- Loading placeholder -----------------------------------------------
export function loadingHTML(msg = 'Loading…') {
  return `<div class="loader"><div class="spinner"></div><span>${msg}</span></div>`;
}

// ---- Empty state -------------------------------------------------------
export function emptyHTML(msg = 'No results found.') {
  return `<p style="color:var(--c-text-sub);text-align:center;padding:2rem">${msg}</p>`;
}
