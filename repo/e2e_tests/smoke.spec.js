const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const USER_PASSWORD = 'E2EUser@Test12345!';
const ADMIN_ROTATED_PASSWORD = 'Admin@Rotated123456!';

function loadBootstrapPassword() {
  const cfgPath = path.join(__dirname, '..', 'instance', 'config.json');
  if (!fs.existsSync(cfgPath)) {
    return null;
  }
  const raw = fs.readFileSync(cfgPath, 'utf8');
  const cfg = JSON.parse(raw);
  return cfg.ADMIN_BOOTSTRAP_PASSWORD || null;
}

async function login(request, username, password) {
  const resp = await request.post('/api/auth/login', {
    data: { username, password },
  });
  const data = await resp.json();
  return { resp, data };
}

async function getAdminToken(request) {
  const bootstrapPassword = loadBootstrapPassword();
  let token = null;
  let usedPassword = null;
  let loginData = null;

  const candidates = [];
  if (process.env.PEX_ADMIN_PASSWORD) {
    candidates.push(process.env.PEX_ADMIN_PASSWORD);
  }
  candidates.push(ADMIN_ROTATED_PASSWORD);
  if (bootstrapPassword) {
    candidates.push(bootstrapPassword);
  }

  for (const candidate of candidates) {
    const { resp, data } = await login(request, 'admin', candidate);
    if (resp.ok()) {
      token = data.token;
      usedPassword = candidate;
      loginData = data;
      break;
    }
  }

  if (!token) return null;

  if (loginData.user && loginData.user.must_change_password) {
    const rotate = await request.post('/api/auth/change-password', {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        current_password: usedPassword,
        new_password: ADMIN_ROTATED_PASSWORD,
      },
    });
    expect(rotate.ok()).toBeTruthy();

    const relogin = await login(request, 'admin', ADMIN_ROTATED_PASSWORD);
    expect(relogin.resp.ok()).toBeTruthy();
    token = relogin.data.token;
  }

  return token;
}

test('login -> queue -> status poll -> logout', async ({ page, request }) => {
  const suffix = Date.now();
  const username = `e2e_user_${suffix}`;
  const email = `${username}@test.local`;
  const skill = `e2e-skill-${suffix}`;

  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable. Set PEX_ADMIN_PASSWORD to run this smoke test.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const registerResp = await request.post('/api/auth/register', {
    data: { username, email, password: USER_PASSWORD },
  });
  expect(registerResp.ok()).toBeTruthy();
  const registerData = await registerResp.json();
  const userId = registerData.user_id;

  const userLogin = await login(request, username, USER_PASSWORD);
  expect(userLogin.resp.ok()).toBeTruthy();
  const userToken = userLogin.data.token;

  const creditResp = await request.post('/api/ledger/credit', {
    headers: adminHeaders,
    data: {
      user_id: userId,
      amount: 500,
      description: 'Seed credits for browser smoke test',
    },
  });
  expect(creditResp.ok()).toBeTruthy();

  const submitVerification = await request.post('/api/verification/submit', {
    headers: { Authorization: `Bearer ${userToken}` },
    multipart: {
      document_type: 'passport',
      document: {
        name: 'doc.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.4 smoke'),
      },
    },
  });
  if (submitVerification.status() === 201) {
    const verification = await submitVerification.json();
    const reviewResp = await request.put(
      `/api/verification/${verification.verification_id}/review`,
      {
        headers: adminHeaders,
        data: {
          decision: 'verified',
          notes: 'approved by Playwright smoke test',
        },
      },
    );
    expect(reviewResp.ok()).toBeTruthy();
  }

  await page.goto('/');
  await page.fill('#l-user', username);
  await page.fill('#l-pass', USER_PASSWORD);
  await page.click('#btn-login');
  await expect(page.locator('#btn-logout')).toBeVisible();

  await page.click('a[data-view="matching"]');
  await expect(page.locator('#btn-open-queue')).toBeVisible();

  await page.click('#btn-open-queue');
  await page.fill('#q-skill', skill);
  await page.click('#queue-form button[type="submit"]');

  const statusCard = page.locator('#match-status-card');
  await expect(statusCard).toBeVisible();
  await expect(statusCard).toContainText('Searching');
  await expect(statusCard).toHaveAttribute('hx-trigger', /every 10s/);

  await page.click('#btn-logout');
  await expect(page.locator('#btn-login')).toBeVisible();

  const meAfterLogout = await page.request.get('/api/auth/me');
  expect(meAfterLogout.status()).toBe(401);
});
