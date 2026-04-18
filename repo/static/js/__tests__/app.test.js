/**
 * Vitest unit tests for static/js/app.js — shell rendering and client-side
 * router.
 *
 * app.js runs boot() at import time, which probes /api/auth/me. We pre-stub
 * fetch so that probe returns 401 (unauthenticated), which lands on the
 * renderAuth() branch and allows the SPA shell logic to mount predictably
 * for subsequent tests.
 *
 * What we cover:
 *   1. boot() calls /api/auth/me; a 401 response routes to renderAuth().
 *   2. navigate('dashboard') wires up the correct route handler.
 *   3. navigate('admin-dashboard') as a regular user shows an access-denied
 *      alert and does NOT invoke the admin handler.
 *   4. navigate('audit-logs') as an auditor is allowed.
 *   5. window.App is populated with the helper methods exposed to onclick=.
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import { API } from '../api.js';

let fetchMock;
let appModule;

// Provide a 401 response for /auth/me so the boot() on import does not fail.
function stubFetchFor401OnAuthMe() {
  return vi.fn(async (url) => ({
    ok: false,
    status: 401,
    json: async () => ({ error: 'Authentication required.' }),
  }));
}

beforeEach(async () => {
  document.body.innerHTML = '';
  sessionStorage.clear();
  vi.resetModules();
  fetchMock = stubFetchFor401OnAuthMe();
  globalThis.fetch = fetchMock;

  // Dynamically import app.js so boot() runs against the mocked fetch.
  // Using import("") each test guarantees a fresh module instance.
  appModule = await import('../app.js');
  // Let the boot() microtask complete
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. boot() behaviour
// ---------------------------------------------------------------------------

describe('app: boot()', () => {
  it('probes /api/auth/me at startup', () => {
    // First fetch call made by boot()
    const urls = fetchMock.mock.calls.map((c) => c[0]);
    expect(urls[0]).toBe('/api/auth/me');
  });

  it('renders the auth (login) shell on 401 from /auth/me', () => {
    expect(document.querySelector('.auth-wrap')).toBeTruthy();
    expect(document.querySelector('#btn-login')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 2. Global App helper object
// ---------------------------------------------------------------------------

describe('app: window.App helpers', () => {
  it('exposes the navigation + click-handler functions on window.App', () => {
    expect(typeof window.App.navigate).toBe('function');
    expect(typeof window.App.openRequestSession).toBe('function');
    expect(typeof window.App.openRateSession).toBe('function');
    expect(typeof window.App.viewRepModal).toBe('function');
    expect(typeof window.App.adminManageUser).toBe('function');
    expect(typeof window.App.adminCreditModal).toBe('function');
    expect(typeof window.App.reviewVerification).toBe('function');
    expect(typeof window.App.resolveViolation).toBe('function');
    expect(typeof window.App.resolveAppeal).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// 3. Role-based navigation guards
// ---------------------------------------------------------------------------

describe('app: navigate() role enforcement', () => {
  beforeEach(() => {
    // Replace the auth shell with the minimal app-shell bits that navigate()
    // uses — #view-container, #main-alert and a [data-view] link so the
    // selector manipulation inside navigate() does not crash.
    document.body.innerHTML = `
      <div id="main-alert"></div>
      <a href="#" data-view="dashboard">dashboard</a>
      <div id="view-container"></div>
    `;
  });

  it('blocks admin-only views when the current user is a "user"', () => {
    API.saveUser({ id: 1, username: 'alice', role: 'user' });
    appModule.navigate('admin-dashboard');

    // navigate() writes to #main-alert via showAlert
    const alert = document.querySelector('#main-alert').innerHTML;
    expect(alert).toContain('Admin access required');
    // The view container should NOT have been painted with the admin view
    expect(document.querySelector('#view-container').innerHTML).toBe('');
  });

  it('blocks staff-only views for regular users', () => {
    API.saveUser({ id: 1, username: 'alice', role: 'user' });
    appModule.navigate('audit-logs');
    const alert = document.querySelector('#main-alert').innerHTML;
    expect(alert).toContain('Admin or Auditor access required');
  });

  it('allows auditors into staff-only views', async () => {
    API.saveUser({ id: 2, username: 'aud', role: 'auditor' });

    // renderAuditLogs (the registered handler) will fire fetch for /audit/logs.
    const fetchBefore = fetchMock.mock.calls.length;

    // Mock the API call the audit logs view makes
    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ logs: [], total: 0 }),
    });

    appModule.navigate('audit-logs');
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // The audit-logs handler made a fetch call
    const newCalls = fetchMock.mock.calls.slice(fetchBefore);
    expect(newCalls.some(([u]) => u.startsWith(
      '/api/audit/logs'))).toBe(true);
  });

  it('renders a "not found" banner for unknown views', () => {
    API.saveUser({ id: 1, username: 'alice', role: 'user' });
    appModule.navigate('this-view-does-not-exist');
    const html = document.querySelector('#view-container').innerHTML;
    expect(html).toContain('alert-error');
    expect(html).toContain('this-view-does-not-exist');
  });
});
