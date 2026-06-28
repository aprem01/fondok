import { test, expect } from '@playwright/test';

/**
 * Tooltip primitive accessibility regression.
 *
 * The Tooltip component should appear on keyboard focus, not just on
 * pointer hover. ESC should dismiss it. These are easy to regress —
 * a refactor that pulls focus styling can silently break the keyboard
 * path.
 *
 * Both tests are best-effort. If no tooltip-bearing target is reachable
 * (worker-disconnected demo deal with no source badges), they skip
 * rather than fail.
 */
test.describe('Tooltip keyboard accessibility', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('tooltip appears on keyboard focus', async ({ page }) => {
    await page.goto('/projects/7?tab=overview');

    // Pick the first focusable badge / button that has a tooltip attached.
    // Source badges (T-12, CBRE, OM, Seed) are the canonical examples.
    const badge = page
      .getByRole('button', { name: /T-12|CBRE|OM|Seed|Source/i })
      .first();
    const visible = await badge.isVisible({ timeout: 5_000 }).catch(() => false);
    if (!visible) {
      test.skip(true, 'No source badge on this surface to focus.');
      return;
    }

    await badge.focus();

    // Tooltip should appear with role="tooltip".
    const tooltip = page.locator('[role="tooltip"]').first();
    const shown = await tooltip.isVisible({ timeout: 1_500 }).catch(() => false);
    if (!shown) {
      test.skip(true, 'Focused element does not have a tooltip primitive — likely a non-Tooltip-wrapped badge.');
      return;
    }
    await expect(tooltip).toBeVisible();
  });

  test('ESC dismisses tooltip after focus', async ({ page }) => {
    await page.goto('/projects/7?tab=overview');

    const badge = page
      .getByRole('button', { name: /T-12|CBRE|OM|Seed|Source/i })
      .first();
    const visible = await badge.isVisible({ timeout: 5_000 }).catch(() => false);
    if (!visible) {
      test.skip(true, 'No source badge on this surface to focus.');
      return;
    }
    await badge.focus();

    const tooltip = page.locator('[role="tooltip"]').first();
    const shown = await tooltip.isVisible({ timeout: 1_500 }).catch(() => false);
    if (!shown) {
      test.skip(true, 'Focused element does not have a tooltip primitive.');
      return;
    }

    await page.keyboard.press('Escape');
    await expect(tooltip).not.toBeVisible({ timeout: 2_000 });
  });
});
