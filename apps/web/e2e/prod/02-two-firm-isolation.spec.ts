import { test, expect, type Browser, type Page } from '@playwright/test';
import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright';
import { PROD_HOSTNAME, smokeDealId, twoFirmSkipReason } from './guard';

/**
 * Two-firm workspace isolation, through the deployed app — FON-93 Phase 3.
 *
 * ── What it proves ──────────────────────────────────────────────────────
 * `01-live-deal-smoke` proves firm A can read its own deal. This proves the
 * other half of the tenancy contract from the user's seat: signed in as a
 * member of a DIFFERENT institution (firm B), firm A's deal URL renders the
 * app's not-found state and firm B's project list contains none of firm A's
 * deals. It is the browser-level twin of `apps/worker/scripts/isolation_check.py`,
 * which asserts the same thing route-by-route over HTTP; this file asserts
 * what an analyst would actually see.
 *
 * ── The not-found state, checked ────────────────────────────────────────
 * `src/app/projects/[id]/page.tsx` renders `data-testid="deal-load-error"`
 * with the heading "Could not load this deal" whenever `useDeal` fails. A
 * cross-tenant deal id makes the worker answer `404` on `GET /deals/{id}`
 * (every deal-scoped route 404s a foreign tenant — never 403, never a 200
 * with an empty body), so the card's detail line reads `GET /deals/<id> → 404`.
 * The spec asserts the test id and the heading, and that the skeleton has
 * gone — it never asserts the detail prose.
 *
 * ── Read-only ───────────────────────────────────────────────────────────
 * Every navigation is a GET. Two browser contexts, one per firm, each with
 * its own Clerk session; nothing is created, patched or deleted in either
 * workspace. The only state written is the coachmark-suppression key in
 * each throwaway profile's localStorage.
 *
 * ── Opt-in ──────────────────────────────────────────────────────────────
 * Same two opt-ins as 01 (see ./guard.ts) PLUS the firm-B secrets
 * `FONDOK_SMOKE_B_EMAIL`, `FONDOK_SMOKE_B_PASSWORD`, `FONDOK_SMOKE_B_ORG`.
 * Without them this file skips itself naming what is missing, so the
 * live-deal smoke keeps running on a deploy where firm B is not provisioned
 * yet. Firm B must be a member of a different Clerk organization from the
 * smoke user, and should own a deal or two so its list is not trivially
 * empty.
 *
 *   CLERK_PUBLISHABLE_KEY=pk_test_… CLERK_SECRET_KEY=sk_test_… \
 *   FONDOK_SMOKE_EMAIL=… FONDOK_SMOKE_PASSWORD=… FONDOK_SMOKE_DEAL_ID=… \
 *   FONDOK_SMOKE_B_EMAIL=… FONDOK_SMOKE_B_PASSWORD=… FONDOK_SMOKE_B_ORG=org_… \
 *   FONDOK_E2E_PROD=1 PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app \
 *     pnpm test:e2e:prod
 */

const A = {
  email: process.env.FONDOK_SMOKE_EMAIL ?? '',
  password: process.env.FONDOK_SMOKE_PASSWORD ?? '',
  // Optional: pin firm A's org too. The smoke user normally belongs to one org.
  org: (process.env.FONDOK_SMOKE_ORG ?? '').trim(),
};
const B = {
  email: process.env.FONDOK_SMOKE_B_EMAIL ?? '',
  password: process.env.FONDOK_SMOKE_B_PASSWORD ?? '',
  org: (process.env.FONDOK_SMOKE_B_ORG ?? '').trim(),
};

/** How long a deal page or the project list may take to settle on a cold prod box. */
const SETTLE_MS = 60_000;

// ── helpers ──────────────────────────────────────────────────────────────

const norm = (s: string): string => s.replace(/\s+/g, ' ').trim();

/** A fresh context + page signed in as `who`, with `org` made active when given. */
async function signIn(
  browser: Browser,
  who: { email: string; password: string; org: string },
): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
    } catch {
      /* private mode — the coachmark just shows; not fatal */
    }
  });
  await setupClerkTestingToken({ page });
  await page.goto('/');
  await clerk.loaded({ page });
  await clerk.signIn({
    page,
    signInParams: { strategy: 'password', identifier: who.email, password: who.password },
  });
  if (who.org) {
    await activateOrg(page, who.org);
  }
  return page;
}

/**
 * Make `org` (an `org_…` id or a slug) the session's active organization,
 * and refuse to continue as any other. The worker resolves the tenant from
 * the JWT's active org and nothing else, so a firm-B session that happened
 * to have firm A's org active would make this whole spec test nothing.
 */
async function activateOrg(page: Page, org: string): Promise<void> {
  const active = await page.evaluate(async (wanted: string) => {
    type Org = { id: string; slug: string | null };
    type Membership = { organization: Org };
    const w = window as unknown as {
      Clerk?: {
        organization: Org | null;
        user: { organizationMemberships: Membership[] } | null;
        setActive: (p: { organization: string }) => Promise<void>;
      };
    };
    const c = w.Clerk;
    if (!c || !c.user) return null;
    const matches = (o: Org | null): boolean => !!o && (o.id === wanted || o.slug === wanted);
    if (!matches(c.organization)) {
      const m = c.user.organizationMemberships.find((x) => matches(x.organization));
      if (!m) return `not-a-member:${wanted}`;
      await c.setActive({ organization: m.organization.id });
    }
    return c.organization ? `${c.organization.id}|${c.organization.slug ?? ''}` : null;
  }, org);
  expect(active, `the session has ${org} as its active organization`).not.toBeNull();
  expect(active, `the test user is a member of ${org}`).not.toMatch(/^not-a-member:/);
  const [id, slug] = String(active).split('|');
  expect([id, slug], `active org is ${org}`).toContain(org);
}

/** Wait for a deal page to leave its skeleton; returns whether it errored. */
async function settleDealPage(page: Page, dealId: string): Promise<boolean> {
  await page.goto(`/projects/${dealId}?tab=overview`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('deal-load-skeleton')).toHaveCount(0, { timeout: SETTLE_MS });
  return (await page.getByTestId('deal-load-error').count()) > 0;
}

type Row = { href: string; id: string; name: string };

/**
 * The deals on `/projects`, once the list has settled: either at least one
 * `/projects/<uuid>` link is rendered, or the page's empty state is. Each
 * card renders the deal name as its first text line.
 */
async function projectRows(page: Page): Promise<Row[]> {
  await page.goto('/projects', { waitUntil: 'domcontentloaded' });
  const links = page.locator('a[href^="/projects/"]:not([href="/projects/new"])');
  const empty = page.getByText(/^No deals yet$|^No deals match your filters$/);
  await expect
    .poll(async () => (await links.count()) > 0 || (await empty.count()) > 0, {
      message: 'the project list settled (cards or the empty state)',
      timeout: SETTLE_MS,
    })
    .toBe(true);
  const rows: Row[] = [];
  for (const link of await links.all()) {
    const href = (await link.getAttribute('href')) ?? '';
    const id = href.replace(/^\/projects\//, '').split(/[?#]/)[0];
    const name = norm((await link.innerText()).split('\n')[0] ?? '');
    rows.push({ href, id, name });
  }
  return rows;
}

// ── the spec ─────────────────────────────────────────────────────────────

const SKIP_REASON = twoFirmSkipReason() ?? 'Opt-in only — see e2e/prod/guard.ts.';

test.describe('@prod two-firm isolation', () => {
  test.skip(() => twoFirmSkipReason() !== null, SKIP_REASON);

  test('firm B cannot open firm A’s deal and sees none of its deals', async ({ browser }) => {
    test.setTimeout(5 * 60_000);
    const dealId = smokeDealId();

    // ── Firm A: the deal loads, and we learn its names ──
    const pageA = await signIn(browser, A);
    const errorA = await settleDealPage(pageA, dealId);
    expect(errorA, 'firm A can open its own deal (precondition for the rest)').toBe(false);
    expect(new URL(pageA.url()).hostname).toBe(PROD_HOSTNAME);

    const rowsA = await projectRows(pageA);
    expect(
      rowsA.map((r) => r.id),
      'the pinned deal is in firm A’s own list (otherwise the smoke user is in the wrong org)',
    ).toContain(dealId);
    const idsA = new Set(rowsA.map((r) => r.id));
    const namesA = rowsA.map((r) => r.name).filter((n) => n.length > 0);
    await pageA.context().close();

    // ── Firm B: the same URL is a dead end ──
    const pageB = await signIn(browser, B);
    const errorB = await settleDealPage(pageB, dealId);
    expect(errorB, 'firm B gets the not-found state on firm A’s deal URL').toBe(true);
    const card = pageB.getByTestId('deal-load-error');
    await expect(card).toBeVisible();
    await expect(card.getByText(/^Could not load this deal$/)).toBeVisible();
    // The URL's id is echoed (so the analyst can check the link); the deal's
    // NAME must not be — that is firm A's data.
    await expect(card).toContainText(dealId);
    const nameA = rowsA.find((r) => r.id === dealId)?.name ?? '';
    if (nameA.length >= 4) {
      await expect(pageB.locator('body')).not.toContainText(nameA);
    }

    // ── Firm B's list shares nothing with firm A's ──
    const rowsB = await projectRows(pageB);
    const sharedIds = rowsB.map((r) => r.id).filter((id) => idsA.has(id));
    expect(sharedIds, 'no deal id appears in both firms’ lists').toEqual([]);
    const sharedNames = rowsB.map((r) => r.name).filter((n) => namesA.includes(n));
    expect(sharedNames, 'no firm-A deal name appears in firm B’s list').toEqual([]);
    expect(new URL(pageB.url()).hostname).toBe(PROD_HOSTNAME);
    await pageB.context().close();
  });
});
