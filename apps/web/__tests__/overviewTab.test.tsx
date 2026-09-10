/**
 * Overview tab — canonical v3 rebuild (design/canonical/Overview Tab v3.dc.html).
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED RENDER — the 5 deal-type-aware KPI tiles, the Sources &
 *     Uses columns, and the milestone timeline all resolve from a single mocked
 *     worker engine-outputs envelope (the real `getEngineField` runs). No
 *     prototype placeholders.
 *
 *  2. DEAL-TYPE-AWARE SECTIONS — an `acquisition · value-add` deal renders the
 *     Property / Entry Valuation / Renovation / Capitalization / Stabilization /
 *     Exit set; flipping `deal_type` to `development` swaps to the Land / Site,
 *     Development Budget, Construction Financing and Development Timeline set.
 *
 *  3. WHERE THIS CAME FROM — clicking a provenance-bearing row (Purchase Price)
 *     opens the shared `WhereThisCameFrom` popover with the calculation formula.
 *
 *  4. FON-59 PROJECT NAME vs PROPERTY NAME — Project Name (deals.name) renders
 *     directly above Property Name; Property Name never falls back to the
 *     deal name; Override writes `field_overrides['property_overview.name'] =
 *     { value, note }`, flips the row to the assumption state, the popover
 *     cites the original + Restore deletes the key; Rename writes
 *     `api.deals.update({ name })` and never touches property_overview.name.
 *
 * NOTE: authored per the task but NOT run here (vitest is executed centrally).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, TimelineResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        purchase_price: 34_000_000,
        price_per_key: 257_576,
        entry_cap_rate: 0.075,
        total_capital_usd: 43_000_000,
        total_capital_per_key: 325_758,
        equity_amount: 17_000_000,
        debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000, pct: 0.79 },
          { label: 'Closing Costs', amount: 680_000, pct: 0.016 },
          { label: 'Renovation Budget', amount: 4_620_000, pct: 0.107 },
          { label: 'Total Uses', amount: 43_000_000, pct: 1, is_total: true },
        ],
        sources: [
          { label: 'Senior Loan', amount: 26_000_000, pct: 0.6 },
          { label: 'Equity', amount: 17_000_000, pct: 0.4 },
          { label: 'Total Sources', amount: 43_000_000, pct: 1, is_total: true },
        ],
      },
      inputs: {}, error: null, runtime_ms: 10, started_at: null, completed_at: null, run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: {
        gross_sale_price: 52_000_000,
        exit_cap_rate: 0.07,
        terminal_noi: 3_640_000,
        selling_costs: 520_000,
        hold_years: 5,
        levered_irr: 0.198,
      },
      inputs: {}, error: null, runtime_ms: 9, started_at: null, completed_at: null, run_id: 'run-1',
    },
    debt: {
      deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
      outputs: { loan_amount: 26_000_000, interest_rate: 0.0725 },
      inputs: {}, error: null, runtime_ms: 4, started_at: null, completed_at: null, run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

const TIMELINE = {
  deal_id: 'deal-uuid-1',
  close_date: '2027-03-31',
  exit_date: '2032-03-31',
  stabilization_date: '2029-06-30',
  events: [
    { event: 'Hotel Purchase', start: '2027-03-31', duration_months: 0, finish: '2027-03-31', basis: 'derived' },
    { event: 'Renovation', start: '2027-06-30', duration_months: 12, finish: '2028-06-30', basis: 'assumption' },
    { event: 'Stabilized (FTM NOI, Value)', start: '2029-06-30', duration_months: 0, finish: '2029-06-30', basis: 'derived' },
    { event: 'Disposition', start: '2032-03-31', duration_months: 0, finish: '2032-03-31', basis: 'derived' },
  ],
} as unknown as TimelineResponse;

// Mutable deal so tests can flip the deal type between renders.
const mockDealRef: { deal: Record<string, unknown> } = {
  deal: {
    id: 'deal-uuid-1', keys: 132, deal_type: 'acquisition', return_profile: 'value-add',
    positioning: 'default', brand: 'Kimpton Hotels & Restaurants', field_overrides: {},
  },
};

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDealRef.deal, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

// api surface — serve the timeline + a configurable market overview; spy the PATCH.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
const timelineSpy = vi.fn(async () => TIMELINE);
const overviewRef: { value: Record<string, unknown> } = { value: {} };
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: { ...actual.api.engines, timeline: (...a: unknown[]) => timelineSpy(...(a as [])) },
      market: { ...actual.api.market, overview: async () => overviewRef.value },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import OverviewTab from '@/components/project/OverviewTab';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  timelineSpy.mockClear();
  engineRunSpy.mockClear();
  overviewRef.value = {};
  mockDealRef.deal = {
    id: 'deal-uuid-1', keys: 132, deal_type: 'acquisition', return_profile: 'value-add',
    positioning: 'default', brand: 'Kimpton Hotels & Restaurants', field_overrides: {},
  };
});

// ─── FON-59 helpers ──────────────────────────────────────────────────────
const EXTRACTED = "Kimpton Angler's South Beach";
const OVERRIDE_KEY = 'property_overview.name';
const OVERVIEW_EXTRACTED = {
  property_name: EXTRACTED,
  property_name_source: 'document',
  property_name_original: { value: EXTRACTED, doc_name: 'Anglers OM.pdf', page: 1 },
};

/** The Overview row element for a General-Information label (label span → left span → row div).
 *  Scoped outside the popover, whose header repeats the label text. */
function rowFor(label: string): HTMLElement {
  const span = screen.getAllByText(label).find((el) => !el.closest('[role="dialog"]'));
  if (!span) throw new Error(`no Overview row labelled ${label}`);
  return span.parentElement!.parentElement!;
}
/** The value cell text of a row (the right-hand span's last child). */
function rowValue(label: string): string {
  const right = rowFor(label).lastElementChild as HTMLElement;
  return (right.lastElementChild as HTMLElement).textContent ?? '';
}
/** The provenance dot's accessible label on a row (null when no dot). */
function rowDotLabel(label: string): string | null {
  const dot = (rowFor(label).lastElementChild as HTMLElement).querySelector('[aria-label]');
  return dot?.getAttribute('aria-label') ?? null;
}

describe('OverviewTab — engine-sourced KPI tiles (value-add)', () => {
  it('renders the 5 deal-type-aware KPI tiles from the mocked engine outputs', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);

    expect(screen.getByText('Total Capitalization')).toBeInTheDocument();
    expect(screen.getByText('Renovation')).toBeInTheDocument();
    expect(screen.getByText('Levered IRR')).toBeInTheDocument();
    // KPI labels also appear elsewhere → assert at least one match.
    expect(screen.getAllByText('Purchase Price').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Stabilized NOI').length).toBeGreaterThan(0);

    // Values are the $M engine figures, not prototype placeholders.
    expect(screen.getByText('$34.00M')).toBeInTheDocument(); // purchase price
    expect(screen.getByText('$43.00M')).toBeInTheDocument(); // total capitalization
    expect(screen.getByText('$4.62M')).toBeInTheDocument();  // renovation
    expect(screen.getByText('$3.64M')).toBeInTheDocument();  // stabilized NOI
    expect(screen.getAllByText('19.8%').length).toBeGreaterThan(0); // levered IRR (shown in >1 place)
  });
});

describe('OverviewTab — value-add section set + benchmark strip', () => {
  it('renders the acquisition/value-add sections, S&U columns and the return benchmark', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);

    for (const title of ['Property', 'Entry Valuation', 'Renovation / CapEx', 'Capitalization', 'Stabilization', 'Exit', 'Transaction Sources & Uses']) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    // Development-only sections are absent for a value-add deal.
    expect(screen.queryByText('Development Budget')).not.toBeInTheDocument();
    expect(screen.queryByText('Construction Financing')).not.toBeInTheDocument();

    // Sources & Uses — the "equity is the plug" note + Amount / Key / % headers.
    expect(screen.getByText(/Equity is the calculated plug/i)).toBeInTheDocument();
    expect(screen.getAllByText('/ Key').length).toBe(2);

    // Return benchmark strip — profile target (value-add → 12-18%).
    expect(screen.getByText('Return benchmark')).toBeInTheDocument();
    expect(screen.getByText('Target levered IRR')).toBeInTheDocument();
    expect(screen.getByText('12-18%')).toBeInTheDocument();
  });
});

describe('OverviewTab — milestone timeline (GET /engines/timeline)', () => {
  it('fetches and renders the transaction timeline rail', async () => {
    render(<OverviewTab projectId="deal-uuid-1" />);

    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());

    expect(await screen.findByText('Transaction Timeline')).toBeInTheDocument();
    // A milestone from the endpoint + its "N-year hold" caption.
    expect(screen.getByText('Hotel Purchase')).toBeInTheDocument();
    expect(screen.getByText(/5-year hold/)).toBeInTheDocument();
  });
});

describe('OverviewTab — "Where this came from" popover', () => {
  it('opens the anchored provenance popover with the calculation formula on a calc row', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);

    // The Entry Valuation "Purchase Price" row shows the full-dollar value.
    // It also appears in the Transaction Sources & Uses table, which renders
    // AFTER Entry Valuation (see section order above), so the first match in
    // DOM order is the Entry Valuation cell this test means to open.
    const [cell] = screen.getAllByText('$34,000,000');
    fireEvent.click(cell);

    // The shared WhereThisCameFrom popover (role=dialog, aria-labelled by field).
    expect(screen.getByRole('dialog', { name: /Where Purchase Price came from/i })).toBeInTheDocument();
    // Its calculation section renders the human formula.
    expect(screen.getByText('Entry NOI ÷ Entry Cap Rate')).toBeInTheDocument();
  });
});

describe('OverviewTab — deal-type-aware (development)', () => {
  it('swaps to the development section set + KPI tiles when deal_type is development', () => {
    mockDealRef.deal = { ...mockDealRef.deal, deal_type: 'development' };
    render(<OverviewTab projectId="deal-uuid-1" />);

    for (const title of ['Project', 'Land / Site Acquisition', 'Development Budget', 'Construction Financing', 'Opening & Stabilization', 'Development Timeline']) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    // Value-add-only sections are gone.
    expect(screen.queryByText('Renovation / CapEx')).not.toBeInTheDocument();

    // Development KPI tiles.
    expect(screen.getByText('Total Dev. Cost')).toBeInTheDocument();
    expect(screen.getByText('Cost / Key')).toBeInTheDocument();
    expect(screen.getByText('$43.00M')).toBeInTheDocument(); // total development cost
  });
});

describe('OverviewTab — FON-59 Project Name vs Property Name', () => {
  it('renders Project Name (deal.name) directly above Property Name, which never falls back to the deal name', async () => {
    mockDealRef.deal = { ...mockDealRef.deal, name: 'Project Unicorn' };
    overviewRef.value = {}; // nothing extracted yet
    render(<OverviewTab projectId="deal-uuid-1" />);
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());

    const projectLabel = screen.getByText('Project Name');
    const propertyLabel = screen.getByText('Property Name');
    // Project Name precedes Property Name in DOM order (directly above it).
    expect(projectLabel.compareDocumentPosition(propertyLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rowFor('Project Name').nextElementSibling).toBe(rowFor('Property Name'));

    expect(rowValue('Project Name')).toBe('Project Unicorn');
    // The deal name appears exactly once — never as the Property Name.
    expect(screen.getAllByText('Project Unicorn')).toHaveLength(1);
    expect(rowValue('Property Name')).toBe('—');
  });

  it('shows both rows on a development deal too (Property Name "—" when none extracted)', async () => {
    mockDealRef.deal = { ...mockDealRef.deal, deal_type: 'development', name: 'Project Gulch' };
    render(<OverviewTab projectId="deal-uuid-1" />);
    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());
    expect(rowValue('Project Name')).toBe('Project Gulch');
    expect(rowValue('Property Name')).toBe('—');
    expect(screen.getAllByText('Project Gulch')).toHaveLength(1);
  });

  it('overrides Property Name via field_overrides, flips the row to the assumption state, cites the original and restores', async () => {
    mockDealRef.deal = { ...mockDealRef.deal, name: 'Project Unicorn' };
    overviewRef.value = OVERVIEW_EXTRACTED;
    const { rerender } = render(<OverviewTab projectId="deal-uuid-1" />);
    expect(await screen.findByText(EXTRACTED)).toBeInTheDocument();
    expect(rowDotLabel('Property Name')).toMatch(/document/i);

    // Open the popover on the extracted value → Override → type → Save value.
    fireEvent.click(screen.getByText(EXTRACTED));
    let dialog = screen.getByRole('dialog', { name: /Where Property Name came from/i });
    expect(within(dialog).getByText('Document sourced')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Override' }));
    const input = within(dialog).getByRole('textbox', { name: /Property Name value/i });
    fireEvent.change(input, { target: { value: 'The Anglers Hotel' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save value' }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy).toHaveBeenCalledWith('deal-uuid-1', {
      field_overrides: { [OVERRIDE_KEY]: { value: 'The Anglers Hotel', note: expect.any(String) } },
    });
    // The override never writes the project name.
    expect(JSON.stringify(updateSpy.mock.calls)).not.toContain('"name"');

    // Simulate the deal refresh + worker read-back (override wins, original kept).
    mockDealRef.deal = {
      ...mockDealRef.deal,
      field_overrides: { [OVERRIDE_KEY]: { value: 'The Anglers Hotel', note: 'Analyst override — Overview · Property Name' } },
    };
    overviewRef.value = { ...OVERVIEW_EXTRACTED, property_name: 'The Anglers Hotel', property_name_source: 'analyst_override' };
    rerender(<OverviewTab projectId="deal-uuid-1" />);

    // Row flips: override value, blue assumption dot; Project Name untouched.
    expect(rowValue('Property Name')).toBe('The Anglers Hotel');
    expect(rowDotLabel('Property Name')).toMatch(/assumption/i);
    expect(rowValue('Project Name')).toBe('Project Unicorn');

    // Popover (still open, resolved live) shows Overridden + "Original: <extracted> · <doc> p.<n>" + Restore.
    dialog = screen.getByRole('dialog', { name: /Where Property Name came from/i });
    expect(within(dialog).getByText('Overridden')).toBeInTheDocument();
    expect(within(dialog).getByText('Original')).toBeInTheDocument();
    expect(within(dialog).getByText(`${EXTRACTED} · Anglers OM.pdf p.1`)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Restore sourced value' }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(2));
    // Restore deletes the key (the extracted value comes back from the worker).
    expect(updateSpy).toHaveBeenLastCalledWith('deal-uuid-1', { field_overrides: {} });
  });

  it('renames the project via api.deals.update({ name }) and never writes property_overview.name', async () => {
    mockDealRef.deal = { ...mockDealRef.deal, name: 'Project Unicorn' };
    overviewRef.value = OVERVIEW_EXTRACTED;
    render(<OverviewTab projectId="deal-uuid-1" />);
    expect(await screen.findByText(EXTRACTED)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Project Unicorn'));
    const dialog = screen.getByRole('dialog', { name: /Where Project Name came from/i });
    // An analyst input, not a document row — no Override / View source here.
    expect(within(dialog).getByText('Assumption')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Override' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /View source/ })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Rename' }));
    const input = within(dialog).getByRole('textbox', { name: /Project Name value/i });
    fireEvent.change(input, { target: { value: 'Project Pelican' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save name' }));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy).toHaveBeenCalledWith('deal-uuid-1', { name: 'Project Pelican' });
    expect(JSON.stringify(updateSpy.mock.calls)).not.toContain(OVERRIDE_KEY);
    expect(JSON.stringify(updateSpy.mock.calls)).not.toContain('field_overrides');
    // Property Name is untouched by the rename.
    expect(rowValue('Property Name')).toBe(EXTRACTED);
    // No model re-run for a rename — names feed no engine.
    expect(engineRunSpy).not.toHaveBeenCalled();
  });
});
