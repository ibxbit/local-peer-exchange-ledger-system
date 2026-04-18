/**
 * Vitest unit tests for static/js/ledger.js — ledger view + transfer modal.
 *
 * Covers:
 *   - renderLedger paints balance and transaction rows from /ledger/balance +
 *     /ledger responses
 *   - a credit transaction row uses the "+" sign, a debit row uses "−"
 *   - low-balance warning appears when balance < 60
 *   - clicking "Transfer Credits" opens a modal, and submitting the form
 *     POSTs /ledger/transfer with an Idempotency-Key header
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import { renderLedger } from '../ledger.js';

let fetchMock;

beforeEach(() => {
  document.body.innerHTML = `
    <div id="view-container"></div>
    <div id="main-alert"></div>
  `;
  sessionStorage.clear();
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function respond({ status = 200, body = {} } = {}) {
  return { ok: status < 400, status, json: async () => body };
}

function flush() {
  return new Promise((r) => setTimeout(r, 0));
}

describe('ledger: renderLedger', () => {
  it('paints balance, transaction table and transfer button', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url.startsWith('/api/ledger/balance')) {
        return respond({ body: { balance: 250.5 } });
      }
      if (url.startsWith('/api/ledger')) {
        return respond({
          body: {
            entries: [
              { transaction_type: 'credit',  amount: 100,
                balance_after: 350.5, description: 'Top up',
                created_at: '2025-01-01T00:00:00Z' },
              { transaction_type: 'transfer_out', amount: 50,
                balance_after: 300.5, description: 'Sent to Bob',
                created_at: '2025-01-02T00:00:00Z' },
            ],
            total: 2,
          },
        });
      }
      return respond({ status: 404 });
    });

    await renderLedger();
    await flush();

    const html = document.querySelector('#view-container').innerHTML;
    expect(html).toContain('My Ledger');
    expect(html).toContain('250.50');
    // Credit row has "+", debit row has "−"
    expect(html).toMatch(/\+100\.00/);
    expect(html).toMatch(/−50\.00/);
    // Transfer button exists
    expect(document.querySelector('#btn-transfer')).toBeTruthy();
  });

  it('shows the low-balance warning banner when balance < 60', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url.startsWith('/api/ledger/balance')) {
        return respond({ body: { balance: 10 } });
      }
      return respond({ body: { entries: [], total: 0 } });
    });

    await renderLedger();
    await flush();

    const html = document.querySelector('#view-container').innerHTML;
    expect(html).toContain('alert-warn');
    expect(html).toMatch(/Balance below 60/);
  });

  it('shows an empty-state row when there are no entries', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url.startsWith('/api/ledger/balance')) {
        return respond({ body: { balance: 500 } });
      }
      return respond({ body: { entries: [], total: 0 } });
    });

    await renderLedger();
    await flush();

    const html = document.querySelector('#view-container').innerHTML;
    expect(html).toContain('No transactions yet');
  });

  it('clicking transfer opens a modal with the form', async () => {
    fetchMock.mockImplementation(async () =>
      respond({ body: { balance: 500, entries: [], total: 0 } }));
    await renderLedger();
    await flush();

    document.querySelector('#btn-transfer').click();
    expect(document.querySelector('#transfer-form')).toBeTruthy();
    expect(document.querySelector('#tf-to')).toBeTruthy();
    expect(document.querySelector('#tf-amt')).toBeTruthy();
  });

  it('submitting the transfer form POSTs /ledger/transfer with an Idempotency-Key',
    async () => {
      fetchMock.mockImplementation(async (url) => {
        if (url.startsWith('/api/ledger/balance')) {
          return respond({ body: { balance: 500 } });
        }
        if (url.startsWith('/api/ledger/transfer')) {
          return respond({ body: { new_balance: 450.0 } });
        }
        return respond({ body: { entries: [], total: 0 } });
      });

      await renderLedger();
      await flush();
      document.querySelector('#btn-transfer').click();

      document.querySelector('#tf-to').value = '7';
      document.querySelector('#tf-amt').value = '50';
      document.querySelector('#tf-note').value = 'for session';

      const form = document.querySelector('#transfer-form');
      form.dispatchEvent(new Event('submit', {
        bubbles: true, cancelable: true,
      }));

      await flush();
      await flush();

      const transferCall = fetchMock.mock.calls.find(
        ([u]) => u === '/api/ledger/transfer');
      expect(transferCall).toBeDefined();
      const [, opts] = transferCall;
      expect(opts.method).toBe('POST');
      expect(opts.headers['Idempotency-Key']).toMatch(/^idem-/);
      const body = JSON.parse(opts.body);
      expect(body).toMatchObject({
        to_user_id: 7, amount: 50, description: 'for session',
      });
    });
});
