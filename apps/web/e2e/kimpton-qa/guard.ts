/**
 * Opt-in guard for the Kimpton QA regression suite — Sam's full manual
 * regression pass (test matrix: Fondok_Kimpton_Test_Matrix.xlsx), made
 * automatable.
 *
 * This is a SIBLING of `e2e/prod/guard.ts`, not an extension of it, and that
 * is deliberate. `prod`'s one rule is "assert presence and structure, never
 * a value" — violating that once produced a 200-run red streak (see
 * e2e/README.md). This suite's whole job is the opposite: Sam's pass
 * criteria are specific pinned numbers (Revenue $12.94M, ADR $247.08,
 * Levered IRR -3.2%, …) checked against ONE deal she maintains herself for
 * exactly this purpose. Pinning values here is correct; it would be wrong in
 * `prod`. Two projects, two rules, so neither drifts into the other's.
 *
 * Reuses `prod`'s host-pinning logic (`baseUrlIsProd` / `PROD_HOSTNAME`) —
 * same deployed app, same reason to pin the alias rather than a raw deploy
 * URL — but has its own opt-in flag and its own required env, so the two
 * suites can never be accidentally selected by the other's flag.
 */
import { PROD_HOSTNAME, baseUrlIsProd } from '../prod/guard';

export { PROD_HOSTNAME };

/** The explicit opt-in flag for this suite. Never shared with `prod`'s. */
export const OPT_IN_FLAG = 'FONDOK_E2E_KIMPTON';

export const REQUIRED_ENV: ReadonlyArray<{ name: string; purpose: string }> = [
  {
    name: 'CLERK_PUBLISHABLE_KEY',
    purpose:
      'Clerk DEVELOPMENT instance publishable key (pk_test_…) — same instance the deployed app was built against. Reused from the prod smoke by Sam’s choice; same risk profile, same key.',
  },
  {
    name: 'CLERK_SECRET_KEY',
    purpose: 'Clerk DEVELOPMENT instance secret key (sk_test_…) — mints the Testing Token that disables bot detection.',
  },
  {
    name: 'FONDOK_SMOKE_EMAIL',
    purpose: 'Identifier of the dedicated Clerk test user. Reused from the prod smoke by Sam’s choice.',
  },
  { name: 'FONDOK_SMOKE_PASSWORD', purpose: 'That user’s password. Reused from the prod smoke.' },
  {
    name: 'FONDOK_KIMPTON_DEAL_ID',
    purpose:
      'The DUPLICATE Kimpton Angler deal Sam maintains for this regression pass. NEVER the original tester deal, NEVER the reference model. This suite reads it only — Sam herself owns re-extraction, since re-extraction resets reviewed fields.',
  },
];

/** Both opt-ins present. Anything less and the `kimpton-qa` project does not exist. */
export function kimptonSmokeEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env[OPT_IN_FLAG] === '1' && baseUrlIsProd(env.PLAYWRIGHT_BASE_URL);
}

export function kimptonSmokeSkipReason(env: NodeJS.ProcessEnv = process.env): string | null {
  if (kimptonSmokeEnabled(env)) return null;
  const missing: string[] = [];
  if (env[OPT_IN_FLAG] !== '1') missing.push(`${OPT_IN_FLAG}=1`);
  if (!baseUrlIsProd(env.PLAYWRIGHT_BASE_URL)) {
    missing.push(`PLAYWRIGHT_BASE_URL=https://${PROD_HOSTNAME}`);
  }
  return `Opt-in only — set ${missing.join(' and ')}.`;
}

/** The duplicate Kimpton deal id, read at use time so this module stays side-effect free. */
export function kimptonDealId(env: NodeJS.ProcessEnv = process.env): string {
  return (env.FONDOK_KIMPTON_DEAL_ID ?? '').trim();
}
