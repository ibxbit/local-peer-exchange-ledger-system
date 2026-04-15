/**
 * Vitest unit tests for static/js/api.js — the centralised HTTP client.
 *
 * api.js relies on the browser environment (sessionStorage, window.location,
 * and global fetch). The Vitest runner is configured to use happy-dom, which
 * provides all three without spinning up a real browser.
 *
 * What we cover
 *   1. Header / token handling
 *      - Auth lives in an httpOnly cookie set by the server, so api.js must
 *        never inject an Authorization header itself. We assert that all
 *        outgoing fetch calls go out with credentials:'same-origin' and
 *        without an Authorization header.
 *      - The legacy getToken/setToken/clearToken stubs must be no-ops so
 *        old call-sites do not crash.
 *   2. Logout state cleanup
 *      - API.logout() must POST /api/auth/logout AND clear sessionStorage,
 *        even if the network call fails.
 *   3. 401 / 403 error handling
 *      - 401 on any endpoint other than /auth/login triggers clearUser()
 *        and a window.location.reload().
 *      - 403 with code:'password_change_required' propagates the code to
 *        the caller intact (so app.js can route the user to the password
 *        change view).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { API } from '../api.js';

// ---------------------------------------------------------------------------
// Test scaffolding
// ---------------------------------------------------------------------------

let originalFetch;
let originalLocation;
let reloadSpy;

beforeEach(() => {
  originalFetch = globalThis.fetch;
  sessionStorage.clear();

  // Replace window.location with a stub so we can spy on .reload() without
  // happy-dom actually attempting a navigation.
  originalLocation = window.location;
  reloadSpy = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...originalLocation, reload: reloadSpy, href: 'http://localhost/' },
  });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: originalLocation,
  });
  vi.restoreAllMocks();
});

/**
 * Build a fake fetch that returns one or more pre-canned responses in order.
 * Each response = { status, body, ok? }; ok defaults to status < 400.
 */
function mockFetchSequence(...responses) {
  const queue = [...responses];
  return vi.fn(async (_url, _opts) => {
    const next = queue.shift() ?? { status: 200, body: {} };
    const ok = next.ok ?? next.status < 400;
    return {
      ok,
      status: next.status,
      json: async () => next.body,
    };
  });
}


// ---------------------------------------------------------------------------
// 1. Header / token handling
// ---------------------------------------------------------------------------

describe('API.req — token / header handling', () => {
  it('does NOT inject an Authorization header (auth lives in httpOnly cookie)', async () => {
    const fetchMock = mockFetchSequence({ status: 200, body: { ok: true } });
    globalThis.fetch = fetchMock;

    await API.get('/users/me');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/users/me');
    expect(opts.method).toBe('GET');
    // Must use credentials:'same-origin' so the cookie is forwarded
    expect(opts.credentials).toBe('same-origin');
    // Must NOT inject an Authorization header
    expect(opts.headers).toBeDefined();
    expect(opts.headers.Authorization).toBeUndefined();
    expect(opts.headers['Content-Type']).toBe('application/json');
  });

  it('legacy getToken/setToken/clearToken are no-op stubs', () => {
    expect(API.getToken()).toBeNull();
    // Setters must not throw and must not persist anything
    expect(() => API.setToken('whatever')).not.toThrow();
    expect(() => API.clearToken()).not.toThrow();
    expect(API.getToken()).toBeNull();
    // sessionStorage must remain untouched by these stubs
    expect(sessionStorage.getItem('pex_user')).toBeNull();
  });

  it('forwards an Idempotency-Key header when opts.idempotencyKey is set', async () => {
    const fetchMock = mockFetchSequence({ status: 201, body: { id: 7 } });
    globalThis.fetch = fetchMock;

    await API.post('/ledger/transfer',
      { to_user_id: 9, amount: 5 },
      { idempotencyKey: 'idem-abc-123' });

    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.headers['Idempotency-Key']).toBe('idem-abc-123');
    expect(opts.body).toBe(JSON.stringify({ to_user_id: 9, amount: 5 }));
  });

  it('serializes GET query params and skips empty/null values', async () => {
    const fetchMock = mockFetchSequence({ status: 200, body: { entries: [] } });
    globalThis.fetch = fetchMock;

    await API.get('/ledger', {
      page: 2, per_page: 50, status: '', user_id: null, role: 'issuer',
    });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toMatch(/^\/api\/ledger\?/);
    expect(url).toContain('page=2');
    expect(url).toContain('per_page=50');
    expect(url).toContain('role=issuer');
    expect(url).not.toContain('status=');
    expect(url).not.toContain('user_id=');
  });

  it('idemKey() generates unique-looking keys', () => {
    const a = API.idemKey();
    const b = API.idemKey();
    expect(a).toMatch(/^idem-\d+-[a-z0-9]+$/);
    expect(a).not.toBe(b);
  });
});


// ---------------------------------------------------------------------------
// 2. Logout state cleanup
// ---------------------------------------------------------------------------

describe('API.logout — local-state cleanup', () => {
  it('POSTs /auth/logout AND clears the cached user', async () => {
    API.saveUser({ id: 1, username: 'alice', role: 'user' });
    expect(API.loadUser()).toMatchObject({ username: 'alice' });

    const fetchMock = mockFetchSequence({ status: 200, body: { ok: true } });
    globalThis.fetch = fetchMock;

    await API.logout();

    // The /auth/logout call clears the httpOnly cookie server-side
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/auth/logout');
    expect(opts.method).toBe('POST');
    expect(opts.credentials).toBe('same-origin');

    // Local cache cleared regardless of network outcome
    expect(API.loadUser()).toBeNull();
    expect(sessionStorage.getItem('pex_user')).toBeNull();
  });

  it('still clears local state when the network call rejects', async () => {
    API.saveUser({ id: 2, username: 'bob', role: 'user' });

    globalThis.fetch = vi.fn(async () => {
      throw new Error('connection refused');
    });

    await API.logout();

    expect(API.loadUser()).toBeNull();
  });

  it('saveUser/loadUser round-trip a JSON object', () => {
    API.saveUser({ id: 7, username: 'carol', role: 'admin', extra: [1, 2] });
    const loaded = API.loadUser();
    expect(loaded).toEqual({ id: 7, username: 'carol', role: 'admin', extra: [1, 2] });
  });

  it('loadUser returns null when sessionStorage is empty or malformed', () => {
    expect(API.loadUser()).toBeNull();
    sessionStorage.setItem('pex_user', 'not-json');
    expect(API.loadUser()).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// 3. 401 / 403 error handling
// ---------------------------------------------------------------------------

describe('API.req — 401 auto-logout', () => {
  it('clears local state and reloads the page on 401 (non-login path)', async () => {
    API.saveUser({ id: 1, username: 'alice', role: 'user' });

    globalThis.fetch = mockFetchSequence({
      status: 401,
      body: { error: 'Authentication required.' },
    });

    const result = await API.get('/users/me');

    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
    // Local user blown away
    expect(API.loadUser()).toBeNull();
    // Browser pushed back to the login screen via reload
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it('does NOT auto-logout on a 401 from /auth/login (so the form can show the error)', async () => {
    globalThis.fetch = mockFetchSequence({
      status: 401,
      body: { error: 'Invalid credentials.' },
    });

    const result = await API.post('/auth/login',
      { username: 'x', password: 'y' });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
    // Login failure must NOT trigger the page reload — the auth UI handles it
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it('does NOT auto-logout on a 401 from /auth/me (boot-time probe)', async () => {
    // app.js's boot() calls /auth/me to detect whether a valid session cookie
    // exists. For an unauthenticated visitor this returns 401, which means
    // "not logged in yet" — renderAuth() then paints the login form. If the
    // 401 handler reloaded the page here, it would race renderAuth() and
    // leave the SPA stuck on the "Starting…" loader (see E2E smoke test).
    globalThis.fetch = mockFetchSequence({
      status: 401,
      body: { error: 'Authentication required.' },
    });

    const result = await API.get('/auth/me');

    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});

describe('API.req — 403 password_change_required propagates', () => {
  it('returns the password_change_required code for the caller to route on', async () => {
    globalThis.fetch = mockFetchSequence({
      status: 403,
      body: {
        error: 'Password change required before continuing.',
        code: 'password_change_required',
      },
    });

    const result = await API.get('/ledger');

    expect(result.ok).toBe(false);
    expect(result.status).toBe(403);
    expect(result.data.code).toBe('password_change_required');
    expect(result.data.error).toMatch(/Password change required/);
    // 403 must NOT trigger the auto-logout reload — only 401 does
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it('returns a generic 403 unchanged when no special code is present', async () => {
    globalThis.fetch = mockFetchSequence({
      status: 403,
      body: { error: 'Insufficient permissions.' },
    });

    const result = await API.get('/admin/analytics');

    expect(result.ok).toBe(false);
    expect(result.status).toBe(403);
    expect(result.data.error).toBe('Insufficient permissions.');
    expect(result.data.code).toBeUndefined();
  });
});

describe('API.req — network failure handling', () => {
  it('returns a sentinel response on fetch rejection', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });

    const result = await API.get('/users/me');

    expect(result.ok).toBe(false);
    expect(result.status).toBe(0);
    expect(result.data.error).toMatch(/Network error/i);
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it('handles a 200 with non-JSON body without crashing', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError('not json'); },
    }));

    const result = await API.get('/something');

    expect(result.ok).toBe(true);
    expect(result.status).toBe(200);
    expect(result.data).toEqual({});
  });
});
