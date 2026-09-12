import { test, expect } from '@playwright/test';
import type { Page, Request, Route } from '@playwright/test';
import { gotoDeal, stubWorker, STUB_DEAL_ID } from '../fixtures/workerStub';

/**
 * Assumption override + mandatory justification — FON-74.
 *
 * This spec existed once before and was deleted on 2026-09-12 because its
 * premise was false: it drove the `OverridePanel` drawer, and nothing in the
 * app ever mounted that drawer. It asserted a gate no analyst could reach, and
 * it `test.skip`ped rather than failed — the worst of both.
 *
 * The premise is now true. The gate lives on the LIVE path, in the one inline
 * editing primitive every tab runs on, so this file drives what an analyst
 * actually touches: the Debt tab's senior loan amount, which writes
 * `field_overrides["debt_stack.tranches.0.principal_usd"]` — an engine input,
 * and therefore one that must carry a reason.
 *
 *   1. Save with no justification fires NO PATCH and the editor stays open, so
 *      the analyst adds the reason rather than losing the edit.
 *   2. Save with one PATCHes `{value, note}` — the analyst's own words, never a
 *      generated string.
 *   3. A no-op edit never asks why. Opening a field to inspect it and saving it
 *      back unchanged must exit quietly; FON-63's guard runs first, and this is
 *      the test that keeps it first.
 *
 * It lives under `e2e/worker/` (not at the suite root) because the override
 * affordance only exists on a worker-connected deal — in mock mode editing is
 * disabled and the old spec's skip is exactly the outcome to avoid.
 */

type Patch = { field_overrides?: Record<string, unknown> };

/** The PATCH bodies the page sent, newest last. */
async function capturePatches(page: Page): Promise<Patch[]> {
  const patches: Patch[] = [];
  // Registered AFTER `stubWorker` so it wins for PATCH; everything else falls
  // through to the stub (and its 404-by-default rule).
  await page.route(`**/__e2e-worker/deals/${STUB_DEAL_ID}`, (route: Route, request: Request) => {
    if (request.method() !== 'PATCH') return route.fallback();
    patches.push(JSON.parse(request.postData() || '{}') as Patch);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: STUB_DEAL_ID }),
    });
  });
  return patches;
}

const PRINCIPAL_KEY = 'debt_stack.tranches.0.principal_usd';

test.describe('an override that changes a number carries a justification', () => {
  test('Save is refused until the justification is non-empty, then stores it', async ({ page }) => {
    await stubWorker(page);
    const patches = await capturePatches(page);
    await gotoDeal(page, '?tab=debt');

    // The senior loan amount — an analyst input the whole debt schedule prices
    // off. Clicking it opens the canonical inline editor.
    await page.getByTestId('edit-senior-amount').click();
    const value = page.locator('input[type="number"]').first();
    await expect(value).toBeVisible();
    await value.fill('24000000');

    // 1 — Save with a blank reason writes nothing, and keeps the edit.
    await page.getByRole('button', { name: 'Save' }).click();
    const note = page.getByTestId('edit-senior-amount-note');
    await expect(note).toBeVisible();
    expect(patches).toHaveLength(0);

    // 2 — with the reason, the value and the words land together.
    await note.fill('Lender resized the senior to 60% LTV');
    await page.getByRole('button', { name: 'Save' }).click();

    await expect.poll(() => patches.length).toBeGreaterThan(0);
    expect(patches[0].field_overrides?.[PRINCIPAL_KEY]).toEqual({
      value: 24_000_000,
      note: 'Lender resized the senior to 60% LTV',
    });
  });

  test('a no-op edit never asks why', async ({ page }) => {
    await stubWorker(page);
    const patches = await capturePatches(page);
    await gotoDeal(page, '?tab=debt');

    // Open the field to inspect it and save it back untouched. That is not an
    // override, so it must not demand a justification — it must exit quietly.
    await page.getByTestId('edit-senior-amount').click();
    await expect(page.locator('input[type="number"]').first()).toBeVisible();
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('edit-senior-amount-note')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0);
    expect(patches).toHaveLength(0);
  });

  test('Cancel discards the reason with the change it explained', async ({ page }) => {
    await stubWorker(page);
    const patches = await capturePatches(page);
    await gotoDeal(page, '?tab=debt');

    await page.getByTestId('edit-senior-amount').click();
    await page.locator('input[type="number"]').first().fill('24000000');
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByTestId('edit-senior-amount-note').fill('abandoned reason');
    await page.getByRole('button', { name: 'Cancel' }).click();

    expect(patches).toHaveLength(0);

    // Re-opening starts clean — a reason never outlives its change.
    await page.getByTestId('edit-senior-amount').click();
    await page.locator('input[type="number"]').first().fill('24000000');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('edit-senior-amount-note')).toHaveValue('');
  });
});
