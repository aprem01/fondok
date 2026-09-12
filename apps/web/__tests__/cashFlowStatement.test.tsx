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
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import type { EngineOutputsResponse, CashFlowStatementOutput } from '@/lib/api';
import { getEngineField } from '@/lib/hooks/useEngineOutputs';
import {
  buildSummary,
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

    fireEvent.click(screen.getByRole('button', { name: 'Financials →' }));
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
