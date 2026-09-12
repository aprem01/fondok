import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker } from '../fixtures/workerStub';

/**
 * Financials — sub-tab deep links, and the Period control as a real filter.
 *
 * Neither of these renders at all in the mock-mode project: `PLTab` shows its
 * "No P&L output yet" placeholder until `expense.outputs.years` is non-empty,
 * so the sub-tab row and the Historicals worksheet never mount. That is why
 * these specs live under `e2e/worker/` against the stubbed-worker server.
 *
 * `__tests__/subTabRouting.test.tsx` covers `useSubTab`'s parsing and
 * `__tests__/historicalsPeriodFilter.test.tsx` covers the filter in jsdom.
 * What only a browser can show is that a pasted URL — the real Next router,
 * the real RSC round-trip, the real component tree — actually lands where it
 * says. That is the half that broke for Sam.
 */

test.describe('Financials sub-tab deep links', () => {
  test.beforeEach(async ({ page }) => {
    await stubWorker(page);
  });

  test('?tab=pl&sub=projections opens Projections, not Historicals', async ({ page }) => {
    await gotoDeal(page, '?tab=pl&sub=projections');

    const projections = page.getByRole('tab', { name: 'Projections' });
    const historicals = page.getByRole('tab', { name: 'Historicals' });
    await expect(projections).toHaveAttribute('aria-selected', 'true');
    await expect(historicals).toHaveAttribute('aria-selected', 'false');
  });

  test('?tab=pl with no sub falls back to Historicals', async ({ page }) => {
    await gotoDeal(page, '?tab=pl');

    await expect(page.getByRole('tab', { name: 'Historicals' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('clicking a sub-tab writes it back into the URL', async ({ page }) => {
    await gotoDeal(page, '?tab=pl');

    await page.getByRole('tab', { name: 'Projections' }).click();
    await expect(page).toHaveURL(/[?&]sub=projections\b/);
    await expect(page).toHaveURL(/[?&]tab=pl\b/);
  });
});

test.describe('Historicals — the Period control filters by basis', () => {
  test.beforeEach(async ({ page }) => {
    await stubWorker(page);
    await gotoDeal(page, '?tab=pl&sub=historicals');
  });

  /** Column headers of the worksheet grid, trimmed. */
  const headers = (page: import('@playwright/test').Page) =>
    page.getByRole('columnheader');

  test('Full Year hides the trailing-twelve column and vice versa', async ({ page }) => {
    const period = page.getByLabel('Period basis');
    await expect(period).toBeVisible();
    // Default is "don't filter" — nothing is hidden from under the analyst.
    await expect(period).toHaveValue('ALL');
    await expect(headers(page).filter({ hasText: /^T12\b/ })).toHaveCount(1);
    await expect(headers(page).filter({ hasText: /^FY/ }).first()).toBeVisible();

    await period.selectOption('FY');
    await expect(headers(page).filter({ hasText: /^T12\b/ })).toHaveCount(0);
    await expect(headers(page).filter({ hasText: /^FY/ }).first()).toBeVisible();

    await period.selectOption('T12');
    await expect(headers(page).filter({ hasText: /^FY/ })).toHaveCount(0);
    await expect(headers(page).filter({ hasText: /^T12\b/ })).toHaveCount(1);
  });

  test('a basis with no columns is disabled rather than blanking the grid', async ({ page }) => {
    const period = page.getByLabel('Period basis');
    await expect(period).toBeVisible();

    // This deal's statements are full-year P&Ls plus one T-12 — no YTD. The
    // option stays visible, carries its count, and cannot be chosen: an empty
    // grid would read as "the data is gone".
    const ytd = period.locator('option', { hasText: /^Year-to-date/ });
    await expect(ytd).toHaveAttribute('disabled', '');
    await expect(ytd).toContainText('· 0');

    await expect(period.locator('option', { hasText: /^Full Year/ })).not.toHaveAttribute(
      'disabled',
      '',
    );
    // Every option states its column count, so nothing is silently filtered.
    await expect(period.locator('option', { hasText: /^Full Year/ })).toContainText('·');
    // The grid still stands.
    await expect(headers(page).first()).toBeVisible();
  });
});
