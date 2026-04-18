/**
 * Vitest unit tests for static/js/auth.js — login / register view.
 *
 * The module only exports renderAuth(onSuccess). It paints forms into the
 * document body and wires click handlers that call API.post. The tests
 * stub global fetch (API.post delegates to it via api.js) and verify:
 *   1. Shell + tabs render into the DOM.
 *   2. Clicking "Sign In" POSTs to /api/auth/login and calls onSuccess on 200.
 *   3. Login error renders in #auth-alert (no crash, onSuccess not invoked).
 *   4. Clicking "Create Account" POSTs to /api/auth/register and switches
 *      to the login tab on success.
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import { renderAuth } from '../auth.js';
import { API } from '../api.js';

let fetchMock;

beforeEach(() => {
  document.body.innerHTML = '';
  sessionStorage.clear();
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function respond({ status = 200, body = {} } = {}) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  };
}

function flushMicrotasks() {
  // Wait a couple of microtasks so the async click handler in auth.js can
  // run past its await points before we assert on the DOM.
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// ---------------------------------------------------------------------------
// 1. Shell
// ---------------------------------------------------------------------------

describe('auth: renderAuth renders the login / register shell', () => {
  it('injects both tabs and both forms into document.body', () => {
    renderAuth(() => {});
    expect(document.querySelector('.auth-wrap')).toBeTruthy();
    expect(document.querySelectorAll('.auth-tab').length).toBe(2);
    expect(document.querySelector('#l-user')).toBeTruthy();
    expect(document.querySelector('#r-user')).toBeTruthy();
    // Register tab is hidden by default
    expect(document.querySelector('#tab-register').style.display).toBe('none');
  });

  it('switching to the register tab shows register form, hides login form',
    () => {
      renderAuth(() => {});
      const tabs = document.querySelectorAll('.auth-tab');
      tabs[1].click();  // "Register" tab
      expect(document.querySelector('#tab-login').style.display).toBe('none');
      expect(document.querySelector('#tab-register').style.display).toBe('');
    });
});

// ---------------------------------------------------------------------------
// 2. Login click
// ---------------------------------------------------------------------------

describe('auth: Sign In click', () => {
  it('POSTs to /api/auth/login and calls onSuccess with the user on 200',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        status: 200,
        body: { token: 't', user: { id: 1, username: 'alice', role: 'user' } },
      }));
      const onSuccess = vi.fn();

      renderAuth(onSuccess);
      document.querySelector('#l-user').value = 'alice';
      document.querySelector('#l-pass').value = 'pw';
      document.querySelector('#btn-login').click();

      await flushMicrotasks();
      await flushMicrotasks();

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/auth/login');
      expect(opts.method).toBe('POST');
      const body = JSON.parse(opts.body);
      expect(body.username).toBe('alice');
      expect(body.password).toBe('pw');

      expect(onSuccess).toHaveBeenCalledTimes(1);
      expect(onSuccess.mock.calls[0][0]).toMatchObject({
        username: 'alice', role: 'user',
      });
      // saveUser was invoked (cached in sessionStorage)
      expect(API.loadUser()).toMatchObject({ username: 'alice' });
    });

  it('surfaces server error into #auth-alert and does NOT call onSuccess',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        status: 401, body: { error: 'Invalid credentials.' },
      }));
      const onSuccess = vi.fn();
      renderAuth(onSuccess);

      document.querySelector('#l-user').value = 'alice';
      document.querySelector('#l-pass').value = 'wrong';
      document.querySelector('#btn-login').click();

      await flushMicrotasks();
      await flushMicrotasks();

      expect(onSuccess).not.toHaveBeenCalled();
      const alert = document.querySelector('#auth-alert').innerHTML;
      expect(alert).toContain('Invalid credentials');
      expect(alert).toContain('alert-error');
      // Button state restored after await
      expect(document.querySelector('#btn-login').disabled).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 3. Register click
// ---------------------------------------------------------------------------

describe('auth: Register click', () => {
  it('POSTs /api/auth/register and auto-switches to the Login tab on success',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        status: 200,
        body: { message: 'Registration successful.', user_id: 42 },
      }));
      renderAuth(() => {});

      // Switch to register tab first
      document.querySelectorAll('.auth-tab')[1].click();
      document.querySelector('#r-user').value = 'newuser';
      document.querySelector('#r-email').value = 'new@example.com';
      document.querySelector('#r-pass').value = 'SuperSecret123!';
      document.querySelector('#btn-register').click();

      await flushMicrotasks();
      await flushMicrotasks();

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/auth/register');
      expect(opts.method).toBe('POST');
      expect(JSON.parse(opts.body)).toMatchObject({
        username: 'newuser', email: 'new@example.com',
      });

      // UI feedback: login tab is now active and username prefilled.
      // (The auto-tab-switch click handler clears the success banner, so we
      // assert on the tab state / prefill rather than the alert text.)
      expect(document.querySelector('#tab-register').style.display).toBe('none');
      expect(document.querySelector('#tab-login').style.display).toBe('');
      expect(document.querySelector('#l-user').value).toBe('newuser');
    });

  it('shows the API error when registration fails (no tab switch)',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        status: 400, body: { error: 'Password too weak.' },
      }));
      renderAuth(() => {});
      document.querySelectorAll('.auth-tab')[1].click();
      document.querySelector('#r-user').value = 'x';
      document.querySelector('#r-email').value = 'x@x';
      document.querySelector('#r-pass').value = 'short';
      document.querySelector('#btn-register').click();

      await flushMicrotasks();
      await flushMicrotasks();

      const alert = document.querySelector('#auth-alert').innerHTML;
      expect(alert).toContain('Password too weak');
      // Register tab still active (no auto-switch on failure)
      expect(document.querySelector('#tab-register').style.display).toBe('');
    });
});
