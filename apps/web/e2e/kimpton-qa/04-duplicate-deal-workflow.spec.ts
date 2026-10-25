import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import { test, expect } from './support';
import { kimptonSmokeSkipReason } from './guard';

/**
 * D22 / FON-110 ("QA tooling for Sam's Phase 1: Duplicate deal, Compare runs,
 * Kimpton golden baseline" — live status "Ready for QA by Team" as of
 * Oct 19). Sam's whole regression workflow depends on this: "Work on a
 * duplicate Kimpton deal — re-extraction resets reviewed fields."
 *
 * IMPORTANT CAVEAT: this spec was written by reading the LOCAL repo checkout
 * on Sam's machine (last commit touching the Projects list: 2026-10-06), not
 * the deployed app. As of that checkout, the per-project kebab menu
 * (`apps/web/src/app/projects/page.tsx` → `buildProjectMenu`) has only
 * "View Details", "Archive" and "Delete Forever" — no Duplicate/Clone
 * action. If Prem has shipped FON-110 since Oct 6 and the deployed app is
 * ahead of this checkout, this test should pass; if it's red, that is a real
 * gap between "Ready for QA by Team" and what's actually reachable, worth
 * raising with Prem directly rather than assuming the test is wrong.
 */

const EMAIL = process.env.FONDOK_SMOKE_EMAIL ?? '';
const PASSWORD = process.env.FONDOK_SMOKE_PASSWORD ?? '';
const SKIP_REASON = kimptonSmokeSkipReason() ?? 'Opt-in only — see e2e/kimpton-qa/guard.ts.';

test.describe('@kimpton-qa Duplicate-deal workflow (D22 / FON-110)', () => {
  test.skip(() => kimptonSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({ page, signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD } });
  });

  test('Projects list exposes a Duplicate/Clone action for a deal (TC: D22 / FON-110)', async ({ page }) => {
    await page.goto('/projects', { waitUntil: 'domcontentloaded' });
    // Open the first kebab/overflow menu on the page — whichever deal it
    // belongs to, we're checking the ACTION EXISTS AT ALL, not this specific
    // deal's menu.
    const kebab = page.getByRole('button', { name: /more|options|…|•••/i }).first();
    await expect(kebab, 'a per-project overflow/kebab menu is visible on the Projects list').toBeVisible({ timeout: 30_000 });
    await kebab.click();
    await expect(
      page.getByText(/^duplicate|^clone/i).first(),
      'a Duplicate/Clone action exists in the project menu — required for Sam’s "work on a duplicate deal, never the original" workflow',
    ).toBeVisible({ timeout: 10_000 });
  });
});
