import { test, expect } from '@playwright/test';
import { gotoDeal, stubWorker, STUB_DEAL_ID } from '../fixtures/workerStub';

/**
 * Data Room — GapChipsStrip empty-state accuracy.
 *
 * Sam's P0: with no `year_coverage` entries on a live deal the strip
 * cheerfully announced "your 5-year history is complete". No financials
 * uploaded is not complete coverage, and a green tick over an empty data room
 * is the worst possible lie for this product to tell. The fix branches on
 * `Object.keys(year_coverage).length === 0`.
 *
 * This spec used to live at `04-validation-banner-regression.spec.ts` and
 * drove `?tab=validation`. The strip moved to the Data Room — it belongs next
 * to the uploads it asks for — so the old spec pointed at a tab that no longer
 * mounts it, could never see the strip, and skipped itself on every run with
 * "GapChipsStrip did not mount". A spec that skips itself forever is not
 * coverage. It is here, on the right tab, against the worker-stub server
 * (`isLiveDealId()` needs a connected worker and a non-numeric id), and it
 * fails if the strip goes missing.
 */

const COVERAGE = `**/deals/${STUB_DEAL_ID}/document_coverage*`;

const coverage = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

test.describe('GapChipsStrip empty-state accuracy', () => {
  test('"No financials uploaded yet" when year_coverage is empty', async ({ page }) => {
    await stubWorker(page);
    // Route ordering: Playwright runs the most recently added handler first,
    // so this wins over the stub's catch-all.
    await page.route(COVERAGE, (route) =>
      route.fulfill(
        coverage({
          deal_id: STUB_DEAL_ID,
          year_coverage: {},
          gaps: [],
          lookback_years: 5,
        }),
      ),
    );

    await gotoDeal(page, '');

    await expect(page.getByText(/no financials uploaded yet/i)).toBeVisible();
    await expect(page.getByText(/history is complete/i)).toHaveCount(0);
  });

  test('"history is complete" only with coverage entries AND no gaps', async ({ page }) => {
    await stubWorker(page);
    await page.route(COVERAGE, (route) =>
      route.fulfill(
        coverage({
          deal_id: STUB_DEAL_ID,
          year_coverage: { '2024': [{ doc_id: 'x', doc_type: 'T12' }] },
          gaps: [],
          lookback_years: 5,
        }),
      ),
    );

    await gotoDeal(page, '');

    await expect(page.getByText(/history is complete/i)).toBeVisible();
    await expect(page.getByText(/no financials uploaded yet/i)).toHaveCount(0);
  });
});
