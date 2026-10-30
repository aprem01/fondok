import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker } from '../fixtures/workerStub';

/**
 * Overview — NOI basis labelling, and what the Stabilization block says when
 * the run published no stabilized year.
 *
 * Both of these are about the app being honest on a screen full of dashes.
 *
 * 1. NOI basis. Fondok has two NOI figures and they are NOT interchangeable:
 *    NOI before the FF&E replacement reserve (`expense.years[].
 *    noi_institutional`) and Cash NOI after it (`expense.years[].noi`, what
 *    Debt sizes on and Returns capitalises). A row labelled a bare "NOI"
 *    leaves the reader to guess which, so no row is allowed to be one —
 *    see `src/lib/engines/noi.ts` for the canonical strings.
 *
 * 2. The re-run instruction. On 2026-09-12 a live deal showed five dashes
 *    across Stabilized Occupancy / ADR / Revenue / NOI / Margin because its
 *    persisted engine output predated the stabilization block. The dashes
 *    were correct — Fondok refuses to substitute another projection year —
 *    but nothing on screen said re-running the model would fill them in, and
 *    the investigation blamed the engine instead. The banner is the fix; this
 *    locks it.
 *
 * SCOPE, honestly: the stubbed worker's expense output carries no
 * `stabilization` block, so `stabilizedYearBlock()` is null for this deal.
 * That makes "renders dashes" close to trivially true here — it is
 * `__tests__/stabilizedParity.test.tsx` that proves the dash is chosen over a
 * borrowed year when a stabilized year IS present. What this spec adds is
 * that the banner reaches a real browser, on the real route, next to the real
 * rows — which is exactly the part that was missing in production.
 *
 * Moved from the `mock` project on 2026-10-30 (go-live Wave 0, C4): it used to
 * open the fictitious sample deal (id=7) the worker-less build rendered. That
 * fixture is gone, so this runs on the `worker-stub` project against the
 * stubbed deal, with `stubWorker` installed before each navigation.
 */

const OVERVIEW = '?tab=overview';

/** Exact-text labels that would leave the reader guessing which NOI it is. */
const AMBIGUOUS_NOI_LABELS = [
  'NOI',
  'Entry NOI',
  'Run-Rate NOI',
  'Forward NOI',
  'Forward 12-Month NOI',
];

test.describe('Overview — NOI basis is always stated', () => {
  test.beforeEach(async ({ page }) => {
    await stubWorker(page);
  });

  test('Entry NOI is before-reserve and the exit figure is Cash NOI', async ({ page }) => {
    await gotoDeal(page, OVERVIEW);

    await expect(
      page.getByText('Run-Rate / Entry NOI (before FF&E reserve)'),
    ).toBeVisible();
    // FON-41 R-059 — the exit figure is labelled by its basis: "Exit NOI (<period>)"
    // when the run states the period, else the bare EXIT_NOI_LABEL. Either way it
    // names Cash NOI after the FF&E reserve, never a bare "NOI".
    await expect(page.getByText(/^Exit NOI\b/).first()).toBeVisible();
  });

  test('no row is labelled a bare, unqualified "NOI"', async ({ page }) => {
    await gotoDeal(page, OVERVIEW);
    // Anchor on a row we know renders, so an empty page can't pass this.
    await expect(page.getByText('Entry Cap Rate')).toBeVisible();

    for (const label of AMBIGUOUS_NOI_LABELS) {
      await expect(
        page.getByText(label, { exact: true }),
        `"${label}" does not say which NOI basis it is`,
      ).toHaveCount(0);
    }
  });
});

test.describe('Overview — a Stabilization block with no published year', () => {
  test.beforeEach(async ({ page }) => {
    await stubWorker(page);
  });

  test('every Stabilization row is a dash and the banner says to re-run', async ({ page }) => {
    await gotoDeal(page, OVERVIEW);

    // Scope to the section — "Stabilized NOI" is also a KPI tile label.
    const stabilization = page.getByTestId('overview-section-stabilization');
    await expect(stabilization).toBeVisible();

    // The five figures plus the year: all refused, none zero, none borrowed.
    for (const label of [
      'Stabilization Year',
      'Stabilized Occupancy',
      'Stabilized ADR',
      'Stabilized Revenue',
      'Stabilized NOI',
      'Stabilized NOI Margin',
    ]) {
      await expect(stabilization.getByText(label, { exact: true })).toBeVisible();
    }
    // Nothing in the section is a fabricated zero standing in for a refusal.
    await expect(stabilization.getByText(/^\$0$|^0\.0%$/)).toHaveCount(0);

    // The instruction — the part that was missing when this shipped broken.
    const banner = stabilization.getByTestId('stabilization-needs-rerun');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/re-run the model/i);
    // It must name where the Re-run lives, or it is not an instruction.
    // (Since the 2026-10-18 client-readiness sweep the single Re-run button is
    // the page-level stale-run banner, so the line points there — not at a tab.)
    await expect(banner).toContainText(/banner at the top of the page/i);
    // And it must not promise a number it does not have.
    await expect(banner).toContainText(/no other year stands in for it/i);
  });

  /**
   * FON-75 — the page-level stale-run banner must NOT fire here.
   *
   * The stub's engines payload carries no `stale_run`, so there is nothing
   * for the page-level banner to report. A banner that appeared anyway would
   * be a false positive — telling an analyst to re-run a model the worker
   * never flagged. That is the one way the feature can do harm, so it is
   * asserted on the screen where the section banner DOES fire: the two are
   * independent.
   *
   * The positive half — both banners together on a genuinely stale run —
   * lives in `04-stale-run-banner.spec.ts`.
   */
  test('the page-level stale-run banner stays silent with no engine payload', async ({ page }) => {
    await gotoDeal(page, OVERVIEW);
    await expect(page.getByTestId('overview-section-stabilization')).toBeVisible();
    await expect(page.getByTestId('stale-run-banner')).toHaveCount(0);
  });
});
