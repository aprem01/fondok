import { test, expect } from '@playwright/test';

/**
 * 5-minute smoke. Highest priority — if any of these fail, the deploy is
 * hosed and downstream specs aren't worth running. Stays narrow on purpose:
 * app loads, wizard renders, coach marks behave.
 */
test.describe('smoke', () => {
  test('app loads and the Projects route renders', async ({ page }) => {
    await page.goto('/');
    // The "Projects" link lives in the sidebar (AppShell), which renders on
    // every route. Exact match so we don't catch "Back to Projects".
    const projectsLink = page
      .getByRole('link', { name: 'Projects', exact: true })
      .first();
    await expect(projectsLink).toBeVisible();

    await projectsLink.click();

    // Assert the destination RENDERED, not just that the address bar moved.
    //
    // The old spec asserted `toHaveURL(/\/projects/)` and nothing else, which
    // was both too weak (a URL can change onto a blank error page) and the
    // flakiest line in the suite: App Router only commits the URL once the
    // RSC payload for the route lands, so on a cold server this raced the
    // 7.5s expect timeout. The route warm-up in `e2e/global-setup.ts` removes
    // the race; waiting on the page's own heading removes the ambiguity.
    await expect(
      page.getByRole('heading', { name: 'Projects', exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/projects\/?(\?|$)/);
    await expect(page.getByText(/something went wrong/i)).toBeHidden();
  });

  test('wizard renders every document category on Step 3', async ({ page }) => {
    await page.goto('/projects/new');
    // Pre-emptively disable coach marks so their pulsing rings + portals
    // don't intercept clicks. Reload so the override is picked up before
    // any CoachMark mounts.
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
    await page.reload();

    // Step 1 visible — "Create New Deal" is the section heading.
    await expect(page.getByText(/create new deal/i).first()).toBeVisible();

    // The wizard's Field component uses unassociated labels — target
    // inputs by placeholder copy instead. Deal Name uses
    // "Chicago Downtown Acquisition" as placeholder.
    await page.getByPlaceholder('Chicago Downtown Acquisition').fill('Smoke Test Deal');
    await page.getByRole('button', { name: /^next/i }).click();

    // Step 2 — Return Requirements visible. Advance.
    await expect(page.getByText(/return requirements/i)).toBeVisible();
    await page.getByRole('button', { name: /^next/i }).click();

    await expect(page.getByTestId('wizard-documents-step')).toBeVisible();

    // Every category renders as a sidebar button. The catalog lives in
    // components/project/wizard/DocumentsStep.tsx (WIZARD_CATEGORIES).
    //
    // Deliberately NOT pinned to a count. FON-34 merged the old "T-12 /
    // Trailing Twelve Months" and "Annual / YTD / Monthly P&L" buckets into
    // one "Financial Statements" category, and this assertion — written as
    // "all 11 categories" — went red and stayed red rather than being
    // updated. A count pin turns every legitimate catalog change into a
    // failure, which is how a suite gets ignored. Assert the categories that
    // must exist; adding one is not a regression.
    const expectedCategories = [
      /Offering Memorandum/,
      /Financial Statements/,
      /STR \/ Comp Set Report/,
      /Insurance Records/,
      /Property Taxes/,
      /Room Mix \/ Unit Mix/,
      /Historical CapEx/,
      /Basic Property Info/,
      /Leases & Agreements/,
      /Surveys & Reviews/,
    ];
    for (const re of expectedCategories) {
      await expect(
        page.getByRole('button', { name: re }).first(),
      ).toBeVisible();
    }
  });

  test('the first coach mark shows and is dismissable via Got it', async ({ page }) => {
    // Step 1 of the wizard mounts the "Why we ask for sourcing channel" hint
    // at order 0, so a first-time visitor always gets exactly this one.
    // Asserted unconditionally: the previous version skipped when it didn't
    // find the button within 3s, which meant it passed silently whether the
    // coach-mark system worked or had been broken for a month.
    await page.goto('/projects/new');

    const gotIt = page.getByRole('button', { name: /got it/i }).first();
    await expect(gotIt).toBeVisible();

    await gotIt.click();
    await expect(gotIt).toBeHidden();
  });
});
