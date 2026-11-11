/**
 * Scenario Analysis — FON-69, Sam's 9-14 findings.
 *
 *   1. STALE SCENARIOS — a saved scenario's figures are the run it was
 *      computed on. When the worker dates that run before the current Base
 *      run (`stale: true` on the compare cell), the comparison header carries
 *      a "Stale · re-run" badge that re-runs THAT scenario, re-reads the
 *      comparison and tells the host (`onRan`). Nothing re-runs on its own,
 *      and the Base column never carries the badge.
 *   2. EDITOR PREVIEW — the drawer's Preview posts the whole override map as
 *      `scenario_overrides` (full-model routing, every catalog key), not the
 *      five-key sandbox that dropped e.g. Year-1 Occupancy on the floor, and
 *      its note says plainly what it is.
 *   3. BASE CASE ASSUMPTIONS — Purchase Price renders from the capital engine
 *      whichever way the run publishes it (top-level scalar, inputs, or a
 *      Sources & Uses line), and the row is dropped — not dashed — when no
 *      run states it.
 *
 * `isWorkerConnected` is true here (unlike scenarioTab.test.tsx) so the
 * compare / preview calls fire and can be asserted on; `api.*` is spied.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import {
  api,
  type EngineOutputsResponse,
  type ScenarioCompareResponse,
  type ScenarioRecord,
} from '@/lib/api';
import ScenarioComparePanel, {
  SCENARIO_STALE_LABEL,
} from '@/components/project/ScenarioComparePanel';
import ScenarioEditor, { PREVIEW_NOTE } from '@/components/project/ScenarioEditor';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/api');
  return { ...actual, isWorkerConnected: () => true };
});

// The canonical Base run — mutable per test so each Purchase Price source can
// be exercised against the same panel.
const engineState = vi.hoisted(() => ({
  outputs: null as unknown,
}));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: engineState.outputs as EngineOutputsResponse | null,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: async () => {},
    }),
  };
});

function withCapital(capital: Record<string, unknown>): EngineOutputsResponse {
  return {
    deal_id: 'deal-1',
    engines: {
      returns: { outputs: { exit_cap_rate: 0.065, hold_years: 5 }, inputs: { assumptions: {} } },
      capital,
    },
  } as unknown as EngineOutputsResponse;
}

function mkScenario(
  partial: Partial<ScenarioRecord> & Pick<ScenarioRecord, 'id' | 'name'>,
): ScenarioRecord {
  return {
    deal_id: 'deal-1',
    tenant_id: 't1',
    description: null,
    is_base: false,
    in_memo: false,
    overrides: [],
    last_run_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...partial,
  };
}

const BASE = mkScenario({ id: 'base', name: 'Base', is_base: true, last_run_id: 'run-base-2' });
const DOWNSIDE = mkScenario({
  id: 's1',
  name: 'Downside',
  last_run_id: 'run-s1-1',
  overrides: [
    { field_path: 'exit_cap_rate', value: 0.075 },
    { field_path: 'starting_occupancy', value: 0.55 },
  ],
});

const RETURNS = (irr: number) => ({
  status: 'complete',
  outputs: { levered_irr: irr, equity_multiple: 1.8, cash_flows: [-10_000_000, 1, 1] },
});

function compareResponse(stale: boolean): ScenarioCompareResponse {
  return {
    deal_id: 'deal-1',
    base_scenario_id: 'base',
    base_run_id: 'run-base-2',
    base_run_started_at: '2026-02-01T00:00:00Z',
    scenarios: [
      {
        scenario_id: 'base',
        scenario_name: 'Base',
        is_base: true,
        last_run_id: 'run-base-2',
        run_started_at: '2026-02-01T00:00:00Z',
        stale: false,
        engines: { returns: RETURNS(0.15) },
      },
      {
        scenario_id: 's1',
        scenario_name: 'Downside',
        is_base: false,
        last_run_id: 'run-s1-1',
        run_started_at: '2026-01-15T00:00:00Z',
        stale,
        engines: { returns: RETURNS(0.11) },
      },
    ],
  } as unknown as ScenarioCompareResponse;
}

beforeEach(() => {
  engineState.outputs = withCapital({ outputs: { purchase_price: 36_436_800 } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Base case assumptions — Purchase Price (FON-69 #3)', () => {
  it('renders the price the capital engine publishes as a top-level output', () => {
    vi.spyOn(api.scenarios, 'compare').mockResolvedValue(compareResponse(false));
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE]} />);
    expect(screen.getByText('Purchase Price')).toBeTruthy();
    expect(screen.getByText('$36.4M')).toBeTruthy();
  });

  it('falls back to the capital engine inputs, then its Sources & Uses line', () => {
    vi.spyOn(api.scenarios, 'compare').mockResolvedValue(compareResponse(false));

    engineState.outputs = withCapital({ outputs: {}, inputs: { purchase_price: 41_000_000 } });
    const first = render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE]} />);
    expect(screen.getByText('Purchase Price')).toBeTruthy();
    expect(screen.getByText('$41.0M')).toBeTruthy();
    first.unmount();

    engineState.outputs = withCapital({
      outputs: { uses: [{ label: 'Purchase price', amount: 28_500_000 }] },
    });
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE]} />);
    expect(screen.getByText('Purchase Price')).toBeTruthy();
    expect(screen.getByText('$28.5M')).toBeTruthy();
  });

  it('drops the row (never a dash) when no run states a price', () => {
    vi.spyOn(api.scenarios, 'compare').mockResolvedValue(compareResponse(false));
    engineState.outputs = withCapital({ outputs: {} });
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE]} />);
    expect(screen.queryByText('Purchase Price')).toBeNull();
  });
});

describe('Scenario comparison — stale scenarios (FON-69 #1)', () => {
  it('flags a scenario whose run predates the current Base run, never the Base', async () => {
    vi.spyOn(api.scenarios, 'compare').mockResolvedValue(compareResponse(true));
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} />);

    const badge = await screen.findByTestId('scenario-stale-s1');
    expect(badge.textContent).toBe(SCENARIO_STALE_LABEL);
    expect(badge.getAttribute('title')).toMatch(/older Base run/);
    expect(screen.queryByTestId('scenario-stale-base')).toBeNull();
  });

  it('shows no badge when the worker reports the scenario current (or predates the flag)', async () => {
    const compareSpy = vi
      .spyOn(api.scenarios, 'compare')
      .mockResolvedValue(compareResponse(false));
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} />);
    await waitFor(() => expect(compareSpy).toHaveBeenCalledTimes(1));
    // The column rendered (its IRR is on screen) without a stale badge.
    expect(await screen.findByText('11.0%')).toBeTruthy();
    expect(screen.queryByTestId('scenario-stale-s1')).toBeNull();
  });

  it('one click re-runs THAT scenario, re-reads the comparison and tells the host', async () => {
    const compareSpy = vi
      .spyOn(api.scenarios, 'compare')
      .mockResolvedValueOnce(compareResponse(true))
      .mockResolvedValueOnce(compareResponse(false));
    const runSpy = vi.spyOn(api.scenarios, 'run').mockResolvedValue({
      scenario_id: 's1',
      deal_id: 'deal-1',
      run_id: 'run-s1-2',
      started_at: '2026-02-02T00:00:00Z',
      engines: {},
    });
    const onRan = vi.fn();
    render(
      <ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} onRan={onRan} />,
    );

    fireEvent.click(await screen.findByTestId('scenario-stale-s1'));

    await waitFor(() => expect(runSpy).toHaveBeenCalledWith('deal-1', 's1'));
    expect(runSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(compareSpy).toHaveBeenCalledTimes(2));
    expect(onRan).toHaveBeenCalledWith(DOWNSIDE);
    // The fresh comparison carries no stale column.
    await waitFor(() => expect(screen.queryByTestId('scenario-stale-s1')).toBeNull());
  });
});

describe('Scenario editor — Preview runs the full model (FON-69 #2)', () => {
  it('posts the whole override map as scenario_overrides and says what the preview is', async () => {
    const previewSpy = vi.spyOn(api.engines, 'returnsPreview').mockResolvedValue({
      deal_id: 'deal-1',
      levered_irr: 0.121,
      unlevered_irr: 0.09,
      equity_multiple: 1.6,
      year_one_coc: 0.05,
      exit_value: 50_000_000,
      net_proceeds: 20_000_000,
      dscr_y1: 1.4,
      hold_years: 5,
      exit_cap_rate: 0.075,
      sensitivity: null,
    });
    render(
      <ScenarioEditor
        open
        dealId="deal-1"
        scenario={DOWNSIDE}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(screen.getByTestId('scenario-preview-note').textContent).toBe(PREVIEW_NOTE);
    expect(PREVIEW_NOTE).toMatch(/Full model/);
    expect(PREVIEW_NOTE).toMatch(/Nothing is written until you Save/);

    // Debounced (350ms); both calls go through the full-model seam — the
    // Base with no overrides, the scenario with EVERY row (Year-1 Occupancy
    // is not one of the five sandbox keys and must not be dropped).
    await waitFor(() => expect(previewSpy).toHaveBeenCalledTimes(2), { timeout: 2000 });
    const bodies = previewSpy.mock.calls.map((c) => c[1]);
    expect(bodies).toContainEqual({});
    expect(bodies).toContainEqual({
      scenario_overrides: { exit_cap_rate: 0.075, starting_occupancy: 0.55 },
    });
    expect(bodies.some((b) => 'overrides' in (b as object))).toBe(false);
  });
});
