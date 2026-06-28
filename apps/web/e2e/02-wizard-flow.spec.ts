import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Wizard end-to-end — verifies the Wave 1 financial gating story.
 *
 * Locked product decision: Step 3 → Step 4 advance is gated on at least
 * one financial document (T-12 OR historical P&L) being staged. This
 * suite proves the Next button stays disabled before any upload and
 * enables after.
 */
test.describe('wizard end-to-end', () => {
  test.beforeEach(async ({ page }) => {
    // Disable coach marks for predictable layout — their pulsing rings
    // can swallow clicks on the active anchor.
    await page.goto('/');
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
  });

  test('Next is gated until at least one financial is uploaded', async ({ page }) => {
    await page.goto('/projects/new');

    // Step 1 → fill required + advance.
    await page.getByPlaceholder('Chicago Downtown Acquisition').fill('Gating Test Deal');
    await page.getByPlaceholder('Chicago, IL').fill('New York, NY');
    await page.getByRole('button', { name: /^next/i }).click();

    // Step 2 → already has a default selection; advance.
    await page.getByRole('button', { name: /^next/i }).click();

    // Step 3 — Documents. The Next button should now be disabled.
    // There are two "Next" buttons (one for category nav, one for
    // step nav) so target the one at the page bottom by its location
    // — the stepper footer button is the LAST Next on the page.
    const stepNext = page.getByRole('button', { name: /^next$/i }).last();
    await expect(stepNext).toBeDisabled();

    // The warn banner copy should be visible.
    await expect(
      page.getByText(/add at least one financial/i),
    ).toBeVisible();
  });

  test('uploading a financial enables Next', async ({ page }) => {
    const fixturePath = resolve(__dirname, 'fixtures', 'sample-t12.pdf');
    if (!existsSync(fixturePath)) {
      test.skip(true, 'Missing e2e/fixtures/sample-t12.pdf');
      return;
    }

    await page.goto('/projects/new');
    await page.getByPlaceholder('Chicago Downtown Acquisition').fill('Upload Enables Next');
    await page.getByRole('button', { name: /^next/i }).click();
    await page.getByRole('button', { name: /^next/i }).click();

    // We should land on Step 3 with T-12 active by default.
    await expect(page.getByText(/add documents/i)).toBeVisible();

    // Locate the file input scoped to the T-12 category drop zone.
    // The category panels each render their own hidden <input id="wizard-{id}-drop">.
    const fileInput = page.locator('#wizard-t12-drop');
    await fileInput.setInputFiles(fixturePath);

    // Wait for the file row to appear — confirms the staged list updated.
    await expect(page.getByText('sample-t12.pdf').first()).toBeVisible();

    // The bottom Next (step nav) should now be enabled.
    const stepNext = page.getByRole('button', { name: /^next$/i }).last();
    await expect(stepNext).toBeEnabled();
  });

  test('unsupported file type is rejected', async ({ page }) => {
    const badFixture = resolve(__dirname, 'fixtures', 'tiny-unsupported.zip');
    if (!existsSync(badFixture)) {
      test.skip(true, 'Missing e2e/fixtures/tiny-unsupported.zip');
      return;
    }

    await page.goto('/projects/new');
    await page.getByPlaceholder('Chicago Downtown Acquisition').fill('Reject Bad Files');
    await page.getByRole('button', { name: /^next/i }).click();
    await page.getByRole('button', { name: /^next/i }).click();

    const fileInput = page.locator('#wizard-t12-drop');
    await fileInput.setInputFiles(badFixture);

    // The rejection toast should fire — confirms the filter ran.
    await expect(
      page.getByText(/unsupported file type/i).first(),
    ).toBeVisible({ timeout: 3000 });

    // The wizard should NOT stage the file. The staged-files list is
    // a <ul aria-label="Selected ..."> under the active category panel.
    // Assert no list item under that list contains the bad filename.
    const stagedList = page.getByRole('list', { name: /selected .* files/i });
    await expect(
      stagedList.getByText('tiny-unsupported.zip'),
    ).toHaveCount(0);

    // Next stays disabled — financials still missing.
    const stepNext = page.getByRole('button', { name: /^next$/i }).last();
    await expect(stepNext).toBeDisabled();
  });
});
