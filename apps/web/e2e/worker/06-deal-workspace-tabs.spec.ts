import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker } from '../fixtures/workerStub';

/**
 * Deal workspace tab navigation — every tab renders without throwing into
 * the ErrorBoundary. If a tab throws, that's a regression worth catching
 * before an external tester clicks it.
 *
 * Moved from the `mock` project on 2026-10-30 (go-live Wave 0, C4): it used
 * to open the fictitious "Kimpton Angler" sample deal (id=7) that rendered
 * when no worker URL was configured. That fixture is gone — a build without a
 * worker now says "Worker not configured" — so the tabs are exercised here
 * against the stubbed worker deal instead. Anything the stub does not serve
 * answers 404, exactly as a worker that never ran that engine would, so each
 * tab renders its own empty / refusal state rather than a number.
 *
 * Tabs (from src/app/projects/[id]/page.tsx tabs array):
 *   '' (Data Room), validation, overview, investment, pl,
 *   debt, cash-flow, returns (→ redirects into overview), partnership, market
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
  for (const tab of TABS) {
    test(`tab=${tab || 'data-room'} renders without ErrorBoundary on the stubbed deal`, async ({ page }) => {
      await stubWorker(page);
      await gotoDeal(page, tab ? `?tab=${tab}` : '');

      // ErrorBoundary fallback copy is "Something went wrong". If any
      // tab throws, this assertion fails — exactly the regression we
      // want to catch.
      await expect(page.getByText(/something went wrong/i)).not.toBeVisible();

      // Sanity check that something rendered at all — every tab should
      // surface at least the deal header (which carries the deal name).
      await expect(page.getByText(/the angler's/i).first()).toBeVisible({
        timeout: 8_000,
      });
    });
  }
});
