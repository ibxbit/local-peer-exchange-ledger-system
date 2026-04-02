/**
 * ledger.js — Transaction history, transfer modal, chain verification.
 */

import { API } from './api.js';
import { el, badge, fmtDate, showAlert, showModal, closeModal,
         buildPagination, loadingHTML, emptyHTML } from './utils.js';

const CREDIT_TYPES = new Set(['credit', 'transfer_in', 'refund']);

export async function renderLedger(page = 1) {
  const vc = el('view-container');
  vc.innerHTML = loadingHTML();

  const [balRes, ledRes] = await Promise.all([
    API.get('/ledger/balance'),
    API.get('/ledger', { page, per_page: 20 }),
  ]);

  const balance  = balRes.data?.balance ?? 0;
  const entries  = ledRes.data?.entries  || [];
  const total    = ledRes.data?.total    || 0;

  vc.innerHTML = `
<div class="page-header">
  <h2>💳 My Ledger</h2>
  <button class="btn btn-primary" id="btn-transfer">⇄ Transfer Credits</button>
</div>

<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">Current Balance</div>
    <div class="stat-value">${balance.toFixed(2)}</div>
    <div class="stat-sub">Available credits</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Total Transactions</div>
    <div class="stat-value">${total}</div>
  </div>
</div>

${balance < 60 ? `
<div class="alert alert-warn">
  ⚠ Balance below 60 — session and queue actions are blocked.
</div>` : ''}

<div class="card">
  <div class="card-title">Transaction History</div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Type</th><th>Amount</th><th>Balance After</th>
        <th>Description</th><th>Date</th>
      </tr></thead>
      <tbody>
        ${entries.length
          ? entries.map(_ledgerRow).join('')
          : `<tr><td colspan="5">${emptyHTML('No transactions yet.')}</td></tr>`}
      </tbody>
    </table>
  </div>
  ${buildPagination(total, page, 20, p => renderLedger(p))}
</div>`;

  el('btn-transfer').addEventListener('click', _openTransferModal);
}

function _ledgerRow(e) {
  const isCredit = CREDIT_TYPES.has(e.transaction_type);
  const sign     = isCredit ? '+' : '−';
  const colour   = isCredit ? 'var(--c-success)' : 'var(--c-danger)';
  return `<tr>
    <td>${badge(e.transaction_type)}</td>
    <td style="color:${colour};font-weight:600">${sign}${(+e.amount).toFixed(2)}</td>
    <td>${(+e.balance_after).toFixed(2)}</td>
    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
      ${e.description || '—'}
    </td>
    <td>${fmtDate(e.created_at)}</td>
  </tr>`;
}

function _openTransferModal() {
  showModal(`
<button class="modal-close">×</button>
<h3>Transfer Credits</h3>
<p style="font-size:.875rem;color:var(--c-text-sub);margin-bottom:1rem">
  You must have ≥ 60 credits remaining after the transfer to continue using the platform.
</p>
<form id="transfer-form">
  <div class="form-group">
    <label>Recipient User ID</label>
    <input id="tf-to" type="number" min="1" placeholder="User ID">
  </div>
  <div class="form-group">
    <label>Amount</label>
    <input id="tf-amt" type="number" min="0.01" step="0.01" placeholder="0.00">
  </div>
  <div class="form-group">
    <label>Note <small style="color:var(--c-text-sub)">(optional)</small></label>
    <input id="tf-note" type="text" placeholder="e.g. payment for session…">
  </div>
  <button class="btn btn-primary btn-full" type="submit">Transfer</button>
</form>`);

  el('transfer-form').addEventListener('submit', async e => {
    e.preventDefault();
    const { ok, data } = await API.post('/ledger/transfer', {
      to_user_id:  +el('tf-to').value,
      amount:       +el('tf-amt').value,
      description:  el('tf-note').value.trim(),
    }, { idempotencyKey: API.idemKey() });

    showAlert(
      ok ? `Transfer complete. New balance: ${data.new_balance?.toFixed(2)}` : data.error,
      ok ? 'success' : 'error'
    );
    if (ok) { closeModal(); renderLedger(); }
  });
}
