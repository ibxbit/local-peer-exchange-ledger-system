const { defineConfig } = require('@playwright/test');

const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5000';
const startCommand = process.platform === 'win32' ? 'py -3 run.py' : 'python3 run.py';

module.exports = defineConfig({
  testDir: './e2e_tests',
  timeout: 90_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL,
    headless: true,
  },
  webServer: process.env.PEX_E2E_USE_EXTERNAL_SERVER
    ? undefined
    : {
        command: startCommand,
        url: baseURL,
        timeout: 120_000,
        reuseExistingServer: true,
      },
});
