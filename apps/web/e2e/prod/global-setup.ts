import type { FullConfig } from '@playwright/test';
import { OPT_IN_FLAG, REQUIRED_ENV, prodSmokeEnabled, prodSmokeSkipReason } from './guard';

/**
 * Global setup for the `prod` post-deploy smoke — FON-76.
 *
 * Runs alongside `e2e/global-setup.ts` (the config lists both), and is a
 * NO-OP on every normal and CI run: without both opt-ins the `prod` project
 * does not exist, there is nothing to set up, and this returns before it
 * touches Clerk or reads a single secret.
 *
 * When the smoke IS enabled it does exactly two things, both before any
 * browser starts:
 *
 * 1. Fails loudly and specifically on a missing secret. A smoke that dies
 *    halfway through a sign-in with `Cannot read properties of undefined`
 *    gets triaged as "Clerk is flaky" and then switched off.
 *
 * 2. Calls Clerk's `clerkSetup()`, which mints a **Testing Token** from the
 *    secret key and exports it as `CLERK_TESTING_TOKEN` / `CLERK_FAPI` in
 *    `process.env` — Playwright's workers are spawned after global setup and
 *    inherit it. That token is the whole reason this is possible: Fondok is
 *    on a Clerk DEVELOPMENT instance, where a scripted headless sign-in
 *    otherwise trips bot detection. We do not drive the sign-in FORM at all;
 *    `clerk.signIn()` in the spec talks to the loaded Clerk client directly.
 *
 * DEVELOPMENT INSTANCE KEYS ONLY. `clerkSetup()` refuses a production secret
 * key on its own, but the check below is explicit and covers the publishable
 * key too, so the rule is stated where a maintainer reads it rather than
 * discovered from a stack trace. The blast radius of a leaked dev-instance
 * key must stay "a test tenant", and the smoke user owns nothing but its org
 * membership.
 */
export default async function prodGlobalSetup(_config: FullConfig): Promise<void> {
  if (!prodSmokeEnabled()) {
    // Not an error, and deliberately silent: this is the path every PR,
    // every push and every laptop run takes.
    return;
  }

  const missing = REQUIRED_ENV.filter(({ name }) => !(process.env[name] ?? '').trim());
  if (missing.length > 0) {
    throw new Error(
      [
        `[prod smoke] ${OPT_IN_FLAG}=1 was set, but ${missing.length} required `
          + `environment variable${missing.length === 1 ? ' is' : 's are'} missing:`,
        ...missing.map(({ name, purpose }) => `  • ${name} — ${purpose}`),
        '',
        'See apps/web/e2e/README.md § "The post-deploy production smoke" for how',
        'these are provisioned. Nothing here is committed; all of them are',
        'GitHub Actions secrets.',
      ].join('\n'),
    );
  }

  const publishableKey = (process.env.CLERK_PUBLISHABLE_KEY ?? '').trim();
  const secretKey = (process.env.CLERK_SECRET_KEY ?? '').trim();

  if (publishableKey.startsWith('pk_live_') || secretKey.startsWith('sk_live_')) {
    throw new Error(
      '[prod smoke] Refusing to run with Clerk PRODUCTION instance keys. This '
        + 'smoke signs in as a throwaway test user on the DEVELOPMENT instance '
        + '(pk_test_… / sk_test_…), which is what makes a leaked key survivable. '
        + 'Fix the CLERK_PUBLISHABLE_KEY / CLERK_SECRET_KEY secrets.',
    );
  }

  // Imported lazily so a normal run never loads it — the package is only
  // needed on the opt-in path, and the config imports this module eagerly.
  const { clerkSetup } = await import('@clerk/testing/playwright');
  await clerkSetup({
    publishableKey,
    secretKey,
    // Never read a local .env: the keys are explicit above and already
    // validated. A stray .env that quietly supplied a live key is exactly
    // the accident the check above exists to prevent.
    dotenv: false,
  });

  // eslint-disable-next-line no-console
  console.log(
    `[prod smoke] Clerk testing token minted. Reading deal ${process.env.FONDOK_SMOKE_DEAL_ID} `
      + `at ${process.env.PLAYWRIGHT_BASE_URL} — read-only.`,
  );
}

/** Re-exported so a failing skip reason is greppable from one place. */
export { prodSmokeSkipReason };
