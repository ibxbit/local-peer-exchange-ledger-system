/**
 * Vitest unit tests for static/js/admin.js — admin dashboard, users, sessions,
 * verifications, violations, appeals, chain verification, audit logs.
 *
 * Covers rendering logic and the confirm-action callbacks that POST/PUT to
 * the API. global fetch is stubbed and the DOM is the public surface we
 * assert against.
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import {
  renderAdminDashboard, renderAdminUsers,
  renderAdminVerifications, renderAdminViolations,
  renderAdminSessions, renderVerifyChains, renderAuditLogs,
  adminManageUser, adminCreditModal,
  reviewVerification, resolveViolation, resolveAppeal,
} from '../admin.js';

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

// ---------------------------------------------------------------------------
// Analytics / Dashboard
// ---------------------------------------------------------------------------

describe('admin: renderAdminDashboard', () => {
  it('renders all stat cards with data from /admin/analytics', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        users: {
          total: 50, active: 45, registered_last_7d: 5,
          by_role: { user: 44, admin: 2, auditor: 4 },
        },
        sessions: {
          total: 100,
          by_status: { completed: 70, pending: 10, cancelled: 20 },
        },
        ledger: { total_entries: 500, total_volume: 12345.67 },
        moderation: {
          pending_verifications: 3,
          open_violations: 2,
          pending_appeals: 1,
        },
        reputation: { platform_avg_rating: 4.3 },
      },
    }));

    await renderAdminDashboard();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('Admin Dashboard');
    expect(vc.textContent).toContain('50');    // total users
    expect(vc.textContent).toContain('45');    // active
    expect(vc.textContent).toContain('100');   // sessions
    expect(vc.textContent).toContain('12346'); // ledger volume rounded
    expect(vc.textContent).toContain('4.3');   // avg rating
    // Role cards
    expect(vc.innerHTML).toContain('role-badge user');
    expect(vc.innerHTML).toContain('role-badge admin');
  });

  it('surfaces API errors', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      status: 403, body: { error: 'forbidden' },
    }));
    await renderAdminDashboard();
    await flush();
    const vc = document.querySelector('#view-container');
    expect(vc.innerHTML).toContain('alert-error');
    expect(vc.textContent).toContain('forbidden');
  });
});

// ---------------------------------------------------------------------------
// User management
// ---------------------------------------------------------------------------

describe('admin: renderAdminUsers', () => {
  it('renders the user table with pagination and filter inputs',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        body: {
          users: [
            {
              id: 1, username: 'admin', email: 'a***@local',
              role: 'admin', is_active: 1, credit_balance: 1000,
              session_count: 5, avg_rating: 4.8, open_violations: 0,
            },
            {
              id: 2, username: 'bob', email: 'b***@x.com',
              role: 'user', is_active: 0, credit_balance: 10,
              session_count: 0, avg_rating: null, open_violations: 2,
            },
          ],
          total: 2,
        },
      }));

      await renderAdminUsers();
      await flush();

      const vc = document.querySelector('#view-container');
      expect(vc.textContent).toContain('User Management');
      expect(vc.textContent).toContain('admin');
      expect(vc.textContent).toContain('bob');
      // role-badge rendered for each role
      expect(vc.innerHTML).toContain('role-badge admin');
      expect(vc.innerHTML).toContain('role-badge user');
      // active/inactive badges via badge() helper
      expect(vc.innerHTML).toContain('badge-active');
      expect(vc.innerHTML).toContain('badge-cancelled');
      // Filter controls
      expect(vc.querySelector('#au-search')).toBeTruthy();
      expect(vc.querySelector('#au-role')).toBeTruthy();
    });

  it('uses current filter values in the request URL', async () => {
    // Filter inputs must live OUTSIDE #view-container because
    // renderAdminUsers first clears the container with loadingHTML() and
    // only then reads the #au-search / #au-role values.
    document.body.innerHTML = `
      <input id="au-search">
      <select id="au-role">
        <option value="">all</option>
        <option value="user">user</option>
      </select>
      <div id="view-container"></div>
      <div id="main-alert"></div>`;
    document.querySelector('#au-search').value = 'bob';
    document.querySelector('#au-role').value = 'user';

    fetchMock.mockResolvedValueOnce(respond({
      body: { users: [], total: 0 },
    }));
    await renderAdminUsers(2);
    await flush();
    const url = fetchMock.mock.calls[0][0];
    expect(url).toContain('page=2');
    expect(url).toContain('search=bob');
    expect(url).toContain('role=user');
  });
});

// ---------------------------------------------------------------------------
// Manage user modal
// ---------------------------------------------------------------------------

describe('admin: adminManageUser', () => {
  it('opens modal with role/role-change + ban button', () => {
    adminManageUser(3, 'carol', 'user', 1);
    const modal = document.querySelector('.modal');
    expect(modal).toBeTruthy();
    expect(modal.textContent).toContain('carol');
    expect(modal.querySelector('#mgr-role')).toBeTruthy();
    expect(modal.querySelector('#btn-ban-user')).toBeTruthy();
    expect(modal.querySelector('#btn-toggle')).toBeTruthy();
  });

  it('Update Role calls PUT /api/users/<id>/role', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { message: 'Role updated.' },
    }));
    adminManageUser(3, 'carol', 'user', 1);
    document.querySelector('#mgr-role').value = 'admin';
    document.querySelector('#btn-change-role').click();
    await flush();
    await flush();

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/users/3/role');
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ role: 'admin' });
  });
});

// ---------------------------------------------------------------------------
// Credit modal
// ---------------------------------------------------------------------------

describe('admin: adminCreditModal', () => {
  it('submits POST /ledger/credit with Idempotency-Key', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { new_balance: 550.0 },
    }));

    adminCreditModal(9, 'dave');
    document.querySelector('#cred-action').value = 'credit';
    document.querySelector('#cred-amt').value = '50';
    document.querySelector('#cred-desc').value = 'Top up';
    document.querySelector('#credit-form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    await flush();

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/ledger/credit');
    expect(opts.method).toBe('POST');
    expect(opts.headers['Idempotency-Key']).toMatch(/^idem-/);
    expect(JSON.parse(opts.body)).toMatchObject({
      user_id: 9, amount: 50, description: 'Top up',
    });
  });

  it('respects the selected action ("debit")', async () => {
    fetchMock.mockResolvedValueOnce(respond({ body: { new_balance: 10 } }));
    adminCreditModal(9, 'dave');
    document.querySelector('#cred-action').value = 'debit';
    document.querySelector('#cred-amt').value = '5';
    document.querySelector('#credit-form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    await flush();

    const url = fetchMock.mock.calls[0][0];
    expect(url).toBe('/api/ledger/debit');
  });
});

// ---------------------------------------------------------------------------
// Verifications / reviewVerification
// ---------------------------------------------------------------------------

describe('admin: renderAdminVerifications + reviewVerification', () => {
  it('renders verification rows from /verification', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        verifications: [
          { id: 1, username: 'bob', document_type: '[passport]',
            status: 'pending', submitted_at: '2025-01-01T00:00:00Z',
            reviewed_at: null },
        ],
        total: 1,
      },
    }));
    await renderAdminVerifications();
    await flush();
    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('Identity Verifications');
    expect(vc.textContent).toContain('bob');
    expect(vc.innerHTML).toContain('badge-pending');
    // Approve / Reject buttons rendered for pending rows
    expect(vc.innerHTML).toContain("reviewVerification(1,'verified'");
    expect(vc.innerHTML).toContain("reviewVerification(1,'rejected'");
  });

  it('reviewVerification PUTs /verification/<id>/review with decision',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        body: { message: 'Verification verified.' },
      }));
      reviewVerification(4, 'verified');
      document.querySelector('#ver-notes').value = 'All good';
      document.querySelector('#btn-confirm-ver').click();
      await flush();
      await flush();

      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/verification/4/review');
      expect(opts.method).toBe('PUT');
      expect(JSON.parse(opts.body)).toEqual({
        decision: 'verified', notes: 'All good',
      });
    });
});

// ---------------------------------------------------------------------------
// Violations / appeals
// ---------------------------------------------------------------------------

describe('admin: resolveViolation + resolveAppeal', () => {
  it('resolveViolation PUTs /reputation/violations/<id>/resolve', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { message: 'Violation resolved.' },
    }));
    resolveViolation(42, 'resolved');
    document.querySelector('#resolve-notes').value = 'Confirmed.';
    document.querySelector('#btn-confirm-resolve').click();
    await flush();
    await flush();

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/reputation/violations/42/resolve');
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({
      decision: 'resolved', notes: 'Confirmed.',
    });
  });

  it('resolveAppeal PUTs /reputation/appeals/<id>/resolve', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { message: 'Appeal resolved.' },
    }));
    resolveAppeal(11, 'upheld');
    document.querySelector('#appeal-notes').value = 'Agreed';
    document.querySelector('#btn-confirm-appeal').click();
    await flush();
    await flush();

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/reputation/appeals/11/resolve');
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({
      decision: 'upheld', notes: 'Agreed',
    });
  });
});

// ---------------------------------------------------------------------------
// Chain verification, sessions, audit logs
// ---------------------------------------------------------------------------

describe('admin: renderVerifyChains', () => {
  it('renders success banners when both chains are valid', async () => {
    // Two parallel calls — /ledger/verify and /audit/logs/verify
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('ledger/verify')) {
        return respond({
          body: { valid: true, message: 'intact', entries: 12 },
        });
      }
      return respond({
        body: { valid: true, message: 'intact', entries: 34 },
      });
    });

    await renderVerifyChains();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('Chain Integrity');
    expect(vc.textContent).toContain('12 entries');
    expect(vc.textContent).toContain('34 entries');
    // Success alert used when valid
    const successCount = (vc.innerHTML.match(/alert-success/g) || []).length;
    expect(successCount).toBeGreaterThanOrEqual(2);
  });
});

describe('admin: renderAdminSessions', () => {
  it('renders session rows from /admin/sessions', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        sessions: [
          { id: 1, initiator: 'alice', participant: 'bob',
            status: 'completed', credit_amount: 5, duration_minutes: 60,
            created_at: '2025-01-01T00:00:00Z',
            completed_at: '2025-01-01T01:00:00Z' },
        ],
        total: 1,
      },
    }));
    await renderAdminSessions();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('All Sessions');
    expect(vc.textContent).toContain('alice');
    expect(vc.textContent).toContain('bob');
    expect(vc.textContent).toContain('60 min');
    expect(vc.innerHTML).toContain('badge-completed');
  });
});

describe('admin: renderAuditLogs', () => {
  it('renders audit log rows + chain hash truncation', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        logs: [
          {
            id: 1, username: 'admin', action: 'USER_CREATED',
            entity_type: 'user', entity_id: 2,
            details: { source: 'register' }, ip_address: '127.0.0.1',
            created_at: '2025-01-01T00:00:00Z',
            log_hash: 'abcdef1234567890' + '0'.repeat(48),
          },
        ],
        total: 1,
      },
    }));
    await renderAuditLogs();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('Audit Logs');
    expect(vc.textContent).toContain('USER_CREATED');
    expect(vc.textContent).toContain('admin');
    // Hash truncated to first 10 chars + …
    expect(vc.innerHTML).toContain('abcdef1234…');
  });
});

describe('admin: renderAdminViolations tabs', () => {
  it('initial render shows the Violations tab by default', async () => {
    // First call loads /reputation/violations for the default tab
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/reputation/violations')) {
        return respond({ body: { violations: [], total: 0 } });
      }
      return respond({ body: { appeals: [], total: 0 } });
    });
    await renderAdminViolations();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.querySelector('#vtab-v')).toBeTruthy();
    expect(vc.querySelector('#vtab-a')).toBeTruthy();
    // At least one call went to /reputation/violations
    const calls = fetchMock.mock.calls.map((c) => c[0]);
    expect(calls.some((u) => u.startsWith(
      '/api/reputation/violations'))).toBe(true);
  });
});
