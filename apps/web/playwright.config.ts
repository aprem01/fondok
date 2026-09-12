import { defineConfig, devices } from '@playwright/test';
import { prodSmokeEnabled } from './e2e/prod/guard';

/**
 * Playwright configuration — Fondok E2E.
 *
 * TWO servers, because the app's backend mode is a build-time constant
 * (`NEXT_PUBLIC_WORKER_URL` is inlined by Next) and the two modes exercise
 * genuinely different surfaces:
 *
 *   :3000  NEXT_PUBLIC_WORKER_URL=''            → `isWorkerConnected()` false.
 *          The API client never fetches; every tab falls back to
 *          `lib/mockData.ts`. This is the shell/wizard/navigation suite.
 *
 *   :3001  NEXT_PUBLIC_WORKER_URL='/__e2e-worker' → `isWorkerConnected()` true,
 *          pointed at a same-origin path nothing serves. Specs under
 *          `e2e/worker/` fulfil it with `page.route` (see
 *          `e2e/fixtures/workerStub.ts`), so the browser runs the REAL
 *          worker-connected code paths — sub-tab routing, the Historicals
 *          worksheet, the scenario chip row — none of which render at all in
 *          mock mode. Same-origin on purpose: no CORS preflight to stub.
 *
 * Auth in both: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy` puts
 * `src/lib/auth.ts` into its demo persona (Eshan Mehta · Brookfield Real
 * Estate). No real Clerk credentials, and no real sign-in is exercised.
 *
 * A THIRD project, `prod`, exists only when someone explicitly opts in —
 * FON-76's post-deploy smoke against one real deal in the deployed app. It is
 * not a variation of the two above: it signs in for real, reads production
 * data, and asserts nothing but presence and structure. See
 * `e2e/prod/guard.ts` for the opt-in rule and `e2e/README.md` for the whole
 * story. On a pull request `prodSmokeEnabled()` is false, so the project is
 * not in this array at all — there is no test for a stray `--project` or a
 * `.only` to select.
 *
 * Chromium-only by design — CI time matters more than browser coverage here.
 */

const MOCK_PORT = 3000;
const WORKER_PORT = 3001;

/**
 * Both opt-ins present: `FONDOK_E2E_PROD=1` AND a `PLAYWRIGHT_BASE_URL` whose
 * host is exactly the production alias. Evaluated once, here, so the guard is
 * a property of the config rather than of whoever remembers to check.
 */
const PROD_SMOKE = prodSmokeEnabled();

/**
 * An external base URL (a deployed build) means we did not choose its
 * `NEXT_PUBLIC_WORKER_URL`, so the worker-stub project has nothing to point
 * at and is left out of the run rather than failing against a server that
 * was never started.
 */
const EXTERNAL_BASE_URL = process.env.PLAYWRIGHT_BASE_URL;

const devServerEnv = {
  // Demo persona kicks in via lib/auth.ts when the key ends in _dummy.
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: 'pk_test_dummy',
};

export default defineConfig({
  testDir: './e2e',
  // Workers default — 1 in CI (the servers are shared) and 4 locally.
  workers: process.env.CI ? 1 : 4,
  retries: process.env.CI ? 2 : 0,
  // Fail fast in CI if a test silently calls test.only.
  forbidOnly: !!process.env.CI,
  /**
   * Per-test budget. Generous on purpose: `next dev` compiles a route the
   * first time it is requested, and a deal page pulling ~10 lazy chunks on a
   * cold CI box is not a 30-second operation. `e2e/global-setup.ts` warms the
   * routes before any test runs, so this ceiling should never be reached —
   * it exists so a slow box reports a real failure instead of a phantom one.
   * A red suite nobody trusts is worse than no suite: every timeout here must
   * mean something.
   */
  timeout: 90_000,
  /**
   * Two setups, run in order. The second is a no-op on every normal and CI
   * run — it returns before reading a secret unless the prod smoke is opted
   * in — and mints the Clerk Testing Token when it is.
   */
  globalSetup: ['./e2e/global-setup.ts', './e2e/prod/global-setup.ts'],
  // Reporter: list for human reading, html for triage.
  reporter: [
    ['list'],
    ['html', { outputFolder: 'e2e/.report', open: 'never' }],
  ],
  // Output: keep traces + screenshots small so the artifact upload is bounded.
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Slow CI box safety margin.
    actionTimeout: 15_000,
    navigationTimeout: 60_000,
  },
  expect: {
    timeout: 10_000,
  },
  projects: [
    {
      name: 'mock',
      // `e2e/prod/` is excluded here as well as in `worker/`: those specs sign
      // in for real and read production data, so they must never be picked up
      // by a project that points at a dev server.
      testIgnore: [/e2e[\\/]worker[\\/]/, /e2e[\\/]prod[\\/]/],
      use: {
        ...devices['Desktop Chrome'],
        baseURL: EXTERNAL_BASE_URL ?? `http://localhost:${MOCK_PORT}`,
      },
    },
    ...(PROD_SMOKE
      ? [
          {
            name: 'prod',
            testMatch: /e2e[\\/]prod[\\/].*\.spec\.ts$/,
            // Read-only against production: one retry for network flake, and
            // no retries beyond that — a smoke that passes on attempt three
            // is telling you something.
            retries: 1,
            use: {
              ...devices['Desktop Chrome'],
              // Non-null by construction: `prodSmokeEnabled()` already
              // required this to parse to the production host.
              baseURL: EXTERNAL_BASE_URL as string,
            },
          },
        ]
      : []),
    ...(EXTERNAL_BASE_URL
      ? []
      : [
          {
            name: 'worker-stub',
            testMatch: /e2e[\\/]worker[\\/].*\.spec\.ts$/,
            use: {
              ...devices['Desktop Chrome'],
              baseURL: `http://localhost:${WORKER_PORT}`,
            },
          },
        ]),
  ],
  // Spin up the local dev servers when no external base URL is set.
  webServer: EXTERNAL_BASE_URL
    ? undefined
    : [
        {
          command: `pnpm exec next dev --port ${MOCK_PORT}`,
          port: MOCK_PORT,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          env: {
            ...devServerEnv,
            // Empty worker URL forces the API client into mock mode so
            // these tests never hit a real backend.
            NEXT_PUBLIC_WORKER_URL: '',
          },
        },
        {
          command: `pnpm exec next dev --port ${WORKER_PORT}`,
          port: WORKER_PORT,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          env: {
            ...devServerEnv,
            // Same-origin path nothing serves — `e2e/worker/` specs fulfil it.
            NEXT_PUBLIC_WORKER_URL: '/__e2e-worker',
            // Own build dir so the two dev servers don't share `.next`.
            NEXT_DIST_DIR: '.next-e2e-worker',
          },
        },
      ],
});
