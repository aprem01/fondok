import { test, expect, type Locator, type Page } from '@playwright/test';
import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import { PROD_HOSTNAME, prodSmokeSkipReason, smokeDealId } from './guard';

/**
 * Post-deploy production smoke — FON-76.
 *
 * ── Why this file exists ────────────────────────────────────────────────
 * On 2026-09-12 a live deal rendered every Stabilization row on Overview as
 * a dash while 1,537 worker tests and 606 web tests were green. Nothing was
 * wrong with the frontend and nothing was wrong with the engine: the deal's
 * PERSISTED `engine_outputs` row had been written before the expense engine
 * started publishing a `stabilization` block. The whole Playwright suite
 * next door runs the frontend against mocks and stubs, so it structurally
 * could not see it — and no check anywhere read production data through the
 * deployed app.
 *
 * This closes that gap, and only that gap. It opens ONE known, pre-existing
 * deal in the deployed app, signed in as a real user, and asserts the
 * figures are there.
 *
 * ── The one rule ────────────────────────────────────────────────────────
 * IT ASSERTS PRESENCE AND STRUCTURE, NEVER A VALUE. Not an IRR, not a total,
 * not a sentence of product copy. Pinning a value is what produced the
 * 200-run red streak this suite just recovered from (see e2e/README.md
 * § Conventions), and a smoke that goes red because an analyst legitimately
 * edited an assumption is a smoke that gets switched off within a week. The
 * question it answers is "does the deployed app render this deal's numbers
 * at all", which is exactly the question that was unanswered on 2026-09-12.
 *
 * ── It writes NOTHING ───────────────────────────────────────────────────
 * No deal creation, no upload, no override, no re-run, no PATCH of any kind.
 * Every navigation is a GET. The only state it touches is one localStorage
 * key in its own throwaway browser profile, to stop coachmarks covering the
 * rows it reads. A smoke that mutates production is a smoke nobody will run.
 *
 * ── Running it ──────────────────────────────────────────────────────────
 *   CLERK_PUBLISHABLE_KEY=pk_test_… CLERK_SECRET_KEY=sk_test_… \
 *   FONDOK_SMOKE_EMAIL=… FONDOK_SMOKE_PASSWORD=… FONDOK_SMOKE_DEAL_ID=… \
 *   FONDOK_E2E_PROD=1 PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app \
 *     pnpm test:e2e:prod
 *
 * Both opt-ins are required (see ./guard.ts) and the `prod` project does not
 * exist in the config without them, so this cannot fire on a pull request.
 */

/** The refusal glyph — `src/lib/ontology/reasons.generated.ts:REFUSAL_GLYPH`. */
const DASH = '—';

const EMAIL = process.env.FONDOK_SMOKE_EMAIL ?? '';
const PASSWORD = process.env.FONDOK_SMOKE_PASSWORD ?? '';

// ── helpers ──────────────────────────────────────────────────────────────

const norm = (s: string): string => s.replace(/\s+/g, ' ').trim();
const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * The figure rendered beside `label`.
 *
 * Every "label · value" surface in the app — Overview's `OverviewRow`, Debt's
 * `DebtRow`, Partnership's `KeyRow`, the shared `KpiTile` — puts the label
 * and the value in sibling elements. So: find the deepest element whose whole
 * text is the label, then walk up until an ancestor has a following sibling
 * that carries text. That sibling is the value.
 *
 * Matching is case-insensitive on purpose: `KpiTile` uppercases its label in
 * CSS, and whether a given Playwright build reads the transformed text is not
 * something this spec should depend on.
 *
 * Returns '' when nothing is found, so callers can poll it while the deal's
 * engine outputs are still in flight.
 */
async function figureBeside(scope: Locator, label: string): Promise<string> {
  const labelEl = scope.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first();
  if ((await labelEl.count()) === 0) return '';
  for (const from of ['self::*', 'ancestor::*[1]', 'ancestor::*[2]']) {
    const sibling = labelEl.locator(`xpath=${from}/following-sibling::*[1]`);
    if ((await sibling.count()) === 0) continue;
    const text = norm(await sibling.first().innerText());
    if (text.length > 0) return text;
  }
  return '';
}

/**
 * `label` renders a figure rather than a refusal.
 *
 * Structure, not value: it must contain a digit and must not be the em dash.
 * Polled, because engine outputs arrive over the network after first paint —
 * a one-shot read would race the fetch and go red for the wrong reason.
 */
async function expectFigure(scope: Locator, label: string): Promise<void> {
  await expect(
    scope.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first(),
    `"${label}" is on the page`,
  ).toBeVisible();
  await expect
    .poll(() => figureBeside(scope, label), {
      message: `"${label}" renders a figure, not "${DASH}" — this is the 2026-09-12 regression`,
      timeout: 45_000,
    })
    .toMatch(/\d/);
  expect(await figureBeside(scope, label), `"${label}" is not a bare ${DASH}`).not.toBe(DASH);
}

/**
 * The first of `labels` that is actually on the page, asserted to render a
 * figure. Deliberately tolerant: sibling tickets rename headline rows (FON-66
 * splits Partnership's "Total Equity" into three), and a smoke that goes red
 * on a legitimate rename teaches people to ignore it. What must not change is
 * that the tab has a headline number at all.
 */
async function expectSomeFigure(page: Page, labels: string[]): Promise<string> {
  const body = page.locator('body');
  for (const label of labels) {
    const el = body.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first();
    if ((await el.count()) > 0) {
      await expectFigure(body, label);
      return label;
    }
  }
  throw new Error(
    `None of these headline rows rendered: ${labels.join(', ')}. Either the tab `
      + 'failed to render or every candidate label was renamed — check the tab by hand.',
  );
}

/** Open a tab of the pinned deal and wait for the deal itself to resolve. */
async function openDeal(page: Page, query: string): Promise<void> {
  await page.goto(`/projects/${smokeDealId()}${query}`, { waitUntil: 'domcontentloaded' });
  // The page renders a skeleton until the deal resolves, then either the deal
  // or an error card. Wait the skeleton out before judging anything.
  await expect(page.getByTestId('deal-load-skeleton')).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByTestId('deal-load-error'), 'the deal loaded').toHaveCount(0);
}

/**
 * No engine in the canonical run is `failed`.
 *
 * `EngineFailuresBanner` has no test id (this slice owns no component files),
 * so it is matched by role + its own vocabulary. Note the failure direction:
 * if that copy is rewritten this assertion goes quietly green rather than
 * falsely red, which is the safe way round for a smoke — but it is a real
 * weakness, and a `data-testid="engine-failures"` on that banner would remove
 * it. Filed as the follow-up in e2e/README.md.
 */
async function expectNoEngineFailures(page: Page): Promise<void> {
  await expect(
    page.getByRole('alert').filter({ hasText: /didn.t finish|model errored/i }),
    'no engine in the canonical run failed',
  ).toHaveCount(0);
}

/**
 * The page-level stale-run banner (FON-75) is absent.
 *
 * DEFENSIVE BY DESIGN: at the time of writing that banner does not exist —
 * it is being built in a sibling slice. `toHaveCount(0)` therefore passes
 * today because the element is not in the DOM, and will start meaning
 * something the moment the component ships. Several plausible test ids are
 * checked because this slice cannot know which one that slice picks.
 *
 * WHEN FON-75 LANDS: confirm its `data-testid` is in this list. If it is not,
 * this assertion is a no-op and you have lost the one check that covers every
 * FUTURE missing engine block automatically.
 */
const STALE_RUN_TESTIDS = ['stale-run-banner', 'stale-run', 'run-stale', 'stale-run-needs-rerun'];
async function expectRunNotStale(page: Page): Promise<void> {
  for (const testId of STALE_RUN_TESTIDS) {
    await expect(
      page.getByTestId(testId),
      `the run is not stale (no "${testId}" banner)`,
    ).toHaveCount(0);
  }
}

// ── the smoke ────────────────────────────────────────────────────────────

/** Evaluated once at load for the reason string; re-evaluated per run for the verdict. */
const SKIP_REASON = prodSmokeSkipReason() ?? 'Opt-in only — see e2e/prod/guard.ts.';

test.describe('@prod live deal smoke', () => {
  test.skip(() => prodSmokeSkipReason() !== null, SKIP_REASON);

  test.beforeEach(async ({ page }) => {
    // Coachmarks overlay the rows this spec reads. Browser-local only —
    // nothing is written to the deal.
    await page.addInitScript(() => {
      try {
        window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
      } catch {
        /* private mode — the coachmark just shows; not fatal */
      }
    });

    // The Testing Token is what makes a headless sign-in possible against a
    // Clerk DEVELOPMENT instance; without it Clerk's bot detection refuses.
    // Minted once in e2e/prod/global-setup.ts, attached per page here.
    await setupClerkTestingToken({ page });

    // Clerk must be loaded before `clerk.signIn` can talk to it, and the
    // route must be public or middleware bounces us to /sign-in first.
    await page.goto('/');
    await clerk.loaded({ page });
    await clerk.signIn({
      page,
      signInParams: { strategy: 'password', identifier: EMAIL, password: PASSWORD },
    });
  });

  test('Overview publishes the Stabilization block', async ({ page }) => {
    await openDeal(page, '?tab=overview');

    const stabilization = page.getByTestId('overview-section-stabilization');
    await expect(stabilization, 'the Stabilization section rendered').toBeVisible();

    // THE regression. When a persisted run predates the `stabilization` block
    // this banner appears and every row below is a dash. Its absence is the
    // single most load-bearing assertion in this file.
    await expect(
      stabilization.getByTestId('stabilization-needs-rerun'),
      'the run publishes a stabilization block (no re-run banner)',
    ).toHaveCount(0);

    // …and the rows are actually populated. The banner and the dashes are two
    // separate failures — a future bug could produce either without the other.
    for (const label of [
      'Stabilized Occupancy',
      'Stabilized ADR',
      'Stabilized Revenue',
      'Stabilized NOI',
      'Stabilized NOI Margin',
    ]) {
      await expectFigure(stabilization, label);
    }

    await expectNoEngineFailures(page);
    await expectRunNotStale(page);
  });

  test('Sources & Uses is in balance', async ({ page }) => {
    await openDeal(page, '?tab=investment&sub=sources-and-uses');

    // A structural invariant of the capital stack, not a pinned total:
    // required equity is the plug, so sources MUST equal uses. The two
    // strings are the state itself, not prose about it.
    await expect(
      page.getByText(/^In balance$/i),
      'Sources & Uses reports its in-balance state',
    ).toBeVisible();
    await expect(page.getByText(/^Out of balance$/i)).toHaveCount(0);

    await expectNoEngineFailures(page);
  });

  test('Returns, Debt and Partnership each render a headline figure', async ({ page }) => {
    await openDeal(page, '?tab=returns');
    await expectSomeFigure(page, ['Levered IRR', 'Equity Multiple']);
    await expectNoEngineFailures(page);

    await openDeal(page, '?tab=debt');
    await expectSomeFigure(page, ['Total Debt', 'Senior Loan Amount', 'Equity Requirement']);
    await expectNoEngineFailures(page);

    await openDeal(page, '?tab=partnership');
    await expectSomeFigure(page, [
      'Total Invested Equity',
      'Total Equity',
      'GP / Sponsor Ownership',
      'LP Investor Ownership',
    ]);
    await expectNoEngineFailures(page);
  });

  test('the smoke ran against the aliased production host', async ({ page }) => {
    // Not ceremony. `fondok-app.vercel.app` is a MANUALLY promoted alias; a
    // run against a raw `fondok-<hash>-…vercel.app` deployment URL tests a
    // bundle no analyst is served, which would make this whole file a
    // reassuring lie. Assert what we actually talked to.
    await openDeal(page, '?tab=overview');
    expect(new URL(page.url()).hostname).toBe(PROD_HOSTNAME);
  });
});
