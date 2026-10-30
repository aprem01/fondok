/**
 * Stubs for the worker reads a rendered tab makes but a given test does not
 * care about.
 *
 * Many test files mock `@/lib/api` partially (`...actual.api`) and override
 * only what they assert on. Every other read then ran the REAL request() and
 * fetched `http://test-worker.local/...`: locally that failed in a few ms
 * (ENOTFOUND), on the CI runner the DNS lookup stalled (EAI_AGAIN) and the
 * failure landed at an arbitrary point in the test. vitest.setup.ts now
 * rejects every unmocked fetch, so those reads must be answered here.
 *
 * Usage, inside a `vi.mock('@/lib/api', …)` factory (hoisted, so import
 * dynamically):
 *
 *   const { withQuietWorker } = await import('./helpers/quietWorker');
 *   const actual = withQuietWorker(await importOriginal<typeof import('@/lib/api')>());
 *
 * Each read answers what a worker with no such data answers: an empty list
 * where the response is a list, otherwise a 404 — the "no data" path every
 * consumer already renders (a hidden panel, a dash, no suggestion). A test
 * that asserts on one of these reads overrides it after this, as before.
 *
 * Do not import `@/lib/api` at runtime here: inside the factory that would
 * resolve to the module being mocked.
 */
import type * as ApiModule from '@/lib/api';

type Api = typeof ApiModule;

export function withQuietWorker<T extends Api>(actual: T): T {
  const notFound = (path: string) => () =>
    Promise.reject(new actual.WorkerError(`Not Found: ${path}`, 404, '{"detail":"Not Found"}'));
  const a = actual.api;
  return {
    ...actual,
    api: {
      ...a,
      deals: {
        ...a.deals,
        benchmarkSuggestions: async (id: string) => ({ id, peer_set_size: null, suggestions: [] }),
        renovationBudgetCandidates: async (id: string) => ({
          id,
          selected_source: null,
          selected_value_usd: null,
          keys: null,
          candidates: [],
          reference_only: [],
          precedence: '',
        }),
      },
      comments: { ...a.comments, list: async () => [] },
      market: {
        ...a.market,
        data: notFound('/deals/{id}/market-data'),
        indexMethodology: notFound('/market/{id}/index-methodology'),
        transactionComps: async (dealId: string) => ({
          deal_id: dealId,
          comps: [],
          median_price_per_key: null,
          median_cap_rate_pct: null,
          cap_rate_range: null,
          note: null,
        }),
      },
      validation: { ...a.validation, historicalBaseline: notFound('/deals/{id}/historical-baseline') },
      analysis: {
        ...a.analysis,
        pricing: { ...a.analysis.pricing, askingVsBid: notFound('/analysis/{id}/pricing/asking-vs-bid') },
      },
    },
  } as T;
}
