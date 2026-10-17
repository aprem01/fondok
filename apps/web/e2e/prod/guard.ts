/**
 * The post-deploy smoke's opt-in guard — FON-76.
 *
 * One predicate, imported by everything that needs it: `playwright.config.ts`
 * (to decide whether the `prod` project exists at all), `global-setup.ts` (to
 * decide whether to mint a Clerk testing token) and the spec itself (a
 * belt-and-braces `test.skip`). Three layers, one rule, defined once — two
 * hand-rolled copies of a guard is how a guard drifts open.
 *
 * The rule, deliberately conjunctive:
 *
 *   1. `FONDOK_E2E_PROD=1` — an explicit "I mean it". A flag cannot be
 *      satisfied by accident the way a URL can (a copied command, a staging
 *      alias, an env var left over from an earlier shell).
 *   2. `PLAYWRIGHT_BASE_URL` whose HOST is exactly the production alias.
 *      Host equality, not `includes()`: `fondok-app.vercel.app.example.test`
 *      contains the string and is not us.
 *
 * Neither alone is enough, so a pull request — which sets neither — cannot
 * select this project. `guard-check.mjs` proves that against the real config.
 *
 * WHY THE ALIAS AND NOT THE DEPLOY URL: `fondok-app.vercel.app` is a plain
 * alias over the Vercel project's auto-managed domain, promoted by
 * `.github/workflows/alias-fondok-app.yml` AFTER a deployment goes Ready.
 * A raw `fondok-<hash>-aprem01s-projects.vercel.app` URL is a bundle no user
 * is served, so a smoke that passes against one proves nothing about what the
 * analyst sees. Pinning the host here makes that structural.
 */

/** The one hostname the smoke is allowed to run against. */
export const PROD_HOSTNAME = 'fondok-app.vercel.app';

/** The explicit opt-in flag. */
export const OPT_IN_FLAG = 'FONDOK_E2E_PROD';

/**
 * Every environment variable the smoke needs, and what each one is for.
 * `global-setup.ts` fails the run naming the missing ones rather than letting
 * a spec die mid-sign-in with a Clerk error nobody can place.
 */
export const REQUIRED_ENV: ReadonlyArray<{ name: string; purpose: string }> = [
  {
    name: 'CLERK_PUBLISHABLE_KEY',
    purpose:
      'Clerk DEVELOPMENT instance publishable key (pk_test_…) — must be the same instance the deployed app was built against.',
  },
  {
    name: 'CLERK_SECRET_KEY',
    purpose:
      'Clerk DEVELOPMENT instance secret key (sk_test_…) — mints the Testing Token that disables bot detection.',
  },
  {
    name: 'FONDOK_SMOKE_EMAIL',
    purpose:
      'Identifier of the dedicated Clerk test user (a +clerk_test address), a member of the org that owns the smoke deal.',
  },
  { name: 'FONDOK_SMOKE_PASSWORD', purpose: 'That user’s password.' },
  {
    name: 'FONDOK_SMOKE_DEAL_ID',
    purpose:
      'The pinned, pre-existing deal the smoke READS. Never created, never modified.',
  },
];

/**
 * The SECOND firm the two-firm isolation spec (`02-two-firm-isolation.spec.ts`)
 * signs in as. Optional on top of `REQUIRED_ENV`: the live-deal smoke must
 * keep running when nobody has provisioned firm B yet, so these are not in
 * `REQUIRED_ENV` — the spec skips itself, naming the missing ones, and
 * `global-setup.ts` says so in its log line.
 *
 * Firm B must be a Clerk test user whose active organization is a DIFFERENT
 * org from the smoke user's — the whole point is that it owns none of firm
 * A's deals. Give it a deal or two of its own so its project list is not
 * trivially empty.
 */
export const TWO_FIRM_ENV: ReadonlyArray<{ name: string; purpose: string }> = [
  {
    name: 'FONDOK_SMOKE_B_EMAIL',
    purpose:
      'Identifier of a SECOND dedicated Clerk test user (a +clerk_test address), member of a different org from the smoke user.',
  },
  { name: 'FONDOK_SMOKE_B_PASSWORD', purpose: 'That user’s password.' },
  {
    name: 'FONDOK_SMOKE_B_ORG',
    purpose:
      'Firm B’s Clerk organization id (org_…) or slug — the spec makes it the active org and refuses to run as any other.',
  },
];

/** Both opt-ins AND every firm-B secret present. */
export function twoFirmEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return prodSmokeEnabled(env) && TWO_FIRM_ENV.every(({ name }) => (env[name] ?? '').trim() !== '');
}

/**
 * Why the two-firm spec is not running, or null when it is. Reuses the prod
 * skip reason first so a run that forgot an opt-in is told that, not "firm B
 * is missing".
 */
export function twoFirmSkipReason(env: NodeJS.ProcessEnv = process.env): string | null {
  const prod = prodSmokeSkipReason(env);
  if (prod !== null) return prod;
  const missing = TWO_FIRM_ENV.filter(({ name }) => (env[name] ?? '').trim() === '').map(({ name }) => name);
  if (missing.length === 0) return null;
  return `Two-firm isolation needs a second firm — set ${missing.join(', ')}.`;
}

/** True when `raw` is a URL whose host is exactly the production alias. */
export function baseUrlIsProd(raw: string | undefined): boolean {
  if (!raw) return false;
  try {
    return new URL(raw).hostname.toLowerCase() === PROD_HOSTNAME;
  } catch {
    return false;
  }
}

/** Both opt-ins present. Anything less and the `prod` project does not exist. */
export function prodSmokeEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env[OPT_IN_FLAG] === '1' && baseUrlIsProd(env.PLAYWRIGHT_BASE_URL);
}

/**
 * Why the smoke is not running, in a sentence a human can act on — or null
 * when it is running. Used as the `test.skip` reason so a manual invocation
 * that forgot one half is told which half.
 */
export function prodSmokeSkipReason(env: NodeJS.ProcessEnv = process.env): string | null {
  if (prodSmokeEnabled(env)) return null;
  const missing: string[] = [];
  if (env[OPT_IN_FLAG] !== '1') missing.push(`${OPT_IN_FLAG}=1`);
  if (!baseUrlIsProd(env.PLAYWRIGHT_BASE_URL)) {
    missing.push(`PLAYWRIGHT_BASE_URL=https://${PROD_HOSTNAME}`);
  }
  return `Opt-in only — set ${missing.join(' and ')}.`;
}

/** The pinned deal id, read at use time so this module stays side-effect free. */
export function smokeDealId(env: NodeJS.ProcessEnv = process.env): string {
  return (env.FONDOK_SMOKE_DEAL_ID ?? '').trim();
}
