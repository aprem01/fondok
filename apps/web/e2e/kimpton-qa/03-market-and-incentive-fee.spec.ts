import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import { test, expect, openDeal, expectNoEngineFailures, worksheetRow } from './support';
import { kimptonSmokeSkipReason } from './guard';

/**
 * MPI/ARI Year-1-only target (D-ref: single MPI/ARI target applies to Year 1
 * only) and the incentive management fee contradiction between Prem's Oct 15
 * and Oct 17 emails.
 */

const EMAIL = process.env.FONDOK_SMOKE_EMAIL ?? '';
const PASSWORD = process.env.FONDOK_SMOKE_PASSWORD ?? '';
const SKIP_REASON = kimptonSmokeSkipReason() ?? 'Opt-in only — see e2e/kimpton-qa/guard.ts.';

test.describe('@kimpton-qa Market Index Analysis & Incentive Fee', () => {
  test.skip(() => kimptonSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({ page, signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD } });
  });

  test('Market — Index Analysis renders MPI / ARI / RGI with figures (TC: MPI-ARI structural)', async ({ page }) => {
    await openDeal(page, '?tab=market');
    const body = page.locator('body');
    for (const label of ['Occupancy Index / MPI', 'ADR Index / ARI', 'RevPAR Index / RGI']) {
      await expect(body.getByText(new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i')).first(), `"${label}" is visible`).toBeVisible({ timeout: 45_000 });
    }
    await expectNoEngineFailures(page);
    // NOTE — "single target, Year 1 only" is a product decision about
    // SCOPE (does the UI let you set/see a different target per year?), not
    // a figure this spec can check without knowing how many year-columns the
    // Index Analysis section renders live. Manual check: open Index Analysis
    // and confirm there is exactly ONE MPI/ARI/RGI target input, not one per
    // projection year. Log the result in the matrix under D3/D15.
  });

  test('P&L — Incentive Management Fee line (TC: D9 — Prem Oct 15 vs Oct 17 contradiction)', async ({ page }) => {
    // The ontology defines `incentive_mgmt_fee` / "Incentive Management Fee"
    // as its own P&L concept, but the rendered Historicals/Projections
    // worksheet (GroundedWorksheet.tsx ROWS) currently has only ONE fee row:
    // "Management Fee" — no separate "Incentive Management Fee" row. That
    // absence IS evidence relevant to D9: if Prem's Oct 17 email claims the
    // incentive fee now flows through, it is not visible as its own
    // worksheet line as of this run. This test records which is actually
    // true rather than assuming either email.
    await openDeal(page, '?tab=pl&sub=historicals');
    const incentiveRow = page.locator('tr', { hasText: /incentive/i });
    const hasIncentiveRow = (await incentiveRow.count()) > 0;
    if (hasIncentiveRow) {
      await expect(incentiveRow.first(), 'an Incentive Management Fee row is visible').toBeVisible();
    } else {
      // Fall back to confirming the single "Management Fee" row at least
      // exists and is populated — and surface the absence as the finding.
      const mgmtFee = await worksheetRow(page, 'Management Fee');
      expect(
        mgmtFee.length,
        '[D9 evidence] no separate "Incentive Management Fee" row found on the worksheet — '
          + 'only a single "Management Fee" row exists. Confirm with Prem whether the incentive '
          + 'component is meant to be broken out here or whether it is computed elsewhere '
          + '(e.g. Partnership waterfall) before treating either Oct 15 or Oct 17 email as current.',
      ).toBeGreaterThan(0);
    }
  });
});
