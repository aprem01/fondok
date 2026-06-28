import { test, expect } from '@playwright/test';

/**
 * Deal workspace tab navigation. The Kimpton Angler demo deal (id=7)
 * is backed by mock data, which means every tab should render cleanly
 * without a backend. If a tab throws into the ErrorBoundary, that's a
 * regression worth catching before Sam clicks it during a demo.
 *
 * Tabs (from src/app/projects/[id]/page.tsx tabs array):
 *   '' (Data Room), validation, overview, investment, pl,
 *   debt, cash-flow, returns, partnership, market
 */
const TABS = [
  '',
  'validation',
  'overview',
  'investment',
  'pl',
  'debt',
  'cash-flow',
  'returns',
  'partnership',
  'market',
];

test.describe('deal workspace tab navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  for (const tab of TABS) {
    test(`tab=${tab || 'data-room'} renders without ErrorBoundary on Kimpton (id=7)`, async ({ page }) => {
      const url = tab ? `/projects/7?tab=${tab}` : '/projects/7';
      await page.goto(url);

      // ErrorBoundary fallback copy is "Something went wrong". If any
      // tab throws, this assertion fails — exactly the regression we
      // want to catch before Sam sees it.
      await expect(page.getByText(/something went wrong/i)).not.toBeVisible();

      // Sanity check that something rendered at all — every tab should
      // surface at least the deal header (which carries the deal name).
      await expect(page.getByText(/kimpton angler/i).first()).toBeVisible({
        timeout: 8_000,
      });
    });
  }
});
