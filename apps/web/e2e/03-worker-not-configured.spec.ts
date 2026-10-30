import { test, expect } from '@playwright/test';

/**
 * Go-live Wave 0 (C4 / C6) — a build with no worker URL shows a dash, never a
 * fixture.
 *
 * This project runs with `NEXT_PUBLIC_WORKER_URL=''`. Until 2026-10-30 that
 * build rendered four fictitious hotels on /projects and a fixed "Kimpton
 * Angler" sample at /projects/7, indistinguishable from real deals. Both
 * surfaces now render the ONE "Worker not configured" state. This spec is the
 * browser-level lock on that: the real route, the real shell, no sample.
 */
const FORMER_SAMPLE_DEALS = [
  'Hilton Garden Inn Downtown',
  'Kimpton Angler',
  'Marriott Magnificent Mile',
  'Hyatt Regency Waterfront',
];

test.describe('no worker URL → "Worker not configured", never a sample deal', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('the Projects list says so and lists nothing', async ({ page }) => {
    await page.goto('/projects');
    await expect(page.getByRole('heading', { name: 'Projects', exact: true })).toBeVisible();
    const state = page.getByTestId('worker-not-configured');
    await expect(state).toBeVisible();
    await expect(state).toContainText('Worker not configured');
    for (const name of FORMER_SAMPLE_DEALS) {
      await expect(page.getByText(name)).toHaveCount(0);
    }
    await expect(page.getByText(/\d+ keys/)).toHaveCount(0);
  });

  test('a legacy sample-deal link renders the same state, not the sample', async ({ page }) => {
    await page.goto('/projects/7?tab=overview');
    const state = page.getByTestId('worker-not-configured');
    await expect(state).toBeVisible();
    for (const name of FORMER_SAMPLE_DEALS) {
      await expect(page.getByText(name)).toHaveCount(0);
    }
    await expect(page.getByText(/something went wrong/i)).toHaveCount(0);
    // Nothing numeric pretends to be the deal's own figure.
    await expect(page.getByText(/^\$[\d,.]+[MK]?$|^\d+(\.\d+)?%$/)).toHaveCount(0);
  });
});
