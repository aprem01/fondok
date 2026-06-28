import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration — Wave 1 E2E suite.
 *
 * Auth bypass: the suite assumes the app is running with
 * NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy so the demo persona
 * (Eshan Mehta · Brookfield Real Estate) kicks in via src/lib/auth.ts.
 * No real Clerk credentials needed.
 *
 * Chromium-only by design — the demo persona is shared state, and CI
 * time matters more than browser coverage for regression catching.
 */
export default defineConfig({
  testDir: './e2e',
  // Workers default — 1 in CI (demo persona is shared) and 4 locally.
  workers: process.env.CI ? 1 : 4,
  retries: process.env.CI ? 2 : 0,
  // Fail fast in CI if a test silently calls test.only.
  forbidOnly: !!process.env.CI,
  // Reporter: list for human reading, html for triage.
  reporter: [
    ['list'],
    ['html', { outputFolder: 'e2e/.report', open: 'never' }],
  ],
  // Output: keep traces + screenshots small so the artifact upload is bounded.
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Slow CI box safety margin.
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  expect: {
    timeout: 7_500,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Spin up the local dev server when no external base URL is set.
  // Set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy here so demo
  // mode kicks in without requiring a real Clerk Pro/Test config.
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: 'pnpm dev',
        port: 3000,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: 'pk_test_dummy',
          // Empty worker URL forces the API client into mock mode so
          // tests never hit a real backend that might not be running.
          NEXT_PUBLIC_WORKER_URL: '',
        },
      },
});
