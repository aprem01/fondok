/**
 * Investment tab — canonical rebuild (design-match) + Move-2 step 4.
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED RENDER — the 5 KPI tiles, the Sources & Uses balance
 *     banner, and the Transaction Timeline all resolve from a single mocked
 *     worker engine-outputs envelope (the real ``getEngineField`` is exercised).
 *     No fixtures, no prototype numbers.
 *
 *  2. PROVIDER-FREE — InvestmentTab renders with NO <AssumptionsProvider>
 *     present. The old ``ctx &&`` gate on Sources & Uses is gone, so the S&U
 *     view renders from engine output alone (Move-2 step 4 — Investment is off
 *     the page assumptions provider).
 *
 *  3. CANONICAL SAVE PATH — editing an assumption (Purchase Price) PATCHes
 *     ``field_overrides`` via api.deals.update — the same path Deal Summary
 *     already used — NOT the local assumptionsStore. This fixes the dual-store
 *     data-integrity bug.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, TimelineResponse } from '@/lib/api';

// Mutable routing state (FON-59 #4) - `params` is a REAL URLSearchParams, what
// Next's ReadonlyURLSearchParams behaves like, so `useSubTab`'s toString()
// round-trip is exercised rather than stubbed.
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-uuid-1',
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1',
      engine: 'capital',
      status: 'complete',
      summary: '',
      outputs: {
        purchase_price: 34_000_000,
        price_per_key: 257_576,
        entry_cap_rate: 0.075,
        total_capital_usd: 43_000_000,
        total_capital_per_key: 325_758,
        equity_amount: 17_000_000,
        debt_amount: 26_000_000,
        // Labels are the capital engine's own (`capital.py` property_lines),
        // because `SU_LABEL_ASSUMPTION` mirrors its `_USE_LABEL_ASSUMPTIONS`
        // map by exact label.
        uses: [
          { label: 'Purchase Price', amount: 34_000_000, pct: 0.79 },
          { label: 'Closing Costs', amount: 680_000, pct: 0.016 },
          { label: 'Renovation', amount: 4_620_000, pct: 0.107 },
          { label: 'Working Capital', amount: 500_000, pct: 0.012 },
          { label: 'Total Uses', amount: 43_000_000, pct: 1, is_total: true },
        ],
        sources: [
          { label: 'Senior Loan', amount: 26_000_000, pct: 0.6 },
          { label: 'Equity', amount: 17_000_000, pct: 0.4 },
          { label: 'Total Sources', amount: 43_000_000, pct: 1, is_total: true },
        ],
      },
      inputs: {},
      error: null,
      runtime_ms: 10,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: {
        gross_sale_price: 52_000_000,
        exit_cap_rate: 0.07,
        terminal_noi: 3_640_000,
        selling_costs: 520_000,
        hold_years: 5,
      },
      inputs: {},
      error: null,
      runtime_ms: 9,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1',
      engine: 'expense',
      status: 'complete',
      summary: '',
      // Two DIFFERENT NOI bases: `noi_institutional` is NOI before the FF&E
      // reserve (what Entry / Run-Rate NOI reads), `noi` is Cash NOI after it.
      outputs: { years: [{ year: 1, noi: 2_550_000, noi_institutional: 3_100_000 }] },
      inputs: {},
      error: null,
      runtime_ms: 5,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

const TIMELINE = {
  deal_id: 'deal-uuid-1',
  close_date: '2027-03-31',
  exit_date: '2032-03-31',
  stabilization_date: '2029-06-30',
  events: [
    // FON-44 §2 — the worker now stamps Hotel Purchase `linked`: it consumes
    // the editable Acquisition Date rather than calculating anything.
    { event: 'Hotel Purchase', start: '2027-03-31', duration_months: 0, finish: '2027-03-31', basis: 'linked' },
    { event: 'Renovation', start: '2027-06-30', duration_months: 12, finish: '2028-06-30', basis: 'assumption' },
    { event: 'Stabilized (FTM NOI, Value)', start: '2029-06-30', duration_months: 0, finish: '2029-06-30', basis: 'derived' },
    { event: 'Senior Loan Maturity', start: '2032-03-31', duration_months: 0, finish: '2032-03-31', basis: 'derived' },
  ],
} as unknown as TimelineResponse;

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, field_overrides: {} },
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({
  useHistoricalBaseline: () => ({ baseline: null }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

// api surface — spy on the field_overrides PATCH; serve the timeline.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
const timelineSpy = vi.fn(async () => TIMELINE);
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: { ...actual.api.engines, timeline: (...a: unknown[]) => timelineSpy(...(a as [])) },
    },
  };
});

// Trim the heavy chrome to nothing — the test only cares about the sub-tab bodies.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/CapexPlanPanel', () => ({
  default: () => null,
  DEFAULT_CAPEX_PLAN: {},
}));
vi.mock('@/components/project/HistoricalBaselinePanel', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// Phase 4.4 — the deal's refusal codes ride the assumption_sources payload
// (`reasons[key]`, a bare ReasonCode) beside the source tags. EMPTY unless a
// test sets one, which is exactly what this provider-free render resolved to
// before, so every pre-existing expectation below is untouched.
let mockReasons: Record<string, string> = {};
let mockSources: Record<string, string> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) => {
    if (!key) return null;
    const source = mockSources[key];
    const reason = mockReasons[key];
    if (!source && !reason) return null;
    return { source: source ?? '', value: null, reason: reason ?? null };
  },
}));

// FON-44 §1 — the computed-value provenance graph. Empty by default, which is
// exactly what the provider-free render resolved to before, so every
// pre-existing expectation is untouched.
let mockTraces: Record<string, Record<string, unknown>> = {};
vi.mock('@/lib/hooks/useValueTrace', () => ({
  useTraceGraph: (engine: string | undefined) => ({
    get: (path: string) => (engine ? (mockTraces[engine]?.[path] ?? null) : null),
  }),
}));

import InvestmentTab from '@/components/project/InvestmentTab';
import { REASONS } from '@/lib/ontology/reasons.generated';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  timelineSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
  mockReasons = {};
  mockSources = {};
  mockTraces = {};
});

describe('InvestmentTab — engine-sourced KPI tiles (no provider present)', () => {
  it('renders all 5 KPI tiles from the mocked engine outputs', () => {
    // NOTE: rendered bare — NO <AssumptionsProvider>. Proves Move-2 step 4.
    render(<InvestmentTab />);
    // KPI labels (some also appear as section-row labels → use getAllByText).
    expect(screen.getByText('Total Cost Basis')).toBeInTheDocument();
    expect(screen.getByText('Required Equity')).toBeInTheDocument();
    expect(screen.getAllByText('Purchase Price').length).toBeGreaterThan(0);
    expect(screen.getByText('Renovation / PIP')).toBeInTheDocument();
    expect(screen.getAllByText('Gross Exit Value').length).toBeGreaterThan(0);

    // Values are the $M engine figures, not prototype placeholders.
    expect(screen.getByText('$43.00M')).toBeInTheDocument(); // total cost basis
    expect(screen.getByText('$34.00M')).toBeInTheDocument(); // purchase price
    expect(screen.getByText('$4.62M')).toBeInTheDocument();  // renovation / PIP
    expect(screen.getByText('$17.00M')).toBeInTheDocument(); // required equity
    expect(screen.getByText('$52.00M')).toBeInTheDocument(); // gross exit value
  });
});

describe('InvestmentTab — Sources & Uses (ctx gate removed)', () => {
  it('renders the in-balance banner from engine sources/uses with no provider', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));

    // Balance banner resolves from the engine totals (43M == 43M → In balance).
    expect(screen.getByText('In balance')).toBeInTheDocument();
    expect(
      screen.getByText(/Required equity is the plug — every other line is owned by/i),
    ).toBeInTheDocument();
    // The canonical Amount / Key / % column headers are present (Uses + Sources).
    expect(screen.getAllByText('/ Key').length).toBe(2);
  });

  it('does NOT render the removed editable LTV field', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    // LTV is Debt-owned; it must not appear as an editable field here.
    expect(screen.queryByText('LTV')).not.toBeInTheDocument();
  });
});

describe('InvestmentTab — Transaction Timeline', () => {
  it('renders the rail, hold caption and the "Owned by" detail column', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Timeline'));

    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());

    expect(await screen.findByText('Transaction Timeline')).toBeInTheDocument();
    // "X-year hold · <close> → <exit>" caption.
    expect(screen.getByText(/5-year hold/)).toBeInTheDocument();
    // A milestone from the endpoint.
    expect(screen.getAllByText('Renovation').length).toBeGreaterThan(0);
    // Detail table "Owned by" column + a derived owner label.
    expect(screen.getByText('Owned by')).toBeInTheDocument();
    expect(screen.getByText('Investment assumption')).toBeInTheDocument();
    expect(screen.getByText('Linked from Debt')).toBeInTheDocument();
  });
});

describe('InvestmentTab — canonical save path (field_overrides, not local store)', () => {
  /** Open the Purchase Price editor and type `value` into it. */
  function editPurchasePrice(value: string): HTMLInputElement {
    fireEvent.click(screen.getByText('$34,000,000'));
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    fireEvent.change(input, { target: { value } });
    return input;
  }

  // ── FON-74 / Slice A adoption ────────────────────────────────────────
  // Slice A's server gate 422s `override_note_required` on any engine-input
  // key written as a bare scalar. Every Investment assumption is an engine
  // input, so every Investment save writes the `{value, note}` envelope —
  // without this the whole Deal Summary is un-saveable.
  it('editing Purchase Price PATCHes field_overrides as {value, note}', async () => {
    render(<InvestmentTab />);
    editPurchasePrice('35000000');
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: 'Broker confirmed the revised bid.' },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.purchase_price).toEqual({
      value: 35_000_000,
      note: 'Broker confirmed the revised bid.',
    });
  });

  it('refuses the save — and writes nothing — when no justification is typed', async () => {
    render(<InvestmentTab />);
    editPurchasePrice('35000000');
    fireEvent.click(screen.getByLabelText('Save'));

    await new Promise((r) => setTimeout(r, 0));
    expect(updateSpy).not.toHaveBeenCalled();
    // Still in edit mode with the draft intact — nothing was discarded.
    expect((document.querySelector('input[type="number"]') as HTMLInputElement).value).toBe('35000000');
  });

  it('a no-op edit short-circuits BEFORE the note check — no request, no demand', async () => {
    render(<InvestmentTab />);
    // Re-save the same number. `isNoOpEdit` runs first, so Save exits quietly
    // rather than asking the analyst to justify a change that isn't one.
    editPurchasePrice('34000000');
    fireEvent.click(screen.getByLabelText('Save'));

    await new Promise((r) => setTimeout(r, 0));
    expect(updateSpy).not.toHaveBeenCalled();
    // Edit mode closed (the no-op path), so there is no editor left open.
    expect(document.querySelector('input[type="number"]')).toBeNull();
  });

  it('the Acquisition Date is an engine input, so it carries a note too', async () => {
    render(<InvestmentTab />);
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('3/31/2027'));
    const input = document.querySelector('input[type="date"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '2027-06-30' } });
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: 'PSA amended — close pushed to the quarter end.' },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.acquisition_close_date).toEqual({
      value: '2027-06-30',
      note: 'PSA amended — close pushed to the quarter end.',
    });
  });

  // The room count is a deal COLUMN, not an override of a sourced value, so
  // `requiresNote` answers false and the editor must not grow a note row.
  it('the Keys column override asks for no justification', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByLabelText('Override room count'));
    expect(screen.queryByLabelText('Override justification')).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-44 §1 — Disposition Cost % is no longer hidden
//
// Sam (9/11): "The underlying 2.00% is currently hidden; only the calculated
// dollar amount is displayed. Please expose Disposition Cost % = 2.00% as an
// editable Investment assumption." The rate was always a real, routed engine
// assumption; it had no row.
// ─────────────────────────────────────────────────────────────────────────

/** The value cell of the Deal Summary row carrying this label. */
function rowValueCell(label: string): HTMLElement {
  const labelEl = screen.getAllByText(label)[0];
  const row = labelEl.closest('div') as HTMLElement;
  return row.lastElementChild as HTMLElement;
}
/** The dot of the Deal Summary row carrying this label. */
function rowDot(label: string): HTMLElement {
  const labelEl = screen.getAllByText(label)[0];
  const row = labelEl.closest('div') as HTMLElement;
  return row.querySelector('[role="img"]') as HTMLElement;
}

describe('InvestmentTab — Disposition Cost % (FON-44 §1)', () => {
  it('renders the rate from the returns engine trace input', () => {
    mockTraces = {
      returns: {
        selling_costs: {
          value: 520_000,
          inputs: [
            { name: 'gross_sale_price', value: 52_000_000 },
            { name: 'selling_costs_pct', value: 0.02, assumption_key: 'selling_costs_pct' },
          ],
        },
      },
    };
    render(<InvestmentTab />);
    expect(rowValueCell('Disposition Cost %').textContent).toContain('2.00%');
  });

  it('falls back to the ratio of two engine outputs, never a hard-coded 2%', () => {
    // No provenance sidecar. selling_costs 520,000 ÷ gross_sale 52,000,000.
    render(<InvestmentTab />);
    expect(rowValueCell('Disposition Cost %').textContent).toContain('1.00%');
  });

  it('badges as an analyst assumption and is editable, saving selling_costs_pct', async () => {
    render(<InvestmentTab />);
    expect(rowDot('Disposition Cost %').getAttribute('aria-label')).toBe('Assumption');

    fireEvent.click(screen.getByText('1.00%'));
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '2.5' } });
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: 'Miami brokerage quoted 2.5% all-in.' },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.selling_costs_pct).toEqual({
      value: 0.025,
      note: 'Miami brokerage quoted 2.5% all-in.',
    });
  });

  it('says on the row that this is the EXIT-side cost, not the acquisition one', () => {
    // Sam asked explicitly that the two 2.00%s not read as the same number.
    render(<InvestmentTab />);
    expect(screen.getByText(/Blended cost of SELLING the asset/i).textContent)
      .toMatch(/Separate from the acquisition Closing Costs %/i);
    // Both rows exist, and they are different rows.
    expect(screen.getByText('Closing Costs %')).toBeInTheDocument();
    expect(screen.getByText('Disposition Cost %')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-44 §2 — three lineage rows badged the wrong owner
// ─────────────────────────────────────────────────────────────────────────

describe('InvestmentTab — Sources & Uses lineage (FON-44 §2)', () => {
  /** The dot of the Sources & Uses line carrying this label. */
  function suDot(label: string): HTMLElement {
    const row = screen.getAllByText(label)[0].closest('div')!.parentElement as HTMLElement;
    return row.querySelector('[role="img"]') as HTMLElement;
  }

  it('Working Capital badges as an assumption, not Calculated', () => {
    // Sam: "shown with the gray Calculated indicator, but Atlas confirms it is
    // stored as an analyst `working_capital` assumption."
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    expect(suDot('Working Capital').getAttribute('aria-label')).toBe('Assumption');
    expect(screen.getAllByText('Analyst assumption, not a calculated line').length).toBeGreaterThan(0);
  });

  it('a line the worker sources from a document badges document_sourced', () => {
    mockSources = { purchase_price: 'om_broker' }; // a `grounded` registry source
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    expect(suDot('Purchase Price').getAttribute('aria-label')).toBe('Document sourced');
  });

  it('Closing Costs stays Calculated — the worker calls it a calculation', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    expect(suDot('Closing Costs').getAttribute('aria-label')).toBe('Calculated');
  });

  it('Renovation badges as an assumption only while no contingency is folded in', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    expect(suDot('Renovation').getAttribute('aria-label')).toBe('Assumption');
  });

  it('…and drops back to Calculated once the engine folds one in', () => {
    // `capital._property_line_input`: with a contingency the Renovation line
    // "is no longer the `renovation_budget` assumption — it is this engine's
    // own `renovation_total_usd`." The badge follows the worker.
    const capital = (OUTPUTS as unknown as {
      engines: { capital: { outputs: Record<string, unknown> } };
    }).engines.capital.outputs;
    capital.renovation_contingency_usd = 250_000;
    try {
      render(<InvestmentTab />);
      fireEvent.click(screen.getByText('Sources & Uses'));
      expect(suDot('Renovation').getAttribute('aria-label')).toBe('Calculated');
    } finally {
      delete capital.renovation_contingency_usd;
    }
  });
});

describe('InvestmentTab — the renovation window is an Investment assumption', () => {
  it('Renovation Start shows the resolved DATE but edits the month offset', async () => {
    render(<InvestmentTab />);
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());

    // Blue assumption dot, not the green "linked" one it used to carry.
    await waitFor(() => expect(rowDot('Renovation Start').getAttribute('aria-label')).toBe('Assumption'));
    // The DATE the worker resolved (close 3/31/2027 + 3 months).
    expect(rowValueCell('Renovation Start').textContent).toContain('6/30/2027');
    expect(screen.getByText(/Months after the acquisition close \(3\/31\/2027\)/)).toBeInTheDocument();

    // The editor opens on the OFFSET, in months.
    fireEvent.click(screen.getByText('6/30/2027'));
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input.value).toBe('3');
    fireEvent.change(input, { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: 'Permit approval pushed the start two quarters.' },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.renovation_start_offset_months).toEqual({
      value: 6,
      note: 'Permit approval pushed the start two quarters.',
    });
    // Timeline-only key — it refetches the timeline rather than re-running.
    expect(engineRunSpy).not.toHaveBeenCalled();
  });

  it('Duration is editable and saves renovation_duration_months', async () => {
    render(<InvestmentTab />);
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());
    await waitFor(() => expect(rowDot('Duration').getAttribute('aria-label')).toBe('Assumption'));

    fireEvent.click(screen.getAllByText('12 months')[0]);
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '18' } });
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: 'GC schedule extended to 18 months.' },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.renovation_duration_months).toEqual({
      value: 18,
      note: 'GC schedule extended to 18 months.',
    });
  });
});

describe('InvestmentTab — Hotel Purchase is linked, not calculated', () => {
  it('reads the worker basis and names the Deal Summary as its owner', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Timeline'));
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());
    expect(await screen.findByText('Linked from Deal Summary')).toBeInTheDocument();
    // Anything genuinely arithmetic on that date stays Calculated — Sam said so
    // about the Exit explicitly, and Senior Loan Maturity is the same shape.
    expect(screen.getAllByText('Calculated').length).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-44 §3 — the two PIPs are different buckets, and say so
// ─────────────────────────────────────────────────────────────────────────

describe('InvestmentTab — Initial Renovation / PIP vs the hold-period plan', () => {
  it('states that the initial renovation is already in Sources & Uses, once', () => {
    render(<InvestmentTab />);
    expect(screen.getByText('Initial Renovation / PIP')).toBeInTheDocument();
    expect(screen.getByText('Day-one capital, funded at close')).toBeInTheDocument();
    expect(
      screen.getByText(/Already counted once in Sources & Uses, as the Renovation use line/i).textContent,
    ).toMatch(/not a duplicate of this one/i);
  });
});

// ── Phase 4.4 — the bare dash learns to say why ──────────────────────────
// Ongoing Capex → FF&E Reserve is the one Investment dash attributable to a
// single assumption key (`ffe_reserve_pct`), so it reads the worker's
// `assumption_sources.reasons` entry and carries it on hover. Everything else
// on the tab is left exactly as it was.
describe('InvestmentTab — the FF&E Reserve dash carries its refusal code', () => {
  /** The value cell of the section row with this label. */
  function valueCell(label: string): HTMLElement {
    const labelEl = screen.getAllByText(label)[0];
    const row = labelEl.closest('div') as HTMLElement; // dot · label · link | value
    return row.lastElementChild as HTMLElement;
  }

  it('renders the reason from the worker code, still showing the em dash', () => {
    mockReasons = { ffe_reserve_pct: 'no_document' };
    render(<InvestmentTab />);
    const cell = valueCell('FF&E Reserve');
    const refusal = cell.querySelector('[data-refused]') as HTMLElement;
    expect(refusal).toBeTruthy();
    expect(refusal.getAttribute('data-refused')).toBe('no_document');
    expect(refusal.getAttribute('aria-label')).toBe(REASONS.no_document.label);
    expect(cell.textContent).toBe('—');
  });

  it('with the code ABSENT renders a bare dash — no wrapper, no tooltip', () => {
    mockReasons = {}; // every worker build today
    render(<InvestmentTab />);
    const cell = valueCell('FF&E Reserve');
    expect(cell.querySelector('[data-refused]')).toBeNull();
    expect(cell.textContent).toBe('—');
  });

  it('leaves the unwired Ongoing Capex dashes exactly as they were', () => {
    mockReasons = { ffe_reserve_pct: 'no_source' };
    render(<InvestmentTab />);
    for (const label of ['ROI Projects', 'Other Recurring Capex', 'Transfer Tax']) {
      const cell = valueCell(label);
      expect(cell.querySelector('[data-refused]')).toBeNull();
      expect(cell.textContent).toBe('—');
    }
  });
});


// ─────────────────────────────────────────────────────────────────────────
// Sub-tab routing convention (FON-59 #4 / FON-61 §3)
//
// Every sub-tab is now a URL slug on the shared `useSubTab` hook, so a deep
// link lands where it says, the back button works, and `setSub` keeps every
// other query param (`doc`, `focus`, `reviewField`) intact.
// ─────────────────────────────────────────────────────────────────────────

describe('InvestmentTab — `?tab=investment&sub=<slug>` routing', () => {
  const tabEl = (name: string) => screen.getByRole('tab', { name });

  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
    nav.replace.mockClear();
  });

  // FON-66 (Sam, 09-11): "The → Investment link should ideally deep-link to
  // Investment → Sources & Uses, where the initial equity requirement lives."
  it('lands on Sources & Uses on ?sub=sources-and-uses (FON-66)', () => {
    nav.params = new URLSearchParams('tab=investment&sub=sources-and-uses');
    render(<InvestmentTab />);
    expect(tabEl('Sources & Uses')).toHaveAttribute('aria-selected', 'true');
    expect(tabEl('Deal Summary')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByText(/Sources & Uses/)).toBeInTheDocument();
  });

  it('opens Timeline on ?sub=timeline', () => {
    nav.params = new URLSearchParams('tab=investment&sub=timeline');
    render(<InvestmentTab />);
    expect(tabEl('Timeline')).toHaveAttribute('aria-selected', 'true');
  });

  it('falls back to Deal Summary on an unknown sub value', () => {
    nav.params = new URLSearchParams('tab=investment&sub=not-a-sub-tab');
    render(<InvestmentTab />);
    expect(tabEl('Deal Summary')).toHaveAttribute('aria-selected', 'true');
  });

  it('follows a param change while already mounted', () => {
    nav.params = new URLSearchParams('tab=investment&sub=sources-and-uses');
    const { rerender } = render(<InvestmentTab />);
    expect(tabEl('Sources & Uses')).toHaveAttribute('aria-selected', 'true');

    nav.params = new URLSearchParams('tab=investment&sub=timeline');
    rerender(<InvestmentTab />);
    expect(tabEl('Timeline')).toHaveAttribute('aria-selected', 'true');
  });

  it('setSub writes sub= and preserves doc / focus / reviewField', () => {
    nav.params = new URLSearchParams('tab=investment&doc=doc-9&focus=equity&reviewField=noi_usd');
    render(<InvestmentTab />);
    fireEvent.click(tabEl('Sources & Uses'));

    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const written = new URLSearchParams(url.split('?')[1]);
    expect(written.get('sub')).toBe('sources-and-uses');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('equity');
    expect(written.get('reviewField')).toBe('noi_usd');
  });
});

// ── Sam, 2026-09-15: "base year / acquisition date ... not propagating" ──
// The close date is an engine input: `revenue.py::projection_start_year` reads
// it, and Financials → Projections renders the Base year from
// `revenue.projection_start_year` — an ENGINE OUTPUT. The save used to refetch
// the deal and the timeline only, so the Base year could never move until
// something else happened to re-run the model.
describe('InvestmentTab — saving the acquisition date re-runs the model', () => {
  it('schedules a run so the projection calendar can follow the new close date', async () => {
    vi.useFakeTimers();
    try {
      render(<InvestmentTab />);
      await vi.waitFor(() => expect(timelineSpy).toHaveBeenCalled());

      const cell = rowValueCell('Acquisition Date');
      fireEvent.click(cell.querySelector('span[style*="underline"]') ?? cell);
      const input = document.querySelector('input[type="date"]') as HTMLInputElement;
      fireEvent.change(input, { target: { value: '2028-03-31' } });
      fireEvent.change(screen.getByLabelText(/justification/i), {
        target: { value: 'Closing pushed to Q1 2028.' },
      });
      fireEvent.click(screen.getByLabelText('Save'));

      await vi.waitFor(() => expect(updateSpy).toHaveBeenCalled());
      const [, body] = updateSpy.mock.calls[0] as unknown as [
        string, { field_overrides: Record<string, unknown> },
      ];
      expect(body.field_overrides.acquisition_close_date).toEqual({
        value: '2028-03-31',
        note: 'Closing pushed to Q1 2028.',
      });

      // The engine run is debounced, exactly as the other assumption saves are.
      expect(engineRunSpy).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1300);
      expect(engineRunSpy).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
