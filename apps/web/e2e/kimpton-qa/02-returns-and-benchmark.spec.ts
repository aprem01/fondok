import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import { test, expect, openDeal, expectNoEngineFailures, expectFigureInRange, figureBeside } from './support';
import { kimptonSmokeSkipReason } from './guard';

/**
 * Known-issue regression checks: Levered IRR, Sources & Uses, benchmark
 * auto-apply. Against the DUPLICATE Kimpton deal, read-only.
 *
 * On the IRR driver bridge Sam asked for (revenue, expense ratios/benchmark,
 * PIP displacement, price, debt, exit — explained driver by driver): there
 * is no "Investment Bridge" UI surface in this codebase that decomposes IRR
 * into those drivers (checked ReturnsTab, PartnershipTab, the sensitivity
 * grids — the only grids are IRR/EM/CoC vs exit-cap-rate × RevPAR-growth
 * and a financing pair, not an attribution waterfall). That breakdown is
 * NOT automatable in this pass — it's a manual, driver-by-driver read Sam
 * does herself and logs in the matrix. What this file DOES automate is the
 * tripwire: is the headline number still catastrophically regressed, or has
 * it recovered into a sane range.
 */

const EMAIL = process.env.FONDOK_SMOKE_EMAIL ?? '';
const PASSWORD = process.env.FONDOK_SMOKE_PASSWORD ?? '';
const SKIP_REASON = kimptonSmokeSkipReason() ?? 'Opt-in only — see e2e/kimpton-qa/guard.ts.';

test.describe('@kimpton-qa Returns, Sources & Uses, benchmark safety', () => {
  test.skip(() => kimptonSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({ page, signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD } });
  });

  test('Returns — Levered IRR has recovered from the catastrophic regression (TC: KNOWN-ISSUE-IRR-0 tripwire)', async ({ page }) => {
    // Sep 15 baseline: 33.15%. Regressed readings: -3.2% (Eshan), -19.9%
    // (Rani). This does NOT assert a specific target — Sam and Prem
    // haven't agreed one post-fix — it asserts the regression floor: IRR
    // should no longer be deep-negative like the Rani reading. If this is
    // red, the regression is still live and the driver-by-driver read is
    // still needed. If green, record the actual value in the matrix and
    // confirm against Eshan/Rani's next re-test, not just this floor.
    await openDeal(page, '?tab=returns');
    const body = page.locator('body');
    const text = await figureBeside(body, 'Levered IRR');
    expect(text, '"Levered IRR" should render a figure').not.toBe('');
    const n = Number(text.replace(/[^0-9.-]/g, ''));
    expect(Number.isFinite(n), `Levered IRR "${text}" should parse to a number`).toBeTruthy();
    expect(n, `Levered IRR is ${text} — still at or below the Rani regression reading (-19.9%); driver bridge still needed`).toBeGreaterThan(-15);
    await expectNoEngineFailures(page);
  });

  test('Investment — Sources & Uses is in balance (same structural check as prod, now on the Kimpton deal)', async ({ page }) => {
    await openDeal(page, '?tab=investment&sub=sources-and-uses');
    await expect(page.getByText(/^In balance$/i), 'Sources & Uses reports its in-balance state').toBeVisible();
    await expect(page.getByText(/^Out of balance$/i)).toHaveCount(0);
    await expectNoEngineFailures(page);
  });

  test('Investment — Renovation / PIP budget is populated (TC: supports FON-89/FON-85 — confirms PIP data reached the model at all)', async ({ page }) => {
    await openDeal(page, '?tab=investment');
    const body = page.locator('body');
    await expect(body.getByText(/^Renovation \/ PIP$|^Renovation Budget$/i).first(), 'a Renovation/PIP section is visible').toBeVisible();
    await expectFigureInRange(body, 'Renovation Budget', 1, Number.MAX_SAFE_INTEGER).catch(async () => {
      // Label text may differ slightly — fall back to the totals row.
      await expectFigureInRange(body, 'Total Renovation / PIP', 1, Number.MAX_SAFE_INTEGER);
    });
  });
});
