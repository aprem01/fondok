import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker } from '../fixtures/workerStub';

/**
 * Scenario Analysis — the chip row.
 *
 * Two things this locks, both reported from the outside:
 *
 *  • Exactly ONE "SOURCE OF TRUTH" badge on the tab. It marks the canonical
 *    underwriting, the one case edited in Financials / Investment / Debt /
 *    Partnership. Two of them would mean two canonical cases, which is the
 *    one thing the badge exists to rule out.
 *
 *  • The ••• actions menu opens on CLICK and stays open. It used to open on
 *    mouseenter and close on the wrapper's mouseleave, so the few pixels
 *    between the chip and the menu swallowed the pointer and "Edit overrides"
 *    was unreachable — the tester's complaint, and precisely the failure a
 *    jsdom test cannot feel because jsdom has no pointer.
 *
 * The chip row does not render in mock mode at all (no worker → no scenario
 * list → `FocusChip` returns null), which is why this lives under
 * `e2e/worker/`.
 */

test.describe('Scenario Analysis chip row', () => {
  test('renders exactly one SOURCE OF TRUTH badge', async ({ page }) => {
    await stubWorker(page);
    await gotoDeal(page, '?tab=scenarios');

    // ONE badge on the whole tab — two would mean two canonical cases.
    const badge = page.getByText('SOURCE OF TRUTH');
    await expect(badge).toHaveCount(1);

    // And it belongs to Base. The chip that carries the badge says "Base";
    // the saved scenario's chip carries its override count instead. Scoped to
    // the chip row because the names repeat in the compare checkboxes and the
    // table headers underneath.
    const chipRow = page.getByTestId('scenario-chip-row');
    const badgedChip = badge.locator('xpath=..');
    await expect(badgedChip).toContainText('Base');
    await expect(badgedChip).not.toContainText('Downside');
    await expect(chipRow).toContainText('1 override');
  });

  test('still exactly one badge when the deal has no saved scenarios', async ({ page }) => {
    await stubWorker(page, {
      scenarios: [
        {
          id: 'base', deal_id: '', tenant_id: 'e2e-tenant', name: 'Base',
          description: null, is_base: true, in_memo: true, overrides: [],
          last_run_id: null, created_at: '', updated_at: '',
        },
      ],
    });
    await gotoDeal(page, '?tab=scenarios');

    await expect(page.getByText('SOURCE OF TRUTH')).toHaveCount(1);
  });

  test('the ••• menu opens on click and survives moving the pointer to it', async ({ page }) => {
    await stubWorker(page);
    await gotoDeal(page, '?tab=scenarios');

    const strip = page.getByTestId('scenario-action-strip');
    const dots = page.getByRole('button', { name: /Scenario actions for Downside/i });

    await expect(dots).toBeVisible();
    await expect(strip).toBeHidden();

    // Hover alone must NOT open it — that was the broken interaction.
    await dots.hover();
    await expect(strip).toBeHidden();

    await dots.click();
    await expect(strip).toBeVisible();
    await expect(dots).toHaveAttribute('aria-expanded', 'true');

    // The dead zone: move the pointer off the chip, across the gap, onto the
    // strip. The old hover menu closed somewhere in here.
    await page.mouse.move(0, 0);
    await strip.hover();
    await expect(strip).toBeVisible();
    await expect(strip.getByRole('button', { name: /edit overrides/i })).toBeVisible();
  });

  test('Escape closes the ••• menu', async ({ page }) => {
    await stubWorker(page);
    await gotoDeal(page, '?tab=scenarios');

    await page.getByRole('button', { name: /Scenario actions for Downside/i }).click();
    await expect(page.getByTestId('scenario-action-strip')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.getByTestId('scenario-action-strip')).toBeHidden();
  });
});
