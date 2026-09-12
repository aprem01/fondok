import type { Page, Route } from '@playwright/test';
import type {
  EngineOutputsResponse,
  ScenarioRecord,
  WorkerDeal,
} from '../../src/lib/api';
import {
  LIVE_DEAL_ID,
  LIVE_DOCS,
  LIVE_EXTRACTIONS,
  LIVE_KEYS,
} from '../../__tests__/helpers/fon41LiveFixture';

/**
 * The worker stub for the `worker-stub` Playwright project.
 *
 * That project's dev server is built with `NEXT_PUBLIC_WORKER_URL=
 * '/__e2e-worker'` — a same-origin path nothing serves — so
 * `isWorkerConnected()` is true and the app takes its real worker-connected
 * code paths. Everything under that prefix is fulfilled here.
 *
 * Two rules keep this honest:
 *
 *  1. The document / extraction payloads are NOT written for this suite.
 *     They are `__tests__/helpers/fon41LiveFixture.ts` — the extraction data
 *     captured off Sam's live deal e577f547 on 2026-09-09, already used by
 *     the component suite. One fixture, two consumers: when the extraction
 *     contract moves, both move together instead of this copy quietly
 *     rotting into a test that passes against a shape the worker stopped
 *     sending.
 *
 *  2. Anything NOT stubbed answers 404, exactly as a worker that has never
 *     run that engine would. Nothing is invented to make a screen look
 *     finished: a spec that needs a number has to say which endpoint
 *     produces it.
 */

export { LIVE_DEAL_ID as STUB_DEAL_ID, LIVE_KEYS as STUB_KEYS };

/** Prefix the app is built to call. Must match playwright.config.ts. */
const WORKER_PREFIX = '/__e2e-worker';

const DEAL: WorkerDeal = {
  id: LIVE_DEAL_ID,
  tenant_id: 'e2e-tenant',
  // Identity matches the fixture's documents (The Angler's, Miami Beach).
  name: "The Angler's",
  city: 'Miami Beach, FL',
  keys: LIVE_KEYS,
  service: 'Lifestyle',
  deal_type: 'acquisition',
  return_profile: 'value-add',
  positioning: 'default',
  brand: null,
  status: 'active',
  deal_stage: 'Underwriting',
  risk: null,
  ai_confidence: null,
  field_overrides: {},
  created_at: '2026-09-08T20:28:12.219536Z',
  updated_at: '2026-09-08T20:28:14.943584Z',
};

/**
 * Minimum viable expense-engine output: `PLTab` renders its "No P&L output
 * yet" placeholder until `expense.outputs.years` is non-empty, so without one
 * the Financials sub-tabs never mount and there is nothing to deep-link to.
 *
 * The three figures are the same single-year block
 * `__tests__/historicalsPeriodFilter.test.tsx` drives GroundedWorksheet with,
 * kept identical rather than re-picked so the two suites describe one deal.
 * No spec below asserts on them — they are a gate, not an expectation.
 */
const ENGINES: EngineOutputsResponse = {
  deal_id: LIVE_DEAL_ID,
  engines: {
    expense: {
      deal_id: LIVE_DEAL_ID,
      engine: 'expense',
      status: 'complete',
      summary: '',
      outputs: {
        years: [
          { year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 },
        ],
      },
      inputs: {},
      error: null,
      runtime_ms: 1,
      started_at: null,
      completed_at: null,
      run_id: 'e2e-run',
    },
    /**
     * FON-74 — `DebtTab` renders its "Debt Engine unavailable" empty state
     * until `debt.outputs.loan_amount` exists, so none of its inline editors
     * (and therefore none of the justification gate on them) ever mounts.
     *
     * Same rule as the expense block above: this is a GATE, not an
     * expectation. `05-override-flow.spec.ts` asserts what the EDITOR does —
     * that Save is refused without a reason and PATCHes `{value, note}` with
     * one — and never asserts a debt figure. The amount matches the senior
     * loan `__tests__/inlineEditIntegrity.test.tsx` drives, kept identical
     * rather than re-picked so the two suites describe one deal.
     */
    debt: {
      deal_id: LIVE_DEAL_ID,
      engine: 'debt',
      status: 'complete',
      summary: '',
      outputs: { loan_amount: 23_400_000 },
      inputs: {},
      error: null,
      runtime_ms: 1,
      started_at: null,
      completed_at: null,
      run_id: 'e2e-run',
    },
  },
} as unknown as EngineOutputsResponse;

const scenario = (over: Partial<ScenarioRecord>): ScenarioRecord => ({
  id: '',
  deal_id: LIVE_DEAL_ID,
  tenant_id: 'e2e-tenant',
  name: '',
  description: null,
  is_base: false,
  in_memo: false,
  overrides: [],
  last_run_id: null,
  created_at: '2026-09-08T20:30:00Z',
  updated_at: '2026-09-08T20:30:00Z',
  ...over,
});

/** Base + one saved scenario — the shape the chip row is built for. */
const SCENARIOS: ScenarioRecord[] = [
  scenario({ id: 'base', name: 'Base', is_base: true, in_memo: true }),
  scenario({
    id: 'downside',
    name: 'Downside',
    overrides: [{ field_path: 'exit_cap_rate', value: 0.075 }],
  }),
];

const ok = (route: Route, body: unknown) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });

const notFound = (route: Route) =>
  route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'not stubbed' }),
  });

/**
 * Install the stub. Call once per test, before the first `page.goto`.
 *
 * `scenarios: []` models a deal that has never saved one (the chip row then
 * renders Base only) — used to prove there is exactly ONE badge on the tab.
 */
export async function stubWorker(
  page: Page,
  opts: { scenarios?: ScenarioRecord[] } = {},
): Promise<void> {
  const scenarios = opts.scenarios ?? SCENARIOS;

  await page.route(`**${WORKER_PREFIX}/**`, (route) => {
    const path = new URL(route.request().url()).pathname.replace(WORKER_PREFIX, '');

    if (path === `/deals/${LIVE_DEAL_ID}`) return ok(route, DEAL);
    if (path === `/deals/${LIVE_DEAL_ID}/status`) {
      return ok(route, {
        id: LIVE_DEAL_ID,
        status: DEAL.status,
        deal_stage: DEAL.deal_stage,
        last_event: null,
      });
    }
    if (path === `/deals/${LIVE_DEAL_ID}/documents`) return ok(route, LIVE_DOCS);
    if (path === `/deals/${LIVE_DEAL_ID}/engines`) return ok(route, ENGINES);
    if (path === `/deals/${LIVE_DEAL_ID}/scenarios`) return ok(route, scenarios);

    const extraction = path.match(/^\/deals\/[^/]+\/documents\/([^/]+)\/extraction$/);
    if (extraction) {
      const result = LIVE_EXTRACTIONS[extraction[1]];
      return result ? ok(route, result) : notFound(route);
    }

    // Everything else: a worker that has not produced this. The UI must
    // render its own empty / refusal state rather than a number.
    return notFound(route);
  });
}

/** Also disables coach marks, whose portals can intercept clicks. */
export async function gotoDeal(page: Page, query: string): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem('fondok:coachmarks:disabled', 'true');
  });
  await page.goto(`/projects/${LIVE_DEAL_ID}${query}`);
}
