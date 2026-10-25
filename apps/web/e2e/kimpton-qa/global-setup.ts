import type { FullConfig } from '@playwright/test';
import { OPT_IN_FLAG, REQUIRED_ENV, kimptonSmokeEnabled, kimptonSmokeSkipReason } from './guard';

/**
 * Global setup for the Kimpton QA regression suite.
 *
 * Mirrors `e2e/prod/global-setup.ts` exactly in mechanism — mints a Clerk
 * Testing Token so sign-in never drives a password field against the Clerk
 * DEVELOPMENT instance's bot detection — but is gated on this suite's own
 * opt-in (`FONDOK_E2E_KIMPTON=1`), so it is a no-op on every normal run, every
 * CI run, and every `prod` smoke run.
 */
export default async function kimptonGlobalSetup(_config: FullConfig): Promise<void> {
  if (!kimptonSmokeEnabled()) {
    return;
  }

  const missing = REQUIRED_ENV.filter(({ name }) => !(process.env[name] ?? '').trim());
  if (missing.length > 0) {
    throw new Error(
      [
        `[kimpton-qa] ${OPT_IN_FLAG}=1 was set, but ${missing.length} required `
          + `environment variable${missing.length === 1 ? ' is' : 's are'} missing:`,
        ...missing.map(({ name, purpose }) => `  • ${name} — ${purpose}`),
        '',
        'See apps/web/e2e/kimpton-qa/README.md for how these are provisioned.',
      ].join('\n'),
    );
  }

  const publishableKey = (process.env.CLERK_PUBLISHABLE_KEY ?? '').trim();
  const secretKey = (process.env.CLERK_SECRET_KEY ?? '').trim();

  if (publishableKey.startsWith('pk_live_') || secretKey.startsWith('sk_live_')) {
    throw new Error(
      '[kimpton-qa] Refusing to run with Clerk PRODUCTION instance keys. This '
        + 'suite signs in as a throwaway test user on the DEVELOPMENT instance '
        + '(pk_test_… / sk_test_…) only.',
    );
  }

  const { clerkSetup } = await import('@clerk/testing/playwright');
  await clerkSetup({ publishableKey, secretKey, dotenv: false });

  // eslint-disable-next-line no-console
  console.log(
    `[kimpton-qa] Clerk testing token minted. Reading/pinning deal ${process.env.FONDOK_KIMPTON_DEAL_ID} `
      + `at ${process.env.PLAYWRIGHT_BASE_URL} — read-only (Sam owns re-extraction).`,
  );
}

export { kimptonSmokeSkipReason };
