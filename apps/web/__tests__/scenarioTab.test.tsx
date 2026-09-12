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
 * FON-69 adds the design-reconciliation contracts for the scenario surface:
 *
 *   5. ••• ACTION STRIP — opens on CLICK and stays open across a pointer move.
 *      This is the direct regression for Sam's complaint that the Downside edit
 *      menu "requires hovering specifically over the '3 changes' badge to keep
 *      the menu open long enough to select Edit overrides."
 *   6. ONE chip row — exactly one SOURCE OF TRUTH badge on the tab.
 *   7. NO RAW FIELD PATHS — nothing in the scenario surface renders a canonical
 *      field path (e.g. `exit_cap_rate`); asserted against the catalog itself,
 *      so a newly added assumption is covered without editing this test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import { api, type ScenarioRecord } from '@/lib/api';
import ScenarioComparePanel from '@/components/project/ScenarioComparePanel';
import ScenarioAnalysisTab from '@/components/project/ScenarioAnalysisTab';
import ScenarioEditor, { ASSUMPTION_CATALOG } from '@/components/project/ScenarioEditor';

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

// ───────────────────────── FON-69 design reconciliation ─────────────────────

/** The management callbacks the project page passes down. */
function mgmt() {
  return {
    onCreate: vi.fn(),
    onEdit: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete: vi.fn(),
  };
}

function openDownsideMenu() {
  fireEvent.click(screen.getByRole('button', { name: 'Scenario actions for Downside' }));
  // "Edit overrides" exists in BOTH canonical places (the strip and the
  // override-table header) — scope every strip assertion to the strip.
  return within(screen.getByTestId('scenario-action-strip'));
}

describe('Scenario Analysis — ••• scenario actions (FON-69)', () => {
  it('opens on CLICK and stays open across a pointer move', () => {
    const h = mgmt();
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />);

    // Closed until clicked — the old surface opened on hover.
    expect(screen.queryByTestId('scenario-action-strip')).toBeNull();

    const dots = screen.getByRole('button', { name: 'Scenario actions for Downside' });
    fireEvent.mouseEnter(dots);
    expect(screen.queryByTestId('scenario-action-strip')).toBeNull();

    fireEvent.click(dots);
    const strip = screen.getByTestId('scenario-action-strip');
    expect(strip).toBeTruthy();

    // Sam's exact complaint: the pointer leaving the chip used to dismiss the
    // menu before "Edit overrides" could be reached. It must survive all of it.
    fireEvent.mouseLeave(dots);
    fireEvent.mouseOut(dots);
    fireEvent.mouseMove(document.body);
    fireEvent.mouseLeave(strip.parentElement as HTMLElement);
    const stillOpen = screen.getByTestId('scenario-action-strip');
    expect(stillOpen).toBeTruthy();
    expect(
      within(stillOpen).getByRole('button', { name: 'Edit overrides' }),
    ).toBeTruthy();
  });

  it('closes on Escape', () => {
    const h = mgmt();
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />);
    openDownsideMenu();
    expect(screen.getByTestId('scenario-action-strip')).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('scenario-action-strip')).toBeNull();
  });

  it('closes on an outside click', () => {
    const h = mgmt();
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />);
    openDownsideMenu();
    expect(screen.getByTestId('scenario-action-strip')).toBeTruthy();

    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('scenario-action-strip')).toBeNull();
  });

  it('fires Edit / Duplicate / Delete with the scenario', () => {
    const h = mgmt();
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />);

    fireEvent.click(openDownsideMenu().getByRole('button', { name: 'Edit overrides' }));
    expect(h.onEdit).toHaveBeenCalledWith(DOWNSIDE);

    fireEvent.click(openDownsideMenu().getByRole('button', { name: 'Duplicate' }));
    expect(h.onDuplicate).toHaveBeenCalledWith(DOWNSIDE);

    fireEvent.click(openDownsideMenu().getByRole('button', { name: 'Delete' }));
    expect(h.onDelete).toHaveBeenCalledWith(DOWNSIDE);
  });

  it('"+ New scenario" lives in the chip row and fires onCreate', () => {
    const h = mgmt();
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />);

    fireEvent.click(screen.getByRole('button', { name: '+ New scenario' }));
    expect(h.onCreate).toHaveBeenCalledTimes(1);
  });

  it('renders no management affordances when the host passes no callbacks', () => {
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} />);
    expect(screen.queryByRole('button', { name: /Scenario actions/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '+ New scenario' })).toBeNull();
  });
});

describe('Scenario Analysis — override table entry points (FON-69)', () => {
  it('offers "Edit overrides" in the table header and says so when empty', () => {
    const h = mgmt();
    const empty = mkScenario({ id: 's2', name: 'Upside' });
    render(<ScenarioComparePanel dealId="deal-1" scenarios={[BASE, empty]} {...h} />);

    fireEvent.click(screen.getAllByText('Upside')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit overrides' }));
    expect(h.onEdit).toHaveBeenCalledWith(empty);

    // The empty state points at that button, not at a hover popover.
    const emptyState = screen.getByText(/No overrides yet/);
    expect(emptyState.textContent).toContain('Edit overrides');
    expect(emptyState.textContent).not.toContain('on the pill above');
  });

  it('per-row ✕ asks the host to drop that one override', () => {
    const onRemoveOverride = vi.fn();
    render(
      <ScenarioComparePanel
        dealId="deal-1"
        scenarios={[BASE, DOWNSIDE]}
        onRemoveOverride={onRemoveOverride}
      />,
    );

    fireEvent.click(screen.getAllByText('Downside')[0]);
    fireEvent.click(
      screen.getByRole('button', { name: 'Remove the Exit Cap Rate override' }),
    );
    expect(onRemoveOverride).toHaveBeenCalledWith(DOWNSIDE, 'exit_cap_rate');
  });
});

describe('Scenario Analysis — one chip row, human labels only (FON-69)', () => {
  it('renders exactly ONE SOURCE OF TRUTH badge on the tab', () => {
    // Mirrors the project page's `activeTab === "scenarios"` composition.
    const h = mgmt();
    render(
      <div>
        <ScenarioAnalysisTab dealId="deal-1" />
        <ScenarioComparePanel dealId="deal-1" scenarios={[BASE, DOWNSIDE]} {...h} />
      </div>,
    );

    expect(screen.getAllByText('SOURCE OF TRUTH').length).toBe(1);
  });

  it('never renders a raw canonical field path anywhere in the surface', () => {
    const h = mgmt();
    // Every catalog path as an override, so the assertion covers the catalog
    // rather than a hard-coded list that drifts as assumptions are added.
    const everything = mkScenario({
      id: 's3',
      name: 'Kitchen sink',
      overrides: ASSUMPTION_CATALOG.map((a) => ({ field_path: a.path, value: 0.5 })),
    });
    render(
      <div>
        <ScenarioAnalysisTab dealId="deal-1" />
        <ScenarioComparePanel dealId="deal-1" scenarios={[BASE, everything]} {...h} />
      </div>,
    );

    // Focus the scenario (override table) and open its action strip — the two
    // surfaces the raw paths used to leak through.
    fireEvent.click(screen.getAllByText('Kitchen sink')[0]);
    fireEvent.click(
      screen.getByRole('button', { name: 'Scenario actions for Kitchen sink' }),
    );

    const rendered = document.body.textContent ?? '';
    const leaked = ASSUMPTION_CATALOG.map((a) => a.path).filter((path) =>
      rendered.includes(path),
    );
    expect(leaked).toEqual([]);
  });
});
