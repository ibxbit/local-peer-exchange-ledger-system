// Vitest configuration for client-side JS unit tests.
//
// Tests live under static/js/__tests__/ and import the SPA modules from
// static/js/ directly (api.js, etc.). We need a DOM environment because
// api.js touches sessionStorage and window.location; happy-dom is a fast,
// lightweight implementation that works well for these isolated unit tests.
//
// The Playwright/E2E specs live under e2e_tests/ and are explicitly
// excluded from the Vitest run so the two test runners stay decoupled.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    include: ['static/js/__tests__/**/*.test.js'],
    exclude: ['node_modules/**', 'e2e_tests/**', 'test-results/**'],
    globals: false,
    clearMocks: true,
  },
});
