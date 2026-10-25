import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import {
  test, expect, openDeal, expectNoEngineFailures,
  worksheetRow, expectHistoricalInRange, expectWorksheetColumnInRange,
  expectFigureInRange, parseNumber,
} from './support';
import { kimptonSmokeSkipReason } from './guard';

/**
 * Sam's three hard pass criteria for this regression pass (source: "Open
 * questions before Eshan and Rani re-test", Oct 19). An item only counts as
 * done when a retest is logged Pass — these specs are that retest, automated.
 *
 *   1. Historicals match the source EXACTLY: Revenue $12.94M, ADR $247.08,
 *      RevPAR $203.57.
 *   2. NOI is $2.5–4M (~25% margin).
 *   3. Year 1 ADR is $275–290.
 *
 * All three run against the DUPLICATE Kimpton Angler deal
 * (FONDOK_KIMPTON_DEAL_ID) — never the original tester deal, never the
 * reference model. Read-only: no override, no re-run, no upload.
 */

const EMAIL = process.env.FONDOK_SMOKE_EMAIL ?? '';
const PASSWORD = process.env.FONDOK_SMOKE_PASSWORD ?? '';
const SKIP_REASON = kimptonSmokeSkipReason() ?? 'Opt-in only — see e2e/kimpton-qa/guard.ts.';

test.describe('@kimpton-qa Pass Criteria 1 — Historicals match source exactly', () => {
  test.skip(() => kimptonSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({ page, signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD } });
  });

  test('Historicals — Total Revenue reads ~$12.94M (TC: PASS-CRITERIA-1a)', async ({ page }) => {
    await openDeal(page, '?tab=pl&sub=historicals');
    // $5k tolerance either side of the $12.94M Sam quoted — she almost
    // certainly rounded to the hundredth of a million; this is not loosening
    // the criterion, it's not over-pinning a digit she never gave us.
    await expectHistoricalInRange(page, 'Total Revenue', 12_935_000, 12_945_000);
    await expectNoEngineFailures(page);
  });

  test('Historicals — ADR reads $247.08 (TC: PASS-CRITERIA-1b)', async ({ page }) => {
    await openDeal(page, '?tab=pl&sub=historicals');
    await expectHistoricalInRange(page, 'ADR', 247.07, 247.09);
  });

  test('Historicals — RevPAR reads $203.57 (TC: PASS-CRITERIA-1c)', async ({ page }) => {
    await openDeal(page, '?tab=pl&sub=historicals');
    await expectHistoricalInRange(page, 'RevPAR', 203.56, 203.58);
  });

  test('Historicals — FON-90 tripwire: does Total Revenue still equal Rooms+F&B+Other? (TC: FON-90)', async ({ page }) => {
    // FON-90 (live, Ready for QA by Team as of Oct 19): "Historical P&L: Total
    // Revenue row derives rooms + F&B + other instead of the statement's
    // stated total." This doesn't prove the bug — we don't have the
    // source statement's own stated total to diff against here — but it
    // DOES tell you whether the derivation is still happening. If this test
    // starts failing (the sum stops matching), that's a signal the fix
    // shipped and changed behavior — go verify against the real T-12,
    // don't just mark it green and move on.
    await openDeal(page, '?tab=pl&sub=historicals');
    const [rooms, fb, other, total] = await Promise.all([
      worksheetRow(page, 'Rooms Revenue'),
      worksheetRow(page, 'Food & Beverage Revenue'),
      worksheetRow(page, 'Other Revenue'),
      worksheetRow(page, 'Total Revenue'),
    ]);
    // Compare column-by-column (same column = same period).
    for (const col of total) {
      const r = rooms.find((c) => c.column === col.column);
      const f = fb.find((c) => c.column === col.column);
      const o = other.find((c) => c.column === col.column);
      const totalN = parseNumber(col.value);
      if (totalN == null || !r || !f || !o) continue;
      const sumN = (parseNumber(r.value) ?? 0) + (parseNumber(f.value) ?? 0) + (parseNumber(o.value) ?? 0);
      expect(
        Math.abs(totalN - sumN),
        `[FON-90 evidence, column "${col.column}"] Total Revenue=${col.value}, `
          + `Rooms+F&B+Other=${sumN} — if these stop matching, FON-90's fix likely shipped; `
          + `confirm Total Revenue against the source statement's own stated total before closing it`,
      ).toBeLessThan(1);
    }
  });
});

test.describe('@kimpton-qa Pass Criteria 2 & 3 — Year 1 projections', () => {
  test.skip(() => kimptonSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({ page, signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD } });
  });

  test('Projections — Year 1 NOI is $2.5–4M, ~25% margin (TC: PASS-CRITERIA-2)', async ({ page }) => {
    await openDeal(page, '?tab=pl&sub=projections');
    // Projections renders its own layout (not the Historicals worksheet
    // table), so try the KPI-tile sibling pattern first; fall back to a
    // worksheet-style row/column read if the page turns out to use a table
    // here too. Whichever succeeds, we still assert the pinned range.
    const body = page.locator('body');
    const label = page.getByText(/^Net Operating Income \(NOI\)$|^NOI$/i).first();
    if (await label.count() > 0) {
      await expectFigureInRange(body, await label.innerText(), 2_500_000, 4_000_000);
    } else {
      await expectWorksheetColumnInRange(page, 'Net Operating Income (NOI)', /year ?1/i, 2_500_000, 4_000_000);
    }
    await expectNoEngineFailures(page);
  });

  test('Projections — Year 1 ADR is $275–290 (TC: PASS-CRITERIA-3; doubles as the FON-89/FON-85 PIP-displacement tripwire)', async ({ page }) => {
    // D12 / FON-89 / FON-85: a saved renovation-budget override is supposed
    // to displace Year 1 occupancy & ADR per Sam's Oct 16 decision, but two
    // known bugs can make that displacement apply when it shouldn't (or not
    // reconcile correctly). This range IS that tripwire in practice — if
    // PIP displacement is still mis-applying, Year 1 ADR will land outside
    // $275–290 and this goes red.
    await openDeal(page, '?tab=pl&sub=projections');
    const body = page.locator('body');
    const label = page.getByText(/^ADR$|^Average Rate$/i).first();
    if (await label.count() > 0) {
      await expectFigureInRange(body, await label.innerText(), 275, 290);
    } else {
      await expectWorksheetColumnInRange(page, 'ADR', /year ?1/i, 275, 290);
    }
  });
});
