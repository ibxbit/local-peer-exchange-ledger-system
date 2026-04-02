/**
 * api.js — Centralised HTTP client.
 * Manages the JWT token and wraps fetch with auth headers.
 */

const TOKEN_KEY = 'pex_token';
const USER_KEY  = 'pex_user';

export const API = {
  _token: localStorage.getItem(TOKEN_KEY),

  setToken(t)   { this._token = t; localStorage.setItem(TOKEN_KEY, t); },
  clearToken()  { this._token = null; localStorage.removeItem(TOKEN_KEY);
                  localStorage.removeItem(USER_KEY); },
  getToken()    { return this._token; },

  saveUser(u)   { localStorage.setItem(USER_KEY, JSON.stringify(u)); },
  loadUser()    { try { return JSON.parse(localStorage.getItem(USER_KEY)); }
                  catch { return null; } },

  /**
   * Core request method.
   * @returns {{ ok, status, data }}
   */
  async req(method, path, body = null, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (this._token) headers['Authorization'] = `Bearer ${this._token}`;
    if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;

    const fetchOpts = { method, headers };
    if (body !== null) fetchOpts.body = JSON.stringify(body);

    try {
      const res  = await fetch('/api' + path, fetchOpts);
      const data = await res.json().catch(() => ({}));

      // Automatically handle token expiry
      if (res.status === 401 && path !== '/auth/login') {
        this.clearToken();
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
