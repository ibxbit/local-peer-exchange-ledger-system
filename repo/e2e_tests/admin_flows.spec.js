/**
 * admin_flows.spec.js — End-to-end coverage for three admin-facing flows
 * that the original smoke spec did not exercise:
 *
 *   1. Violation appeals: a user files an appeal, an admin reviews
 *      the appeals list in the SPA, and resolves it (upheld / denied).
 *   2. Schedule resource management: admin creates / updates / deactivates
 *      a building/room/time-slot resource. There is no SPA UI for this
 *      surface yet, so the test exercises the /api/admin/resources
 *      endpoints directly through Playwright's request fixture.
 *   3. Verification workflow: a user uploads a document, the admin sees
 *      it in the SPA queue, fetches the decrypted payload through the
 *      admin-only document viewer endpoint, and then approves it via
 *      the Approve button in the UI.
 *
 * Conventions copied from smoke.spec.js:
 *   - login()             → POST /api/auth/login wrapper
 *   - getAdminToken()     → handles bootstrap → rotation → re-login
 *   - test.skip when admin credentials are unavailable
 *   - All test data is namespaced with Date.now() so re-runs do not collide
 */

const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const USER_PASSWORD = 'E2EUser@Test12345!';
const ADMIN_ROTATED_PASSWORD = 'Admin@Rotated123456!';

// ---------------------------------------------------------------------------
// Auth helpers (kept consistent with smoke.spec.js — copy/paste rather than
// import to avoid coupling the two specs through a shared helper module).
// ---------------------------------------------------------------------------

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

// Captured at runtime by getAdminToken() so uiLoginAdmin can drive the SPA
// login form with the same password the API-side helper just proved works
// (relevant when rotation is disabled and the admin is still on the
// demo/bootstrap password rather than ADMIN_ROTATED_PASSWORD).
let WORKING_ADMIN_PASSWORD = ADMIN_ROTATED_PASSWORD;

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
  // Demo-mode fallback — matches docker-compose.yml's
  // PEX_ADMIN_BOOTSTRAP_PASSWORD when instance/config.json is not reachable
  // from the Playwright container (named volume mount).
  candidates.push('Admin@Demo123!');

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
    usedPassword = ADMIN_ROTATED_PASSWORD;
  }

  WORKING_ADMIN_PASSWORD = usedPassword;
  return token;
}

/**
 * Register a fresh user and return { username, userId, token, headers }.
 * Optionally tops up credits and submits + approves a verification document.
 */
async function provisionUser(request, adminHeaders, opts = {}) {
  const suffix = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const username = `${opts.usernamePrefix || 'e2e_user'}_${suffix}`;
  const email = `${username}@test.local`;

  const reg = await request.post('/api/auth/register', {
    data: { username, email, password: USER_PASSWORD },
  });
  expect(reg.ok()).toBeTruthy();
  const userId = (await reg.json()).user_id;

  const lg = await login(request, username, USER_PASSWORD);
  expect(lg.resp.ok()).toBeTruthy();
  const token = lg.data.token;
  const headers = { Authorization: `Bearer ${token}` };

  if (opts.credit) {
    const credit = await request.post('/api/ledger/credit', {
      headers: adminHeaders,
      data: { user_id: userId, amount: opts.credit, description: 'e2e seed' },
    });
    expect(credit.ok()).toBeTruthy();
  }

  if (opts.verify) {
    const submit = await request.post('/api/verification/submit', {
      headers,
      multipart: {
        document_type: 'passport',
        document: {
          name: 'doc.pdf',
          mimeType: 'application/pdf',
          buffer: Buffer.from('%PDF-1.4 e2e admin_flows'),
        },
      },
    });
    if (submit.status() === 201) {
      const vid = (await submit.json()).verification_id;
      const review = await request.put(`/api/verification/${vid}/review`, {
        headers: adminHeaders,
        data: { decision: 'verified', notes: 'e2e auto-approve' },
      });
      expect(review.ok()).toBeTruthy();
    }
  }

  return { username, userId, token, headers };
}

/**
 * Drive the SPA login form — used by tests that want to exercise the UI as
 * the admin (rather than just the API). Asserts that the post-login state
 * includes the logout button.
 */
async function uiLoginAdmin(page) {
  await page.goto('/');
  await page.fill('#l-user', 'admin');
  await page.fill('#l-pass', WORKING_ADMIN_PASSWORD);
  await page.click('#btn-login');
  await expect(page.locator('#btn-logout')).toBeVisible();
}

// ---------------------------------------------------------------------------
// 1. Violation Appeals Flow
// ---------------------------------------------------------------------------

test('appeals flow: user files appeal -> admin resolves via SPA (upheld)', async ({ page, request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable. Set PEX_ADMIN_PASSWORD to run admin_flows tests.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  // --- Setup: two users (reporter + target), violation, appeal ---
  const reporter = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'apl_rep', credit: 200 });
  const target = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'apl_tgt', credit: 200 });

  // Reporter files a violation
  const violationResp = await request.post('/api/reputation/violations', {
    headers: reporter.headers,
    data: {
      user_id: target.userId,
      violation_type: 'spam',
      severity: 'medium',
      description: 'e2e: filing spam violation for appeals flow',
    },
  });
  expect(violationResp.status()).toBe(201);
  const violationId = (await violationResp.json()).violation_id;

  // Target files an appeal
  const appealResp = await request.post(
    `/api/reputation/violations/${violationId}/appeal`,
    {
      headers: target.headers,
      data: { reason: 'I dispute this — I never sent that message.' },
    });
  expect(appealResp.status()).toBe(201);
  const appealId = (await appealResp.json()).appeal_id;

  // --- UI: admin resolves the appeal as "upheld" through the SPA ---
  await uiLoginAdmin(page);
  await page.click('a[data-view="admin-violations"]');
  await expect(page.locator('h2:has-text("Violations & Appeals")')).toBeVisible();

  // Switch to the Appeals tab
  await page.click('#vtab-a');
  // Match the row by the unique appellant username rather than the appeal
  // id — earlier tests populate the appeals list, so ordering by id DESC
  // plus per_page=20 can push our row onto page 2. Username is unique
  // per test run (Date.now() + random suffix) and rendered into every row.
  const appealRow = page.locator(`tr:has-text("${target.username}")`);
  await expect(appealRow.first()).toBeVisible();

  // Click "Uphold" — opens the modal
  // The button's onclick calls App.resolveAppeal(${a.id},'upheld')
  await appealRow.first().locator('button:has-text("Uphold")').click();
  await expect(page.locator('h3:has-text("Uphold Appeal")')).toBeVisible();
  await page.fill('#appeal-notes', 'e2e: appeal upheld by Playwright admin_flows test');
  await page.click('#btn-confirm-appeal');

  // --- Verify: appeal status is now "upheld" via API ---
  await expect.poll(async () => {
    const list = await request.get('/api/admin/appeals?per_page=100', {
      headers: adminHeaders,
    });
    const data = await list.json();
    const ours = (data.appeals || []).find(a => a.id === appealId);
    return ours ? ours.status : 'pending';
  }, {
    message: 'appeal status should flip to upheld within timeout',
    timeout: 5_000,
  }).toBe('upheld');
});

test('appeals flow: admin denies an appeal via SPA', async ({ page, request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const reporter = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'dny_rep', credit: 200 });
  const target = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'dny_tgt', credit: 200 });

  const v = await request.post('/api/reputation/violations', {
    headers: reporter.headers,
    data: { user_id: target.userId, violation_type: 'harassment',
            severity: 'high', description: 'e2e: harassment for deny path' },
  });
  const violationId = (await v.json()).violation_id;
  const a = await request.post(
    `/api/reputation/violations/${violationId}/appeal`,
    { headers: target.headers, data: { reason: 'frivolous' } });
  const appealId = (await a.json()).appeal_id;

  await uiLoginAdmin(page);
  await page.click('a[data-view="admin-violations"]');
  await page.click('#vtab-a');

  // Match by unique target username (same reason as the upheld flow above).
  const appealRow = page.locator(`tr:has-text("${target.username}")`);
  await expect(appealRow.first()).toBeVisible();
  await appealRow.first().locator('button:has-text("Deny")').click();
  await page.fill('#appeal-notes', 'e2e: appeal denied — evidence supports report');
  await page.click('#btn-confirm-appeal');

  await expect.poll(async () => {
    const list = await request.get('/api/admin/appeals?per_page=100', {
      headers: adminHeaders,
    });
    const ours = ((await list.json()).appeals || []).find(x => x.id === appealId);
    return ours ? ours.status : null;
  }).toBe('denied');
});

// ---------------------------------------------------------------------------
// 2. Schedule Resource Management
//
// There is no SPA UI for /api/admin/resources at this time (admin.js does
// not register an "admin-resources" route), so this test exercises the
// REST API directly via Playwright's `request` fixture. When a UI is
// added, replace the request.* calls below with page.click()/page.fill().
// ---------------------------------------------------------------------------

test('schedule resources: admin can create, update, and deactivate', async ({ request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const tag = `e2e_${Date.now()}`;
  const building = `B-${tag}`;
  const room = `R-${tag}`;
  const slotInitial = 'mon-09-10';
  const slotUpdated = 'tue-14-15';

  // --- CREATE ---
  const create = await request.post('/api/admin/resources', {
    headers: adminHeaders,
    data: { building, room, time_slot: slotInitial },
  });
  expect(create.status()).toBe(201);
  const createBody = await create.json();
  expect(createBody).toHaveProperty('resource_id');
  const resourceId = createBody.resource_id;

  // --- LIST: confirm it is visible and active ---
  const list = await request.get(
    `/api/admin/resources?building=${building}&per_page=100`,
    { headers: adminHeaders });
  expect(list.ok()).toBeTruthy();
  const listBody = await list.json();
  const ours = (listBody.resources || []).find(r => r.id === resourceId);
  expect(ours).toBeTruthy();
  expect(ours.is_active).toBe(1);
  expect(ours.time_slot).toBe(slotInitial);

  // --- UPDATE: change the time slot ---
  const update = await request.put(`/api/admin/resources/${resourceId}`, {
    headers: adminHeaders,
    data: { time_slot: slotUpdated },
  });
  expect(update.ok()).toBeTruthy();

  const listAfter = await request.get(
    `/api/admin/resources?building=${building}&per_page=100`,
    { headers: adminHeaders });
  const updated = ((await listAfter.json()).resources || [])
    .find(r => r.id === resourceId);
  expect(updated.time_slot).toBe(slotUpdated);

  // --- DEACTIVATE (DELETE soft-deactivates is_active=0) ---
  const deactivate = await request.delete(`/api/admin/resources/${resourceId}`, {
    headers: adminHeaders,
  });
  expect(deactivate.ok()).toBeTruthy();

  // Filter is_active=0 to be sure the row is still there but flipped off
  const inactiveList = await request.get(
    `/api/admin/resources?building=${building}&is_active=0&per_page=100`,
    { headers: adminHeaders });
  const deactivated = ((await inactiveList.json()).resources || [])
    .find(r => r.id === resourceId);
  expect(deactivated).toBeTruthy();
  expect(deactivated.is_active).toBe(0);
});

test('schedule resources: non-admin is forbidden', async ({ request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const user = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'rsrc_usr' });
  const create = await request.post('/api/admin/resources', {
    headers: user.headers,
    data: { building: 'X', room: 'Y', time_slot: 'wed-10-11' },
  });
  expect(create.status()).toBe(403);
});

// ---------------------------------------------------------------------------
// 3. Verification Workflow
//
// Mixes UI + API:
//   - User registration / verification submission via API (faster setup)
//   - Admin reviews the queue in the SPA (renderAdminVerifications)
//   - Admin fetches the decrypted document via the API endpoint
//     (no UI button exists for this admin-only viewer at present)
//   - Admin clicks "Approve" in the SPA modal
// ---------------------------------------------------------------------------

test('verification workflow: user submits -> admin views document and approves via SPA', async ({ page, request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  // --- Setup: register a user, submit a verification document ---
  const docBytes = Buffer.from('%PDF-1.4 e2e-verification-payload-' + Date.now());
  const user = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'ver_flow' });

  const submit = await request.post('/api/verification/submit', {
    headers: user.headers,
    multipart: {
      document_type: 'national_id',
      document: {
        name: 'doc.pdf',
        mimeType: 'application/pdf',
        buffer: docBytes,
      },
    },
  });
  expect(submit.status()).toBe(201);
  const submitBody = await submit.json();
  const vid = submitBody.verification_id;

  // --- Admin uses the document viewer to fetch the decrypted payload ---
  // (audit-logged on the server; we verify the bytes match what was uploaded)
  const docResp = await request.get(`/api/verification/${vid}/document`, {
    headers: adminHeaders,
  });
  expect(docResp.status()).toBe(200);
  expect(docResp.headers()['content-type']).toContain('application/pdf');
  const fetched = await docResp.body();
  expect(Buffer.compare(fetched, docBytes)).toBe(0);

  // --- Admin reviews the SPA queue and approves via the UI ---
  await uiLoginAdmin(page);
  await page.click('a[data-view="admin-verifications"]');
  await expect(page.locator('h2:has-text("Identity Verifications")')).toBeVisible();

  // The pending row contains both the username and a row-id of #<vid>
  const verRow = page.locator(`tr:has(td:text-is("#${vid}"))`);
  await expect(verRow).toBeVisible();
  await expect(verRow).toContainText(user.username);

  await verRow.locator('button:has-text("Approve")').click();
  await expect(page.locator('h3:has-text("Approve Verification")')).toBeVisible();
  await page.fill('#ver-notes', 'e2e: verified via Playwright admin_flows');
  await page.click('#btn-confirm-ver');

  // --- Verify: the user-side status endpoint reflects the approval ---
  await expect.poll(async () => {
    const status = await request.get('/api/verification/status', {
      headers: user.headers,
    });
    const data = await status.json();
    return data.verification ? data.verification.status : 'unknown';
  }, {
    message: 'verification should flip to verified after admin approval',
    timeout: 5_000,
  }).toBe('verified');
});

test('verification workflow: admin can reject via SPA', async ({ page, request }) => {
  const adminToken = await getAdminToken(request);
  test.skip(!adminToken, 'Admin credentials unavailable.');
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };

  const user = await provisionUser(request, adminHeaders,
    { usernamePrefix: 'ver_rej' });
  const submit = await request.post('/api/verification/submit', {
    headers: user.headers,
    multipart: {
      document_type: 'utility_bill',
      document: {
        name: 'doc.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.4 e2e-reject-' + Date.now()),
      },
    },
  });
  const vid = (await submit.json()).verification_id;

  await uiLoginAdmin(page);
  await page.click('a[data-view="admin-verifications"]');

  const verRow = page.locator(`tr:has(td:text-is("#${vid}"))`);
  await expect(verRow).toBeVisible();
  await verRow.locator('button:has-text("Reject")').click();
  await expect(page.locator('h3:has-text("Reject Verification")')).toBeVisible();
  await page.fill('#ver-notes', 'e2e: document is illegible');
  await page.click('#btn-confirm-ver');

  await expect.poll(async () => {
    const status = await request.get('/api/verification/status', {
      headers: user.headers,
    });
    const data = await status.json();
    return data.verification ? data.verification.status : 'unknown';
  }).toBe('rejected');
});
