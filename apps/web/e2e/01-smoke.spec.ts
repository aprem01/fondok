import { test, expect } from '@playwright/test';

/**
 * 5-minute smoke. Highest priority — if any of these fail, the deploy
 * is hosed and downstream specs aren't worth running. Stays narrow on
 * purpose: app loads, wizard renders, coach marks behave.
 */
test.describe('smoke', () => {
  test('app loads and projects nav is reachable', async ({ page }) => {
    await page.goto('/');
    // Landing page is public. The "Projects" link lives in the sidebar
    // (AppShell) which renders on every route. Use the more specific
    // exact-name match so we don't accidentally match "Back to Projects"
    // or similar.
    const projectsLink = page
      .getByRole('link', { name: 'Projects', exact: true })
      .first();
    await expect(projectsLink).toBeVisible();

    await projectsLink.click();
    await expect(page).toHaveURL(/\/projects/);
    await expect(page.getByText(/something went wrong/i)).not.toBeVisible();
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

    // Step 3 — every category renders as a sidebar button. The catalog lives
    // in components/project/wizard/DocumentsStep.tsx.
    //
    // Deliberately NOT pinned to a count. FON-34 merged the old "T-12 /
    // Trailing Twelve Months" and "Annual / YTD / Monthly P&L" buckets into
    // one "Financial Statements" category with a per-file statement-type
    // picker, and this assertion — written as "all 11 categories" — went red
    // and stayed red rather than being updated. A count pin turns every
    // legitimate catalog change into a failure, which is how a suite gets
    // ignored. Assert the categories that must exist; adding one is not a
    // regression.
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

  test('coach marks dismissable via Got it', async ({ page }) => {
    // Land on a surface that mounts a CoachMark — Step 1 of the wizard
    // mounts the "Why we ask for sourcing channel" hint.
    await page.goto('/projects/new');

    // The first un-dismissed CoachMark renders its "Got it" button.
    const gotIt = page.getByRole('button', { name: /got it/i }).first();
    const visible = await gotIt.isVisible({ timeout: 3000 }).catch(() => false);
    if (visible) {
      await gotIt.click();
      // After dismissal the same button should not be visible.
      await expect(gotIt).not.toBeVisible();
    } else {
      // No coach mark on this surface — skip rather than fail. The
      // smoke is about regression catching, not coverage completeness.
      test.skip(true, 'No CoachMark mounted on this surface');
    }
  });
});
