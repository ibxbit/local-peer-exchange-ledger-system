/**
 * Vitest unit tests for static/js/dashboard.js — user dashboard view.
 *
 * The module exports renderDashboard(). It fetches /auth/me and
 * /reputation/score/<uid>, then paints stat cards + quick actions into the
 * #view-container div. The tests stub fetch and assert on the DOM.
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import { renderDashboard } from '../dashboard.js';
import { API } from '../api.js';

let fetchMock;

beforeEach(() => {
  document.body.innerHTML = '<div id="view-container"></div>';
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

describe('dashboard: renderDashboard', () => {
  it('paints stat cards and account table on happy path', async () => {
    API.saveUser({ id: 5, username: 'alice', role: 'user' });

    // /auth/me then /reputation/score/5 — dashboard.js fires both in parallel
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/auth/me')) {
        return respond({
          body: {
            user: {
              id: 5, username: 'alice', email: 'alice@example.com',
              role: 'user', credit_balance: 325.5,
              is_active: 1, created_at: '2025-01-01T00:00:00Z',
            },
          },
        });
      }
      if (url.includes('/reputation/score/5')) {
        return respond({
          body: {
            reputation_score: 92.0,
            average_rating: 4.3,
            total_ratings: 12,
            positive_ratings: 11,
            sessions_completed: 7,
            violations_against: 0,
          },
        });
      }
      return respond({ status: 404, body: {} });
    });

    await renderDashboard();
    await flush();

    const vc = document.querySelector('#view-container');
    // Headline
    expect(vc.innerHTML).toContain('Dashboard');
    // Balance rendered with two decimals
    expect(vc.textContent).toContain('325.50');
    // Reputation stats
    expect(vc.textContent).toContain('92');
    expect(vc.textContent).toContain('4.3');
    // Account table values
    expect(vc.textContent).toContain('alice');
    expect(vc.textContent).toContain('alice@example.com');
    expect(vc.textContent).toContain('user');  // role badge
    // Active status badge
    expect(vc.innerHTML).toContain('badge-active');
    // Quick action buttons present
    expect(vc.innerHTML).toContain("App.navigate('matching')");
    expect(vc.innerHTML).toContain("App.navigate('ledger')");
  });

  it('shows a low-balance warning when credit_balance < 60', async () => {
    API.saveUser({ id: 9, username: 'bob', role: 'user' });
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/auth/me')) {
        return respond({
          body: {
            user: {
              id: 9, username: 'bob', email: 'b@b.com',
              role: 'user', credit_balance: 30, is_active: 1,
              created_at: '2025-01-01T00:00:00Z',
            },
          },
        });
      }
      return respond({ body: { reputation_score: 0, sessions_completed: 0 } });
    });

    await renderDashboard();
    await flush();

    const html = document.querySelector('#view-container').innerHTML;
    expect(html).toContain('alert-warn');
    expect(html).toMatch(/below 60 credits/);
  });

  it('renders an error banner when /auth/me fails', async () => {
    API.saveUser({ id: 1, username: 'x', role: 'user' });
    fetchMock.mockResolvedValue(respond({
      status: 500, body: { error: 'boom' },
    }));

    await renderDashboard();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.innerHTML).toContain('alert-error');
    expect(vc.textContent).toContain('boom');
  });

  it('refreshes the cached user object from /auth/me data', async () => {
    API.saveUser({ id: 2, username: 'stale', role: 'user' });
    fetchMock.mockImplementation(async (url) => {
      if (url.includes('/auth/me')) {
        return respond({
          body: {
            user: {
              id: 2, username: 'stale', email: 's@s.com',
              role: 'user', credit_balance: 100, is_active: 1,
              created_at: '2025-01-01T00:00:00Z',
              // new field coming from the server that wasn't in the cache
              must_change_password: false,
            },
          },
        });
      }
      return respond({ body: {} });
    });

    await renderDashboard();
    await flush();

    // saveUser is called with merged fields — the new field is persisted.
    const stored = API.loadUser();
    expect(stored.id).toBe(2);
    expect(stored.must_change_password).toBe(false);
  });
});
