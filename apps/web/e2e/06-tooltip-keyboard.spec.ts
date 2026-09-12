import { test, expect } from '@playwright/test';

/**
 * The Tooltip primitive's keyboard path.
 *
 * `src/components/help/Tooltip.tsx` shows on focus as well as hover and hides
 * on Escape. Both are easy to lose in a refactor that only ever gets checked
 * with a mouse, and losing them makes every definition in the app
 * keyboard-unreachable.
 *
 * Driven through `/methodology`, which renders `AssumptionBadge` — a
 * `tabIndex={0}` span wrapped in the real primitive. The previous version of
 * this spec hunted for a source badge on the Kimpton deal's Overview, which
 * needs live assumption provenance to render, found nothing, and skipped
 * itself on every single run. Two permanently-skipped tests read as coverage
 * and were not.
 */
test.describe('Tooltip keyboard accessibility', () => {
  /** Any AssumptionBadge on the methodology page — they all wrap Tooltip. */
  const badge = (page: import('@playwright/test').Page) =>
    page.locator('[tabindex="0"]').filter({ hasText: /Analyst|Seed/ }).first();

  test('tooltip appears on keyboard focus', async ({ page }) => {
    await page.goto('/methodology');

    await badge(page).focus();
    await expect(page.locator('[role="tooltip"]').first()).toBeVisible();
  });

  test('ESC dismisses the tooltip', async ({ page }) => {
    await page.goto('/methodology');

    await badge(page).focus();
    const tooltip = page.locator('[role="tooltip"]').first();
    await expect(tooltip).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(tooltip).toBeHidden();
  });
});
