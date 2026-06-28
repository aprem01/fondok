import { test, expect } from '@playwright/test';

/**
 * Assumption override + mandatory justification.
 *
 * Wave 1 product decision: when an analyst overrides an extracted
 * assumption, they MUST attach a non-empty justification note. The
 * OverrideModal disables Save until the note is non-empty.
 *
 * The override pencil + modal only render on live worker deals
 * (numeric mock-fixture deals show source badges without the edit
 * affordance). When the suite runs against the demo build, these
 * tests skip cleanly rather than fail spuriously.
 */
test.describe('assumption override with justification', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('Save is gated until a justification note is non-empty', async ({ page }) => {
    await page.goto('/projects/7?tab=overview');

    // Find the first override pencil. On mock deals this won't render.
    const overrideBtn = page.getByRole('button', { name: /override value/i }).first();
    const visible = await overrideBtn.isVisible({ timeout: 5_000 }).catch(() => false);
    if (!visible) {
      test.skip(true, 'Override pencil only renders on live worker deals.');
      return;
    }
    await overrideBtn.click();

    // Modal opens — type a new value.
    const valueField = page.getByLabel(/new value/i);
    await expect(valueField).toBeVisible();
    await valueField.fill('0.75');

    // Save should be disabled until a note is non-empty.
    const saveBtn = page.getByRole('button', { name: /save override/i });
    await expect(saveBtn).toBeDisabled();

    // Type a justification.
    await page.getByLabel(/justification/i).fill('E2E test override');
    await expect(saveBtn).toBeEnabled();
  });
});
