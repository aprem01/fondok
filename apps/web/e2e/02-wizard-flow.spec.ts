import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Wizard end-to-end — the Wave 1 financial gating story.
 *
 * Locked product decision: Step 3 → Step 4 is gated on at least one financial
 * statement being staged. The Next button is deliberately NOT natively
 * disabled — it takes the click and surfaces a WARN banner, because a button
 * that silently refuses leaves the analyst wondering what broke.
 *
 * Hooks, not prose. These specs wait on `wizard-documents-step` and the
 * `#wizard-financials-drop` input, never on step copy. The previous version
 * waited on the words "Add documents" — a heading this step stopped rendering
 * some waves ago — and failed on every push instead of being updated.
 */

const DEAL_NAME_PLACEHOLDER = 'Chicago Downtown Acquisition';

/** Step 1 → 2 → 3, leaving the wizard on Documents with nothing staged. */
async function openDocumentsStep(page: import('@playwright/test').Page, dealName: string) {
  await page.goto('/projects/new');
  // Coach-mark portals can intercept clicks on the active anchor.
  await page.evaluate(() => {
    localStorage.setItem('fondok:coachmarks:disabled', 'true');
  });
  await page.reload();

  await page.getByPlaceholder(DEAL_NAME_PLACEHOLDER).fill(dealName);
  await page.getByRole('button', { name: /^next/i }).click();
  // Step 2 (Return Profile) ships with a default selection — just advance.
  await page.getByRole('button', { name: /^next/i }).click();

  await expect(page.getByTestId('wizard-documents-step')).toBeVisible();
}

/** The stepper's own Next — the LAST one on the page (the other walks categories). */
const stepNextButton = (page: import('@playwright/test').Page) =>
  page.getByRole('button', { name: /^next$/i }).last();

/**
 * Open the Financial Statements category and return its file input.
 *
 * Only the ACTIVE category renders a panel, so `#wizard-financials-drop`
 * does not exist until Financial Statements is selected in the sidebar —
 * Step 3 opens on Offering Memorandum. The old specs went straight for the
 * input and timed out waiting for an element no one had asked for.
 */
async function openFinancialsDropzone(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /^Financial Statements/ }).first().click();
  const input = page.locator('#wizard-financials-drop');
  await expect(input).toBeAttached();
  return input;
}

test.describe('wizard end-to-end', () => {
  test('Next is gated until at least one financial is staged', async ({ page }) => {
    await openDocumentsStep(page, 'Gating Test Deal');

    const stepNext = stepNextButton(page);
    // `aria-disabled` — the click still fires so the gate can explain itself.
    await expect(stepNext).toBeDisabled();

    // Quiet until asked: the WARN banner appears only on an attempt.
    const gateWarning = page.getByRole('alert').filter({ hasText: /at least one financial/i });
    await expect(gateWarning).toBeHidden();

    await stepNext.click({ force: true });
    await expect(gateWarning).toBeVisible();
    // And we are still on Step 3 — the gate held.
    await expect(page.getByTestId('wizard-documents-step')).toBeVisible();
  });

  test('staging a financial enables Next', async ({ page }) => {
    const fixturePath = resolve(__dirname, 'fixtures', 'sample-t12.pdf');
    if (!existsSync(fixturePath)) {
      test.skip(true, 'Missing e2e/fixtures/sample-t12.pdf');
      return;
    }

    await openDocumentsStep(page, 'Upload Enables Next');

    // Each category panel renders its own hidden `<input id="wizard-{id}-drop">`.
    // FON-34 merged the old `t12` and `pnl` buckets into `financials`.
    const input = await openFinancialsDropzone(page);
    await input.setInputFiles(fixturePath);

    // The staged-file row confirms the list updated.
    await expect(page.getByText('sample-t12.pdf').first()).toBeVisible();
    await expect(stepNextButton(page)).toBeEnabled();
  });

  test('unsupported file type is rejected and does not satisfy the gate', async ({ page }) => {
    const badFixture = resolve(__dirname, 'fixtures', 'tiny-unsupported.zip');
    if (!existsSync(badFixture)) {
      test.skip(true, 'Missing e2e/fixtures/tiny-unsupported.zip');
      return;
    }

    await openDocumentsStep(page, 'Reject Bad Files');

    const input = await openFinancialsDropzone(page);
    await input.setInputFiles(badFixture);

    // The rejection toast fires — the extension allowlist ran.
    await expect(page.getByText(/unsupported file type/i).first()).toBeVisible();

    // The file is not staged: the "Selected … files" list never shows it.
    await expect(
      page.getByRole('list', { name: /selected .* files/i }).getByText('tiny-unsupported.zip'),
    ).toHaveCount(0);

    // And the gate still holds — a rejected file is not a financial.
    await expect(stepNextButton(page)).toBeDisabled();
  });
});
