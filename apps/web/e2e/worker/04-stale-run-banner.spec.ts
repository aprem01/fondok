import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker, STUB_DEAL_ID } from '../fixtures/workerStub';

/**
 * FON-75 — the page-level stale-run banner, in a real browser.
 *
 * On 2026-09-12 a live deal opened with the whole Stabilization section of
 * Overview rendering five dashes. Nothing was broken: the deal's persisted
 * engine output had been written before `expense.stabilization` existed, and
 * no surface said so. Two banners now cover that — the section banner
 * (`stabilization-needs-rerun`, locked by `08-overview-noi-basis.spec.ts`) is
 * the specific message where the dashes are; this page-level one is the
 * general net, driven by `stale_run.missing_blocks` off the engines endpoint,
 * so the NEXT block addition is covered the day it deploys.
 *
 * This runs on the `worker-stub` project because the banner only mounts when
 * `isWorkerConnected()` is true and the deal id is a real (non-mock) one — the
 * mock project at :3000 gates both banners off, which is exactly the reason
 * `08-` cannot assert this half.
 *
 * Route ordering note: Playwright runs the most recently added handler first,
 * so the per-test `/engines` route below wins over `stubWorker`'s catch-all.
 * Nothing in the shared fixture changes.
 */

const ENGINES = `**/deals/${STUB_DEAL_ID}/engines`;

/** The published block. Values are shape, not expectations — nothing asserts them. */
const STABILIZATION = {
  stabilized_year_index: 1,
  stabilized_year: 2,
  source: 'fondok_derived',
  signal: 'occupancy',
  derived_year: 2,
  stabilized_occupancy: 0.771,
  stabilized_adr: 401.25,
  stabilized_revenue: 12_840_000,
  stabilized_noi_before_reserve: 4_355_000,
  stabilized_cash_noi: 3_802_387,
  stabilized_noi_margin: 0.339,
};

/**
 * One expense row, with or without the stabilization block, plus whatever the
 * worker's read-time freshness check would have concluded about it.
 * `stale_run: null` and a missing key never occur together in production —
 * that is the whole point of the detector — so the fixture keeps them paired.
 */
function enginesPayload(opts: { stale: boolean }) {
  const outputs: Record<string, unknown> = {
    years: [{ year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 }],
    noi_cagr: 0.05,
  };
  if (!opts.stale) outputs.stabilization = STABILIZATION;
  return {
    deal_id: STUB_DEAL_ID,
    engines: {
      expense: {
        deal_id: STUB_DEAL_ID,
        engine: 'expense',
        status: 'complete',
        summary: '',
        outputs,
        inputs: {},
        error: null,
        runtime_ms: 1,
        started_at: null,
        completed_at: null,
        run_id: 'e2e-run',
      },
    },
    stale_run: opts.stale
      ? { reason: 'stale_run', missing_blocks: { expense: ['stabilization'] } }
      : null,
  };
}

test.describe('FON-75 — a run that predates a published block', () => {
  test('both banners appear, and the page one names the section', async ({ page }) => {
    await stubWorker(page);
    await page.route(ENGINES, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesPayload({ stale: true })),
      }),
    );

    await gotoDeal(page, '?tab=overview');

    // The general net — page level, above the tab content.
    const pageBanner = page.getByTestId('stale-run-banner');
    await expect(pageBanner).toBeVisible();
    // It must name a section a reader recognises, not the engine field path.
    // (The wording around it is not pinned here — `__tests__/staleRunBanner
    // .test.tsx` checks the copy against the ReasonCode module, so product
    // prose can change without turning this spec red. See e2e/README.md.)
    await expect(pageBanner.getByTestId('stale-run-sections')).toContainText('Stabilization');
    await expect(pageBanner).not.toContainText('expense.stabilization');
    // And it must offer the fix, not just the diagnosis.
    await expect(pageBanner.getByRole('button', { name: /re-run/i })).toBeVisible();

    // The specific message, still where the dashes are.
    const section = page.getByTestId('overview-section-stabilization');
    await expect(section.getByTestId('stabilization-needs-rerun')).toBeVisible();
  });

  test('a current run shows neither banner', async ({ page }) => {
    await stubWorker(page);
    await page.route(ENGINES, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesPayload({ stale: false })),
      }),
    );

    await gotoDeal(page, '?tab=overview');

    // Anchor on something that does render, so an empty page cannot pass this.
    await expect(page.getByTestId('overview-section-stabilization')).toBeVisible();
    await expect(page.getByTestId('stale-run-banner')).toHaveCount(0);
    await expect(page.getByTestId('stabilization-needs-rerun')).toHaveCount(0);
  });
});
