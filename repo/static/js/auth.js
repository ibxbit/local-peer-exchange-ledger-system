/**
 * auth.js — Login and Register views.
 */

import { API } from './api.js';
import { el, showAlert } from './utils.js';

export function renderAuth(onSuccess) {
  document.body.innerHTML = `
<div class="auth-wrap">
  <div class="auth-card">
    <h1>⇄ PeerExchange</h1>
    <div class="auth-tabs">
      <button class="auth-tab active" data-tab="login">Sign In</button>
      <button class="auth-tab"        data-tab="register">Register</button>
    </div>
    <div id="auth-alert"></div>

    <!-- Login form -->
    <div id="tab-login">
      <div class="form-group">
        <label>Username</label>
        <input id="l-user" type="text" placeholder="username" autocomplete="username">
      </div>
      <div class="form-group">
        <label>Password</label>
        <input id="l-pass" type="password" placeholder="password" autocomplete="current-password">
      </div>
      <button class="btn btn-primary btn-full" id="btn-login">Sign In</button>
    </div>

    <!-- Register form -->
    <div id="tab-register" style="display:none">
      <div class="form-group">
        <label>Username <small style="color:var(--c-text-sub)">(3–32 chars, letters/digits/_.-)</small></label>
        <input id="r-user" type="text" placeholder="username" autocomplete="username">
      </div>
      <div class="form-group">
        <label>Email</label>
        <input id="r-email" type="email" placeholder="you@example.com" autocomplete="email">
      </div>
      <div class="form-group">
        <label>Password <small style="color:var(--c-text-sub)">(≥12 chars, upper+lower+digit+special)</small></label>
        <input id="r-pass" type="password" placeholder="••••••••••••" autocomplete="new-password">
      </div>
      <button class="btn btn-primary btn-full" id="btn-register">Create Account</button>
    </div>

    <p style="text-align:center;margin-top:1rem;font-size:.75rem;color:var(--c-text-sub)">
      Fully offline — no external network required.
    </p>
  </div>
</div>`;

  // Tab switcher
  document.querySelectorAll('.auth-tab').forEach(t =>
    t.addEventListener('click', () => {
      document.querySelectorAll('.auth-tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      el('tab-login').style.display    = t.dataset.tab === 'login'    ? '' : 'none';
      el('tab-register').style.display = t.dataset.tab === 'register' ? '' : 'none';
      el('auth-alert').innerHTML = '';
    })
  );

  // Enter key submits
  ['l-user', 'l-pass'].forEach(id =>
    el(id)?.addEventListener('keypress', e => { if (e.key === 'Enter') el('btn-login').click(); })
  );

  el('btn-login').addEventListener('click', async () => {
    const btn = el('btn-login');
    btn.disabled = true;
    btn.textContent = 'Signing in…';

    const { ok, data } = await API.post('/auth/login', {
      username: el('l-user').value.trim(),
      password: el('l-pass').value,
    });

    btn.disabled = false;
    btn.textContent = 'Sign In';

    if (!ok) {
      showAlert(data.error || 'Login failed.', 'error', 'auth-alert');
      return;
    }
    API.setToken(data.token);
    API.saveUser(data.user);
    onSuccess(data.user);
  });

  el('btn-register').addEventListener('click', async () => {
    const btn = el('btn-register');
    btn.disabled = true;
    btn.textContent = 'Creating account…';

    const { ok, data } = await API.post('/auth/register', {
      username: el('r-user').value.trim(),
      email:    el('r-email').value.trim(),
      password: el('r-pass').value,
    });

    btn.disabled = false;
    btn.textContent = 'Create Account';

    if (!ok) {
      showAlert(data.error || 'Registration failed.', 'error', 'auth-alert');
      return;
    }
    showAlert('Registration successful! You can now sign in.', 'success', 'auth-alert');
    // Auto-switch to login tab
    document.querySelectorAll('.auth-tab')[0].click();
    el('l-user').value = el('r-user').value;
  });
}
