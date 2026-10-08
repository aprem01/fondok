/**
 * Cash Flow tab — canonical ``cash_flow`` engine view (Move 2, Stage 2b).
 *
 * The worker now emits a reconciled ``cash_flow`` statement; the tab reads it
 * straight through instead of re-assembling five engines in the browser
 * (the deleted ``buildCashFlowFromWorker``). This suite locks two contracts:
 *
 *  1. The composed statement FOOTS — each section's component (linked) rows
 *     sum, per period column, to the canonical returns arrays the worker
 *     reconciled to; the summary bridge foots property + financing = equity.
 *  2. The FALLBACK renders — a deal whose run predates the cash_flow engine
 *     (no ``cash_flow`` key) shows the "No cash flow output yet" placeholder,
 *     never an empty tab or a crash.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import type { EngineOutputsResponse, CashFlowStatementOutput } from '@/lib/api';
import { getEngineField } from '@/lib/hooks/useEngineOutputs';
import {
  buildSummary,
  cumulativeLevered,
  hasCashFlowStatement,
  periodHeaders,
  rowState,
  sectionFoots,
  slugifyLabel,
} from '@/components/project/cashFlowStatement';

// ── A small, self-consistent 2-year statement. Component (linked) rows foot
// exactly to the canonical unlevered/levered arrays, mirroring the worker's
// reconciliation guard.
const CF: CashFlowStatementOutput = {
  deal_id: 'deal-1',
  hold_years: 2,
  unlevered: [
    { label: 'Acquisition Uses at Close', values: [-1000, null, null], kind: 'linked' },
    { label: 'Net Operating Income', values: [null, 100, 120], kind: 'linked' },
    { label: 'FF&E Reserve', values: [null, -10, -12], kind: 'linked' },
    { label: 'Gross Sale Proceeds', values: [null, null, 1500], kind: 'linked' },
    { label: 'Selling & Disposition Costs', values: [null, null, -60], kind: 'linked' },
    { label: 'Unlevered Cash Flow', values: [-1000, 90, 1548], kind: 'calc' },
  ],
  levered: [
    { label: 'Unlevered Cash Flow', values: [-1000, 90, 1548], kind: 'linked' },
    { label: 'Debt Proceeds', values: [600, null, null], kind: 'linked' },
    { label: 'Interest Expense', values: [null, -30, -30], kind: 'linked' },
    { label: 'Principal Amortization', values: [null, -20, -20], kind: 'linked' },
    { label: 'Exit Debt Payoff', values: [null, null, -560], kind: 'linked' },
    { label: 'Net Cash Flow to Equity', values: [-400, 40, 938], kind: 'calc' },
  ],
  distributions: [
    { label: 'LP Distributions', values: [30, 700], kind: 'linked' },
    { label: 'GP Distributions', values: [10, 238], kind: 'linked' },
    { label: 'Total Distributions', values: [40, 938], kind: 'calc' },
  ],
  unlevered_cash_flow: [-1000, 90, 1548],
  levered_cash_flow: [-400, 40, 938],
  provenance: {
    // A worker-supplied state overrides the kind-based fallback.
    'unlevered.net_operating_income': {
      value: 220,
      inputs: [],
      state: 'document_sourced',
    },
  },
};

function envelope(cf: CashFlowStatementOutput | null): EngineOutputsResponse {
  const engines = {} as EngineOutputsResponse['engines'];
  if (cf) {
    engines.cash_flow = {
      deal_id: 'deal-1',
      engine: 'cash_flow',
      status: 'complete',
      summary: '',
      outputs: cf as unknown as Record<string, unknown>,
      inputs: null,
      error: null,
      runtime_ms: 1,
      started_at: null,
      completed_at: null,
      run_id: null,
    };
  }
  return { deal_id: 'deal-1', engines };
}

describe('cashFlowStatement — pure view-model helpers', () => {
  it('slugifyLabel matches the worker provenance key derivation', () => {
    expect(slugifyLabel('Net Operating Income')).toBe('net_operating_income');
    expect(slugifyLabel('Selling & Disposition Costs')).toBe('selling_disposition_costs');
    expect(slugifyLabel('Net Cash Flow to Equity')).toBe('net_cash_flow_to_equity');
  });

  it('each section foots to the canonical returns series', () => {
    expect(sectionFoots(CF, 'unlevered')).toBe(true);
    expect(sectionFoots(CF, 'levered')).toBe(true);
    expect(sectionFoots(CF, 'distributions')).toBe(true);
  });

  it('detects a section that does NOT foot (guards against silent drift)', () => {
    const broken: CashFlowStatementOutput = {
      ...CF,
      levered_cash_flow: [-400, 40, 999], // exit column no longer ties out
    };
    expect(sectionFoots(broken, 'levered')).toBe(false);
  });

  it('builds the summary bridge and KPIs from the canonical arrays', () => {
    const s = buildSummary(CF);
    expect(s.foots).toBe(true);

    const kpi = (label: string) => s.kpis.find((k) => k.label === label)?.value;
    expect(kpi('Total Equity Invested')).toBe(400); // -levered[0]
    expect(kpi('Total Cash Returned to Equity')).toBe(978); // Σ levered[1..]
    expect(kpi('Net Exit Proceeds')).toBe(880); // gross - selling - payoff
    expect(kpi('Operating Cash Flow to Equity')).toBe(98); // total - refi - exit
    // No refinance line in this deal → the KPI reads null (renders em-dash).
    expect(kpi('Net Refinance Proceeds')).toBeNull();

    // Bridge foots per column: unlevered + financing === net-to-equity.
    const [unlev, financing, equity] = s.bridge;
    for (let i = 0; i < equity.values.length; i++) {
      expect(unlev.values[i] + financing.values[i]).toBeCloseTo(equity.values[i], 6);
    }
  });

  it('labels the exit period column and includes close', () => {
    expect(periodHeaders(CF)).toEqual(['Close', 'Year 1', 'Year 2 / Exit']);
  });

  it('rowState prefers the worker provenance state, else maps the row kind', () => {
    const noi = CF.unlevered[1];
    const acq = CF.unlevered[0];
    const bottom = CF.unlevered[5];
    expect(rowState(CF, 'unlevered', noi)).toBe('document_sourced'); // from provenance
    expect(rowState(CF, 'unlevered', acq)).toBe('linked'); // kind fallback
    expect(rowState(CF, 'unlevered', bottom)).toBe('calculated'); // kind fallback
  });

  it('hasCashFlowStatement gates the fallback', () => {
    expect(hasCashFlowStatement(CF)).toBe(true);
    expect(hasCashFlowStatement(null)).toBe(false);
    expect(hasCashFlowStatement(undefined)).toBe(false);
    // A legacy run with no reconciled series is treated as absent.
    expect(
      hasCashFlowStatement({ ...CF, levered_cash_flow: [] } as CashFlowStatementOutput),
    ).toBe(false);
  });

  it('getEngineField reads the statement from a cash_flow envelope, undefined when absent', () => {
    expect(getEngineField<CashFlowStatementOutput>(envelope(CF), 'cash_flow')).toBe(CF);
    expect(getEngineField<CashFlowStatementOutput>(envelope(null), 'cash_flow')).toBeUndefined();
  });
});

// ─────────────────── Component render (populated + fallback) ───────────────

const hoisted = vi.hoisted(() => ({ outputs: null as EngineOutputsResponse | null }));

// Mutable routing state (FON-59 #4) - `params` is a REAL URLSearchParams, what
// Next's ReadonlyURLSearchParams behaves like, so `useSubTab`'s toString()
// round-trip is exercised rather than stubbed.
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-1',
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
}));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: hoisted.outputs,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(),
    }),
  };
});

import CashFlowTab from '@/components/project/CashFlowTab';

describe('CashFlowTab — render', () => {
  beforeEach(() => cleanup());

  it('renders the canonical statement (Summary + Levered) from the cash_flow output', () => {
    hoisted.outputs = envelope(CF);
    render(<CashFlowTab />);

    // Summary sub-tab (default): KPI cards + bridge from the worker output.
    // Rebuilt tab renders KPI labels in canonical sentence case
    // (KPI_LABEL_CANONICAL) — "Total equity invested", not title case.
    expect(screen.getByText('Total equity invested')).toBeInTheDocument();
    expect(screen.getByText('Cash Flow Bridge')).toBeInTheDocument();
    // Output-only framing per the canonical design.
    expect(screen.getByText('Output only')).toBeInTheDocument();

    // Switch to the levered statement — a line unique to it should appear.
    // Sub-tabs are now the shared SubTabNav (role="tab", not a plain button).
    // The per-tab Data Key legend was removed (mounted once at page level), so
    // the levered statement rendering is asserted via its unique line item.
    fireEvent.click(screen.getByRole('tab', { name: 'Levered / Equity' }));
    expect(screen.getByText('Exit Debt Payoff')).toBeInTheDocument();
  });

  it('renders the Run Model placeholder when no cash_flow output exists', () => {
    hoisted.outputs = envelope(null);
    render(<CashFlowTab />);
    expect(screen.getByText('No cash flow output yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run Model' })).toBeInTheDocument();
    // No statement chrome leaks onto the placeholder.
    expect(screen.queryByText('Cash Flow Bridge')).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Sub-tab routing convention (FON-59 #4 / FON-61 §3)
//
// Every sub-tab is now a URL slug on the shared `useSubTab` hook, so a deep
// link lands where it says, the back button works, and `setSub` keeps every
// other query param (`doc`, `focus`, `reviewField`) intact.
// ─────────────────────────────────────────────────────────────────────────

describe('CashFlowTab — `?tab=cash-flow&sub=<slug>` routing', () => {
  const tabEl = (name: string) => screen.getByRole('tab', { name });

  beforeEach(() => {
    cleanup();
    // The sub-tab nav only exists once there is a statement to show.
    hoisted.outputs = envelope(CF);
    nav.params = new URLSearchParams('');
    nav.replace.mockClear();
    nav.push.mockClear();
  });

  it('opens Levered / Equity on ?sub=levered-equity', () => {
    nav.params = new URLSearchParams('tab=cash-flow&sub=levered-equity');
    render(<CashFlowTab />);
    expect(tabEl('Levered / Equity')).toHaveAttribute('aria-selected', 'true');
    expect(tabEl('Summary')).toHaveAttribute('aria-selected', 'false');
  });

  it('opens Unlevered on ?sub=unlevered', () => {
    nav.params = new URLSearchParams('tab=cash-flow&sub=unlevered');
    render(<CashFlowTab />);
    expect(tabEl('Unlevered')).toHaveAttribute('aria-selected', 'true');
  });

  it('falls back to Summary on an unknown sub value', () => {
    nav.params = new URLSearchParams('tab=cash-flow&sub=not-a-sub-tab');
    render(<CashFlowTab />);
    expect(tabEl('Summary')).toHaveAttribute('aria-selected', 'true');
  });

  it('follows a param change while already mounted', () => {
    nav.params = new URLSearchParams('tab=cash-flow&sub=unlevered');
    const { rerender } = render(<CashFlowTab />);
    expect(tabEl('Unlevered')).toHaveAttribute('aria-selected', 'true');

    nav.params = new URLSearchParams('tab=cash-flow&sub=levered-equity');
    rerender(<CashFlowTab />);
    expect(tabEl('Levered / Equity')).toHaveAttribute('aria-selected', 'true');
  });

  it('setSub writes sub= and preserves doc / focus / reviewField', () => {
    nav.params = new URLSearchParams('tab=cash-flow&doc=doc-9&focus=equity&reviewField=noi_usd');
    render(<CashFlowTab />);
    fireEvent.click(tabEl('Unlevered'));

    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const written = new URLSearchParams(url.split('?')[1]);
    expect(written.get('sub')).toBe('unlevered');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('equity');
    expect(written.get('reviewField')).toBe('noi_usd');
  });

  // FON-67 §1 (Sam, 09-11) — "analysts should be able to trace each component
  // upstream": every banner chip names the sub-tab that holds the figure.
  it('the output-only banner chips carry the upstream sub-tab', () => {
    render(<CashFlowTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Investment →' }));
    expect(nav.push).toHaveBeenLastCalledWith(
      '/projects/deal-1?tab=investment&sub=sources-and-uses',
      { scroll: false },
    );

    fireEvent.click(screen.getByRole('button', { name: 'P&L →' }));
    expect(nav.push).toHaveBeenLastCalledWith(
      '/projects/deal-1?tab=pl&sub=projections',
      { scroll: false },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Debt →' }));
    expect(nav.push).toHaveBeenLastCalledWith(
      '/projects/deal-1?tab=debt&sub=debt-schedule',
      { scroll: false },
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-67 (R-072) — the Summary statement
//
// Sam's tester: "Cash Flow shows only two line items under Cash Flow Bridge;
// I expect P&L, debt, equity distributions, NOI and IRR by year visible
// together." The Summary now leads with the full year-by-year statement read
// from the worker's lines BY LABEL, "—" for any line the engine did not emit,
// the cumulative levered series as the one labelled browser-side sum, and the
// whole-hold IRR / multiple from the returns engine shown ONCE beside it.
// ─────────────────────────────────────────────────────────────────────────

/** The same 2-year deal, carrying the labels ``cash_flow.py`` emits today
 *  ("NOI (before FF&E reserve)") rather than the legacy "Net Operating Income". */
const CF_WORKER: CashFlowStatementOutput = {
  ...CF,
  unlevered: [
    { label: 'Acquisition Uses at Close', values: [-1000, null, null], kind: 'linked' },
    { label: 'NOI (before FF&E reserve)', values: [null, 100, 120], kind: 'linked' },
    { label: 'FF&E Reserve', values: [null, -10, -12], kind: 'linked' },
    { label: 'Gross Sale Proceeds', values: [null, null, 1500], kind: 'linked' },
    { label: 'Selling & Disposition Costs', values: [null, null, -60], kind: 'linked' },
    { label: 'Unlevered Cash Flow', values: [-1000, 90, 1548], kind: 'calc' },
  ],
  provenance: {},
};

function envelopeWithReturns(
  cf: CashFlowStatementOutput,
  returns: Record<string, unknown> | null,
): EngineOutputsResponse {
  const env = envelope(cf);
  if (returns) {
    env.engines.returns = {
      deal_id: 'deal-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: returns,
      inputs: null,
      error: null,
      runtime_ms: 1,
      started_at: null,
      completed_at: null,
      run_id: null,
    };
  }
  return env;
}

/** The sticky label cell of the statement row labelled `label`. (The cell's
 *  accessible NAME also carries its provenance dot — "Linked NOI …" — so the
 *  row is found by its visible label text, not by role + name.) */
function statementRowHeader(card: HTMLElement, label: string): HTMLElement {
  return within(card).getByText(label).closest('[role="rowheader"]') as HTMLElement;
}

/** Text of every data cell on the statement row whose sticky label is `label`. */
function statementRowCells(card: HTMLElement, label: string): string[] {
  const row = statementRowHeader(card, label).parentElement as HTMLElement;
  return within(row).getAllByRole('cell').map((c) => c.textContent ?? '');
}

describe('cumulativeLevered — the one browser-side sum', () => {
  it('runs the canonical levered series forward, null past its end', () => {
    expect(cumulativeLevered(CF, 3)).toEqual([-400, -360, 578]);
    expect(cumulativeLevered(CF, 4)).toEqual([-400, -360, 578, null]);
  });
});

describe('CashFlowTab — Summary statement (FON-67 / R-072)', () => {
  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
  });

  it('renders the engine lines by label, year by year, with — for a line the engine did not emit', () => {
    hoisted.outputs = envelopeWithReturns(CF_WORKER, { levered_irr: 0.198, unlevered_irr: 0.123, equity_multiple: 1.66 });
    render(<CashFlowTab />);
    const card = screen.getByTestId('cash-flow-summary-statement');

    // Columns are the canonical period headers (Close = Year 0).
    expect(within(card).getByRole('columnheader', { name: 'Close' })).toBeInTheDocument();
    expect(within(card).getByRole('columnheader', { name: 'Year 2 / Exit' })).toBeInTheDocument();

    // Property lines, read from `unlevered` by the worker's own label.
    expect(statementRowCells(card, 'NOI (before FF&E reserve)')).toEqual(['—', '$100', '$120']);
    // Canonical display relabel of the worker's "FF&E Reserve" line.
    expect(statementRowCells(card, 'FF&E Reserve / CapEx')).toEqual(['—', '($10)', '($12)']);
    expect(statementRowCells(card, 'Unlevered Cash Flow')).toEqual(['($1,000)', '$90', '$1,548']);
    // A required line the engine did not emit is visible as — in every period.
    expect(statementRowCells(card, 'Deferred Capital Deployed')).toEqual(['—', '—', '—']);
    expect(statementRowHeader(card, 'Deferred Capital Deployed')).toHaveAttribute(
      'title',
      'Not emitted by the cash_flow engine for this deal',
    );

    // Debt lines from `levered`.
    expect(statementRowCells(card, 'Debt Proceeds')).toEqual(['$600', '—', '—']);
    expect(statementRowCells(card, 'Interest Expense')).toEqual(['—', '($30)', '($30)']);
    expect(statementRowCells(card, 'Principal Amortization')).toEqual(['—', '($20)', '($20)']);
    expect(statementRowCells(card, 'Exit Debt Payoff')).toEqual(['—', '—', '($560)']);
    expect(statementRowCells(card, 'Net Cash Flow to Equity')).toEqual(['($400)', '$40', '$938']);
    // Deal-conditional lines (no refinance on this deal) are not shown at all.
    expect(within(card).queryByText('Net refinance cash-out')).toBeNull();
    expect(within(card).queryByText('Refinance / Junior Debt Service')).toBeNull();

    // Distributions are per operating period → shifted right of the close column.
    expect(statementRowCells(card, 'LP Distributions')).toEqual(['—', '$30', '$700']);
    expect(statementRowCells(card, 'GP Distributions')).toEqual(['—', '$10', '$238']);
    expect(statementRowCells(card, 'Total Distributions')).toEqual(['—', '$40', '$938']);

    // The ONE browser-side sum, labelled as such.
    expect(statementRowCells(card, 'Cumulative levered cash flow (calculated)')).toEqual(['($400)', '($360)', '$578']);
  });

  it('shows the whole-hold IRR figures ONCE beside the table, never per year', () => {
    hoisted.outputs = envelopeWithReturns(CF_WORKER, { levered_irr: 0.198, unlevered_irr: 0.123, equity_multiple: 1.66 });
    render(<CashFlowTab />);
    const block = screen.getByTestId('cash-flow-irr-block');

    expect(within(block).getAllByText('Levered IRR')).toHaveLength(1);
    expect(within(block).getByText('19.8%')).toBeInTheDocument();
    expect(within(block).getAllByText('Unlevered IRR')).toHaveLength(1);
    expect(within(block).getByText('12.3%')).toBeInTheDocument();
    expect(within(block).getAllByText('Equity multiple')).toHaveLength(1);
    expect(within(block).getByText('1.66x')).toBeInTheDocument();
    expect(within(block).getByText(/IRR is a whole-hold figure/)).toBeInTheDocument();
    // No IRR row sneaks into the year-by-year grid.
    const card = screen.getByTestId('cash-flow-summary-statement');
    const grid = within(card).getByRole('table');
    expect(within(grid).queryByText(/IRR/)).toBeNull();
  });

  it('reads — for the IRR figures when the returns engine has not run', () => {
    hoisted.outputs = envelopeWithReturns(CF_WORKER, null);
    render(<CashFlowTab />);
    const block = screen.getByTestId('cash-flow-irr-block');
    expect(within(block).getAllByText('—')).toHaveLength(3);
  });

  it('keeps the Cash Flow Bridge card, below the statement', () => {
    hoisted.outputs = envelopeWithReturns(CF_WORKER, null);
    render(<CashFlowTab />);
    const statement = screen.getByTestId('cash-flow-summary-statement');
    const bridge = screen.getByText('Cash Flow Bridge');
    expect(bridge).toBeInTheDocument();
    // eslint-disable-next-line no-bitwise
    expect(statement.compareDocumentPosition(bridge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('still reads the NOI row from a run that carries the legacy "Net Operating Income" label', () => {
    hoisted.outputs = envelopeWithReturns(CF, null);
    render(<CashFlowTab />);
    const card = screen.getByTestId('cash-flow-summary-statement');
    expect(statementRowCells(card, 'Net Operating Income')).toEqual(['—', '$100', '$120']);
    expect(within(card).queryByText('NOI (before FF&E reserve)')).toBeNull();
  });

  it('the Unlevered and Levered sub-tabs are untouched', () => {
    hoisted.outputs = envelopeWithReturns(CF_WORKER, null);
    render(<CashFlowTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Unlevered' }));
    expect(screen.queryByTestId('cash-flow-summary-statement')).toBeNull();
    expect(screen.getByText('Property level · before financing')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Levered / Equity' }));
    expect(screen.getByText('Levered / Equity Cash Flow')).toBeInTheDocument();
    expect(screen.queryByTestId('cash-flow-irr-block')).toBeNull();
  });
});

// ─── FON-63 — the negative-NOI strip rides along on Cash Flow ─────────────
describe('CashFlowTab — negative-NOI strip (FON-63)', () => {
  beforeEach(() => cleanup());
  const withDebt = (debt: Record<string, unknown> | null): EngineOutputsResponse => {
    const env = envelope(CF);
    if (debt) {
      (env.engines as unknown as Record<string, unknown>).debt = {
        deal_id: 'deal-1', engine: 'debt', status: 'complete', summary: '',
        outputs: debt, inputs: null, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: null,
      };
    }
    return env;
  };

  it('shows the strip when the debt output lists a negative year', () => {
    hoisted.outputs = withDebt({ negative_noi_years: [1], total_shortfall_usd: 1_920_000, noi_warning: null });
    render(<CashFlowTab />);
    expect(screen.getByTestId('noi-warning-strip')).toHaveTextContent(
      'Year 1 NOI is negative · debt service shortfall $1.92M · DSCR N/A for Year 1',
    );
  });

  it('no strip when empty, or on a run that predates the fields', () => {
    hoisted.outputs = withDebt({ negative_noi_years: [], total_shortfall_usd: 0, noi_warning: null });
    render(<CashFlowTab />);
    expect(screen.queryByTestId('noi-warning-strip')).toBeNull();
    cleanup();
    hoisted.outputs = withDebt({ year_one_dscr: 1.3 });
    render(<CashFlowTab />);
    expect(screen.queryByTestId('noi-warning-strip')).toBeNull();
  });
});
