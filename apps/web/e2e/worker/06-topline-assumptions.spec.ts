import { test, expect } from '@playwright/test';
import type { Page, Request, Route } from '@playwright/test';
import { gotoDeal, stubWorker, STUB_DEAL_ID } from '../fixtures/workerStub';

/**
 * FON-116 / FON-117 — the Future P&L topline, end to end through the browser
 * (plan §5.2: "edit ramp → re-run → badge").
 *
 * The worker is stubbed (`e2e/fixtures/workerStub.ts`), so this drives the
 * REAL worker-connected code: the provenance payload's `topline_status`
 * feeds the basis line and the Topline card; editing the ramp PATCHes
 * `field_overrides.penetration_ramp_by_year` with the analyst's note (the
 * worker refuses it without one); the re-run's payload then reports the
 * analyst's ramp and the basis line says so.
 */

type Patch = { field_overrides?: Record<string, unknown> };

const revYear = (year: number, occupancy: number, adr: number) => ({
  year, occupancy, adr, revpar: occupancy * adr,
  rooms_revenue: 10_000_000, fb_revenue: 2_000_000, other_revenue: 500_000, total_revenue: 12_500_000,
});
const expYear = (year: number) => ({
  year,
  total_revenue: 12_500_000,
  dept_expenses: { rooms: 2_500_000, food_beverage: 1_500_000, other_operated: 250_000, total: 4_250_000 },
  undistributed: { administrative_general: 900_000, information_telecom: 180_000, sales_marketing: 800_000, property_operations: 500_000, utilities: 560_000, total: 2_940_000 },
  mgmt_fee: 375_000, ffe_reserve: 500_000,
  fixed_charges: { property_taxes: 700_000, insurance: 200_000, rent: 0, other_fixed: 0, total: 900_000 },
  gop: 5_310_000, noi: 3_535_000, noi_institutional: 4_035_000,
});

function engines(ramp: number[]) {
  const row = (engine: string, outputs: unknown) => ({
    deal_id: STUB_DEAL_ID, engine, status: 'complete', summary: '', outputs, inputs: {},
    error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'e2e-run',
  });
  const occ = [0.755 * ramp[0], 0.755 * ramp[1], 0.755];
  return {
    deal_id: STUB_DEAL_ID,
    engines: {
      revenue: row('revenue', {
        years: [revYear(1, occ[0], 300), revYear(2, occ[1], 305), revYear(3, occ[2], 310)],
        topline_methodology_version: 2,
        topline_by_year: [1, 2, 3].map((y) => ({ year: y, mpi_target: 100, ari_target: 100, ramp_pct: y < 3 ? ramp[y - 1] : null, occupancy: occ[y - 1], adr: 300 })),
      }),
      expense: row('expense', { years: [1, 2, 3].map(expYear) }),
    },
  };
}

function toplineStatus(ramp: number[], source: 'default' | 'analyst_override') {
  const g = (label: string) => ({ value: null, source: 'unavailable', source_id: 'unavailable', label, reason: 'no_market_forecast' });
  return {
    methodology_version: 2,
    benchmark_level: 'comp_set',
    benchmark_label: 'STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx',
    benchmark_fallback_reason: null,
    benchmark_by_year: [1, 2, 3].map((y) => ({ year: y, occupancy: 0.755, adr: 310.78, revpar: 234.6, basis: 'actual', level: 'comp_set', label: 'Actual · STR TTM May 2025' })),
    calendar_known: false,
    calendar_unknown: false,
    growth_state: 'unavailable',
    growth_by_year: [1, 2, 3].map((y) => ({ year: y, calendar_year: null, applied: true, occupancy: g('held flat — no figure'), adr: g('held flat — no figure') })),
    stabilization_year: 3,
    stabilization_source: 'default_year_3',
    penetration_occ: 100, penetration_adr: 100,
    penetration_occ_source: 'default', penetration_adr_source: 'default',
    penetration_occ_by_year: [], penetration_adr_by_year: [],
    current_mpi: { value: 109.8, label: 'Current MPI 109.8 (STR TTM May 2025)', basis: 'str_published' },
    current_ari: { value: 75.1, label: 'Current ARI 75.1 (STR TTM May 2025)', basis: 'str_published' },
    ramp_by_year: ramp,
    ramp_source: source,
    ramp_policy: 'all_deals',
    ramp_state: 'defined',
    explicit_metrics: [],
    t12_reference: null,
    terminal_revpar_growth: { value: 0, source_id: 'market_forecast_unavailable', basis: 'unavailable', label: 'not grown' },
    suggestions: [{ key: 'revpar_growth', value: 0.045, label: 'Apply platform suggestion 4.5% RevPAR growth (labelled; not a market forecast)', note_prefill: 'Platform suggestion 4.5% — no market forecast on file' }],
    legacy_flags: [], problems: [], notes: [], reextract_needed: [],
    calendar_anchor: { source: null, date: null, assumed: false, first_model_year: null, label: '' },
    revenue_basis: { state: 'present', missing: [], message: null },
  };
}

/** Serve the topline payloads; `state.ramp` switches after the PATCH lands. */
async function stubTopline(page: Page, state: { ramp: number[]; source: 'default' | 'analyst_override' }, patches: Patch[]) {
  await page.route(`**/__e2e-worker/deals/${STUB_DEAL_ID}**`, (route: Route, request: Request) => {
    const path = new URL(request.url()).pathname.replace('/__e2e-worker', '');
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === `/deals/${STUB_DEAL_ID}` && request.method() === 'PATCH') {
      const body = JSON.parse(request.postData() || '{}') as Patch;
      patches.push(body);
      // The worker's re-run would now report the analyst's ramp.
      const ov = body.field_overrides?.penetration_ramp_by_year as { value?: number[] } | undefined;
      if (ov?.value) {
        state.ramp = ov.value;
        state.source = 'analyst_override';
      }
      return json({ id: STUB_DEAL_ID });
    }
    if (path === `/deals/${STUB_DEAL_ID}/assumption_sources`) {
      return json({ id: STUB_DEAL_ID, sources: {}, values: {}, topline_status: toplineStatus(state.ramp, state.source) });
    }
    if (path === `/deals/${STUB_DEAL_ID}/engines` && request.method() === 'GET') return json(engines(state.ramp));
    return route.fallback();
  });
}

test.describe('the Future P&L topline (FON-116 / FON-117)', () => {
  test('edit the ramp with a note → re-run → the basis line names your ramp', async ({ page }) => {
    await stubWorker(page);
    const patches: Patch[] = [];
    const state: { ramp: number[]; source: 'default' | 'analyst_override' } = { ramp: [0.85, 0.95], source: 'default' };
    await stubTopline(page, state, patches);
    await gotoDeal(page, '?tab=pl&sub=projections');

    await expect(page.getByTestId('topline-basis-line')).toContainText('Benchmark: STR comp set TTM May 2025');
    await expect(page.getByTestId('topline-clause-ramp')).toHaveText(/Ramp 85\/95\/100 \(default\)/);
    await expect(page.getByTestId('topline-growth-unavailable')).toContainText('No market forecast — set growth manually');
    // The 4.5% is offered only as the worker's labelled suggestion.
    await expect(page.getByTestId('topline-suggestion-apply')).toContainText('labelled; not a market forecast');

    const card = page.getByTestId('topline-card');
    await card.getByLabel('Ramp Year 1 (%)').fill('90');
    // No note → no PATCH.
    await card.getByTestId('topline-ramp-save').click();
    await expect(card.getByTestId('topline-ramp-error')).toBeVisible();
    expect(patches).toHaveLength(0);

    await card.getByLabel('Ramp — override justification').fill('Soft opening — brand conversion');
    await card.getByTestId('topline-ramp-save').click();
    await expect.poll(() => patches.length).toBeGreaterThan(0);
    expect(patches[0].field_overrides?.penetration_ramp_by_year).toEqual({
      value: [0.9, 0.95],
      note: 'Soft opening — brand conversion',
    });

    // The re-run's provenance payload reports the analyst's ramp.
    await page.reload();
    await expect(page.getByTestId('topline-clause-ramp')).toHaveText(/Ramp 90\/95\/100 \(your ramp\)/);
  });
});
