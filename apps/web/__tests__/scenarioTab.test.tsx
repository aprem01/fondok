/**
 * Scenario Analysis tab — canonical-alignment contracts (FON-53).
 *
 * Locks the four canonical affordances added to the Scenario Analysis tab:
 *
 *   1. BASE-CASE PANEL is read-only — the "Base case assumptions ·
 *      Read-only · sourced from the underwriting model" panel plus the
 *      "Base Case is read-only here…" copy render when Base is focused, with
 *      NO editable inputs.
 *   2. SOURCE OF TRUTH badge rides the Base focus chip (Base is the one deal
 *      the model tabs render).
 *   3. INLINE OVERRIDE TABLE — focusing a saved scenario shows the
 *      Assumption / Base / Scenario / Change / Source columns with each
 *      override resolved against the canonical Base run.
 *   4. PRE-SAVE PREVIEW — the ScenarioEditor drawer renders a Preview block
 *      of projected metric deltas before Save.
 *
 * NOTE: written per the build brief but NOT run here (tsc-only verification).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import { api, type ScenarioRecord } from '@/lib/api';
import ScenarioComparePanel from '@/components/project/ScenarioComparePanel';
import ScenarioEditor from '@/components/project/ScenarioEditor';

// isWorkerConnected → false so no engine fetch fires; everything else is real.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/api');
  return { ...actual, isWorkerConnected: () => false };
});

// The canonical Base run the override table + Base panel read from.
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/hooks/useEngineOutputs');
  const OUTPUTS = {
    deal_id: 'deal-1',
    engines: {
      returns: {
        outputs: { exit_cap_rate: 0.065, hold_years: 5 },
        inputs: {
          assumptions: {
            revpar_growth: 0.03,
            ltv: 0.62,
            interest_rate: 0.0766,
            exit_cap_rate: 0.065,
            hold_years: 5,
          },
        },
      },
      debt: { outputs: { interest_rate: 0.0766, amortization_years: 30, term_years: 5 } },
      capital: { outputs: { ltv: 0.62, purchase_price: 36_436_800 } },
      expense: { inputs: { assumptions: { expense_growth: 0.028 } } },
    },
  };
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS as unknown as import('@/lib/api').EngineOutputsResponse,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: async () => {},
    }),
  };
});

function mkScenario(partial: Partial<ScenarioRecord> & Pick<ScenarioRecord, 'id' | 'name'>): ScenarioRecord {
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

const BASE = mkScenario({ id: 'base', name: 'Base', is_base: true });
const DOWNSIDE = mkScenario({
  id: 's1',
  name: 'Downside',
  description: 'Softer exit',
  overrides: [
    { field_path: 'exit_cap_rate', value: 0.075 },
    { field_path: 'ltv', value: 0.6 },
  ],
});

beforeEach(() => {
  vi.spyOn(api.scenarios, 'compare').mockResolvedValue({
    deal_id: 'deal-1',
    base_scenario_id: 'base',
    scenarios: [],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Scenario Analysis — Base case panel', () => {
  it('renders the read-only Base-case panel + SOURCE OF TRUTH badge by default', () => {
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE]} />);

    expect(screen.getByText('Base case assumptions')).toBeTruthy();
    expect(screen.getByText('Read-only · sourced from the underwriting model')).toBeTruthy();
    expect(
      screen.getByText(/Base Case is read-only here\./i),
    ).toBeTruthy();
    // Base is the source of truth.
    expect(screen.getByText('SOURCE OF TRUTH')).toBeTruthy();
    // Read-only: the panel exposes no inputs.
    expect(document.querySelectorAll('input, select, textarea').length).toBe(0);
  });
});

describe('Scenario Analysis — inline override table', () => {
  it('shows Assumption/Base/Scenario/Change/Source for a focused scenario', () => {
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} />);

    // Focus the Downside scenario (first "Downside" is the focus chip).
    fireEvent.click(screen.getAllByText('Downside')[0]);

    expect(screen.getByText('Assumption')).toBeTruthy();
    expect(screen.getByText('Scenario')).toBeTruthy();
    expect(screen.getByText('Change')).toBeTruthy();
    // Override row resolved against the canonical Base run: 6.50% → 7.50%.
    expect(screen.getByText('Exit Cap Rate')).toBeTruthy();
    expect(screen.getByText('7.50%')).toBeTruthy();
    expect(screen.getByText('6.50%')).toBeTruthy();
    // Change in basis points, Source tab from the shared catalog.
    expect(screen.getByText('+100 bps')).toBeTruthy();
    expect(screen.getByText('Investment →')).toBeTruthy();
  });
});

describe('Scenario Analysis — pre-save Preview', () => {
  it('renders the Preview block with projected metrics in the editor drawer', () => {
    render(
      <ScenarioEditor
        open
        dealId="deal-1"
        scenario={null}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(screen.getByTestId('scenario-preview')).toBeTruthy();
    expect(screen.getByText('Preview')).toBeTruthy();
    expect(screen.getByText('Levered IRR')).toBeTruthy();
    expect(screen.getByText('Equity Multiple')).toBeTruthy();
    expect(screen.getByText('Year-1 CoC')).toBeTruthy();
  });
});
