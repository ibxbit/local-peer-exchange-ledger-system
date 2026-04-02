/**
 * verification.js — Identity verification submission and status UI.
 */

import { API } from './api.js';
import { el, badge, fmtDate, showAlert, loadingHTML } from './utils.js';

const DOC_TYPES = ['passport', 'national_id', 'drivers_license', 'utility_bill'];
const DOC_LABELS = {
  passport:        'Passport',
  national_id:     'National ID',
  drivers_license: "Driver's License",
  utility_bill:    'Utility Bill',
};

/** Renders the verification panel (used inside Profile view). */
export async function renderVerificationPanel(containerId) {
  const c = el(containerId);
  if (!c) return;
  c.innerHTML = loadingHTML('Checking verification status…');

  const { ok, data } = await API.get('/verification/status');
  if (!ok) { c.innerHTML = `<div class="alert alert-error">${data.error}</div>`; return; }

  const ver = data.verification;
  const status = data.status === 'not_submitted' ? 'not_submitted' : ver?.status;

  c.innerHTML = `
<div class="card">
  <div class="card-title">Identity Verification</div>
  ${_verificationContent(status, ver)}
</div>`;

  _attachVerificationHandlers(c, status);
}

function _verificationContent(status, ver) {
  if (status === 'not_submitted' || status === 'rejected') {
    const isResubmit = status === 'rejected';
    return `
${isResubmit ? `
  <div class="alert alert-error" style="margin-bottom:1rem">
    Your previous submission was <strong>rejected</strong>.
    ${ver?.notes ? `Reason: ${ver.notes}` : ''}
    You may submit new documents below.
  </div>` : `
  <p style="color:var(--c-text-sub);margin-bottom:1rem;font-size:.875rem">
    Submit a document to verify your identity on the platform.
    Your data is encrypted with AES-256 at rest.
  </p>`}
  <div class="step-bar">
    <div class="step active"><div class="step-num">1</div> <span>Submit</span></div>
    <div class="step-line"></div>
    <div class="step"><div class="step-num">2</div> <span>Review</span></div>
    <div class="step-line"></div>
    <div class="step"><div class="step-num">3</div> <span>Verified</span></div>
  </div>
  <form id="ver-form">
    <div class="form-group">
      <label>Document Type</label>
      <select id="ver-doc-type">
        ${DOC_TYPES.map(t => `<option value="${t}">${DOC_LABELS[t]}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>Document Reference / Number
        <small style="color:var(--c-text-sub)">(encrypted before storage)</small>
      </label>
      <input id="ver-doc-data" type="text"
             placeholder="e.g. passport number or document identifier">
    </div>
    <button class="btn btn-primary" type="submit" id="btn-ver-submit">
      Submit for Verification
    </button>
  </form>`;
  }

  if (status === 'pending') {
    return `
  <div class="ver-status">
    <div class="ver-icon pulse">⏳</div>
    <h3>Under Review</h3>
    <p style="color:var(--c-text-sub);font-size:.875rem">
      Submitted: ${fmtDate(ver?.submitted_at)}<br>
      An admin will review your document shortly.
    </p>
    ${badge('pending')}
  </div>`;
  }

  if (status === 'verified') {
    return `
  <div class="ver-status">
    <div class="ver-icon">✅</div>
    <h3 style="color:var(--c-success)">Verified</h3>
    <p style="color:var(--c-text-sub);font-size:.875rem">
      ${ver?.document_type}<br>
      Reviewed: ${fmtDate(ver?.reviewed_at)}
    </p>
    ${badge('verified')}
  </div>`;
  }

  return `<p>Status unknown.</p>`;
}

function _attachVerificationHandlers(container, status) {
  const form = container.querySelector('#ver-form');
  if (!form) return;

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = el('btn-ver-submit');
    btn.disabled = true;
    btn.textContent = 'Submitting…';

    const { ok, data } = await API.post('/verification/submit', {
      document_type: el('ver-doc-type').value,
      document_data: el('ver-doc-data').value.trim(),
    });

    btn.disabled = false;
    btn.textContent = 'Submit for Verification';

    if (!ok) { showAlert(data.error, 'error'); return; }
    showAlert('Verification submitted. An admin will review it shortly.', 'success');
    // Refresh the panel
    renderVerificationPanel(container.id || container.parentElement.id);
  });
}
