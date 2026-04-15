/**
 * api.js — Centralised HTTP client.
 *
 * Auth hardening: JWT is stored in an httpOnly cookie set by the server on
 * login.  JavaScript cannot read it (XSS protection).  The browser sends it
 * automatically on every same-origin request (credentials:'same-origin').
 *
 * Non-sensitive user metadata (username, role, etc.) is cached in
 * sessionStorage so the UI can personalise without a round-trip.
 * sessionStorage is cleared on tab close and is isolated per-tab, so
 * switching users in different tabs cannot leak roles.
 *
 * Backward compatibility: Bearer tokens in Authorization headers are still
 * accepted server-side for API clients and existing tests.
 */

const USER_KEY = 'pex_user';

export const API = {
  // Token lives only in the httpOnly cookie — not accessible to JS.
  // These stubs keep call-sites that were written against the old
  // localStorage token API working without modification.
  getToken()   { return null; },
  setToken()   { /* no-op: server sets the cookie on login */ },
  clearToken() { /* use API.logout() to clear the server-side cookie */ },

  saveUser(u)  { sessionStorage.setItem(USER_KEY, JSON.stringify(u)); },
  loadUser()   {
    try { return JSON.parse(sessionStorage.getItem(USER_KEY)); }
    catch { return null; }
  },
  clearUser()  { sessionStorage.removeItem(USER_KEY); },

  /**
   * Call POST /auth/logout to clear the httpOnly cookie, then wipe local state.
   * Callers should navigate to the login screen after this resolves.
   */
  async logout() {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); }
    catch (_) { /* network error — clear local state regardless */ }
    this.clearUser();
  },

  /**
   * Core request method.
   * credentials:'same-origin' ensures the httpOnly cookie is sent automatically.
   * @returns {{ ok, status, data }}
   */
  async req(method, path, body = null, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;

    const fetchOpts = { method, headers, credentials: 'same-origin' };
    if (body !== null) fetchOpts.body = JSON.stringify(body);

    try {
      const res  = await fetch('/api' + path, fetchOpts);
      const data = await res.json().catch(() => ({}));

      // Auto-logout on 401: clear local state and reload to show login screen.
      // `/auth/login` is excluded so the auth UI can render the credential
      // error. `/auth/me` is also excluded because it's the boot-time probe —
      // a 401 there just means "not logged in yet" and triggering a reload
      // would race renderAuth() and leave the page stuck on "Starting…".
      if (res.status === 401
          && path !== '/auth/login'
          && path !== '/auth/me') {
        this.clearUser();
        window.location.reload();
      }
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      return { ok: false, status: 0, data: { error: 'Network error. Is the server running?' } };
    }
  },

  get(path, params = {}) {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v != null))
    ).toString();
    return this.req('GET', path + (q ? '?' + q : ''));
  },
  post(path, body, opts) { return this.req('POST',   path, body, opts || {}); },
  put(path, body)        { return this.req('PUT',    path, body); },
  del(path)              { return this.req('DELETE', path); },

  /** Generate a random idempotency key */
  idemKey() {
    return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  },
};
