import { test, expect } from '@playwright/test';

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
 * SCOPE, honestly: this project runs with `NEXT_PUBLIC_WORKER_URL=''`, so
 * `useEngineOutputs` never fetches and `stabilizedYearBlock()` is null for
 * every deal. That makes "renders dashes" trivially true here — it is
 * `__tests__/stabilizedParity.test.tsx` that proves the dash is chosen over a
 * borrowed year when real engine output IS present. What this spec adds is
 * that the banner reaches a real browser, on the real route, next to the real
 * rows — which is exactly the part that was missing in production.
 */

const OVERVIEW = '/projects/7?tab=overview';

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
    await page.addInitScript(() => {
      window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('Entry NOI is before-reserve and the exit figure is Cash NOI', async ({ page }) => {
    await page.goto(OVERVIEW);

    await expect(
      page.getByText('Run-Rate / Entry NOI (before FF&E reserve)'),
    ).toBeVisible();
    await expect(
      page.getByText('Forward 12-Month Cash NOI (after FF&E reserve)'),
    ).toBeVisible();
  });

  test('no row is labelled a bare, unqualified "NOI"', async ({ page }) => {
    await page.goto(OVERVIEW);
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
    await page.addInitScript(() => {
      window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('every Stabilization row is a dash and the banner says to re-run', async ({ page }) => {
    await page.goto(OVERVIEW);

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
    await expect(banner).toContainText(/investment/i);
    // And it must not promise a number it does not have.
    await expect(banner).toContainText(/will not stand another projection year/i);
  });
});
