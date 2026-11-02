/**
 * MarketTab — canonical rebuild regression suite (FON-72).
 *
 * Contracts locked here:
 *
 *  1. TRANSACTION COMPS — SELLER COLUMN. The rebuilt comps grid renders the new
 *     `seller` backend field as its own column (the design's ninth column). A
 *     disclosed seller shows its name; a comp with no seller renders "—" (the
 *     canonical awaiting em dash), never a fabricated value.
 *
 *  2. AWAITING-DATA IS AN EM DASH, NOT A NUMBER. Market Overview tiles whose
 *     data source isn't extracted (Demand / Supply Growth) render "—". The
 *     canonical prototype placeholders (e.g. "+4.2%", "612 keys") are NEVER
 *     wired as data.
 *
 * The tab reads exclusively from the mocked market API / engine outputs — no
 * fixtures, no prototype numbers.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import React from 'react';

// Non-numeric id → the live worker fetch path runs (numeric ids are treated
// as mock/demo and skip the fetch).
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

// Real aggregated STR/CoStar market data. Subject metrics + published MPI/ARI/
// RGI indices → the blended comp set is recovered (comp = subject ÷ index).
const MARKET = {
  deal_id: 'deal-uuid-1',
  str_trend: {
    subject_occupancy_pct: 0.714,
    subject_adr_usd: 278,
    subject_revpar_usd: 198.5,
    mpi_occupancy_index: 103.2,
    ari_adr_index: 94.2,
    rgi_revpar_index: 97.2,
    comp_set_size: 8,
    total_keys: 2008,
    compset: [
      { name: 'The Betsy Hotel', keys: 61, occupancy_pct: null, adr_usd: null, revpar_usd: null },
      { name: 'Nautilus Sonesta', keys: 250, occupancy_pct: null, adr_usd: null, revpar_usd: null },
    ],
  },
  sources: {},
};

// Two extracted comps — one with a disclosed seller, one without (seller null).
const COMPS = {
  deal_id: 'deal-uuid-1',
  comps: [
    {
      name: 'The Betsy Hotel',
      market: 'Miami Beach — South Beach',
      sale_date: 'Jun 2025',
      keys: 61,
      sale_price_usd: 25_010_000,
      price_per_key_usd: 410_000,
      cap_rate_pct: 5.8,
      buyer_name: 'Certares Real Estate',
      buyer_type: null,
      seller: 'Betsy Ross Hospitality',
      interest: 'Ground Lease', // R-064 — stated by the OM
      source_document_id: 'doc-1',
      source_page: 12,
    },
    {
      name: 'Z Ocean Hotel',
      market: 'Miami Beach — South Beach',
      sale_date: 'Aug 2024',
      keys: 68,
      sale_price_usd: 18_020_000,
      price_per_key_usd: 265_000,
      cap_rate_pct: 6.6,
      buyer_name: 'Sixty Hotels',
      buyer_type: null,
      seller: null, // no seller disclosed → renders "—"
      interest: null, // R-064 — not stated → renders "—", never inferred
      source_document_id: null,
      source_page: null,
    },
  ],
  // The true median of the two rows above — the worker's `_median` averages
  // the middle pair on an even count. The tiles recompute it from the selected
  // subset, so a fixture that disagreed with its own rows would be testing the
  // fixture rather than the code.
  median_price_per_key: 337_500,
  median_cap_rate_pct: 6.2,
  note: null,
};

vi.mock('@/lib/api', async () => {
  const { withQuietWorker } = await import('./helpers/quietWorker');
  const actual = withQuietWorker(await vi.importActual<typeof import('@/lib/api')>('@/lib/api'));
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      market: {
        ...actual.api.market,
        data: vi.fn(async () => MARKET),
        overview: vi.fn(async () => null),
        transactionComps: vi.fn(async () => COMPS),
      },
      deals: {
        ...actual.api.deals,
        provenance: vi.fn(async () => ({ deal_id: 'deal-uuid-1', engines: {} })),
        update: vi.fn(async () => ({ id: 'deal-uuid-1' })),
      },
    },
  };
});

// Mutable so the FON-61 tests can start from a deal that already carries the
// STR seed (read at render time — the factory itself is hoisted).
let mockOverrides: Record<string, unknown> = {};
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, city: 'Miami Beach', field_overrides: mockOverrides },
    refresh: vi.fn(),
  }),
}));

// Stable spy so the STR card's "Re-run model" action can be asserted.
const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, status: 'idle' }),
}));

// FON-61 (D4) — the STR card reads the WORKER's source tags (settable per
// test), exactly like Financials → Projections. ``mockProvSettled`` = whether
// the provenance fetch has finished (false → "Checking model basis…").
let mockSources: Record<string, string> = {};
let mockProvSettled = true;
// Phase 4.4 — the worker's machine-readable refusal codes ride the SAME
// assumption_sources payload as the source tags (`reasons[key]`, a bare
// ReasonCode). Empty here unless a test sets one, so every pre-existing
// expectation below sees exactly the object it saw before.
let mockReasons: Record<string, string> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) =>
    key && (mockSources[key] || mockReasons[key])
      ? { source: mockSources[key] ?? '', value: null, reason: mockReasons[key] ?? null }
      : null,
  useProvenanceState: () => ({ ready: Object.keys(mockSources).length > 0, settled: mockProvSettled }),
}));

// Keep the REAL getEngineField; swap the hook to serve no outputs by default
// (context callouts fall back to the neutral anchor line — never a fabricated
// number). FON-61 (61.1) sets real revenue-engine years so the card can quote
// the Base Year Financials → Projections actually renders.
let mockOutputs: unknown = null;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: mockOutputs, previous: null, loading: false, lastRunAt: null, refresh: vi.fn() }),
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import MarketTab from '@/components/project/MarketTab';
import { api } from '@/lib/api';
import { resetRouter } from './helpers/mockRouter';

beforeEach(() => {
  cleanup();
  resetRouter(nav);
  mockOverrides = {};
  mockSources = {};
  mockReasons = {};
  mockProvSettled = true;
  mockOutputs = null;
  engineRunSpy.mockClear();
  vi.mocked(api.deals.update).mockClear();
});




describe('MarketTab — Transaction Comps SELLER column (new backend field)', () => {
  it('renders the SELLER column with the disclosed seller from mocked data', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));

    // Wait for the loaded comps grid (SELLER only exists once the fetch lands —
    // avoids the loading→loaded card swap detaching an earlier match).
    expect(await screen.findByText('SELLER', undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('Transaction Comparables')).toBeInTheDocument();

    // A disclosed seller value renders in its column.
    expect(screen.getByText('Betsy Ross Hospitality')).toBeInTheDocument();

    // The BUYER column is still there (seller is additive, not a rename).
    expect(screen.getByText('BUYER')).toBeInTheDocument();
    expect(screen.getByText('Certares Real Estate')).toBeInTheDocument();
  });

  it('renders a missing seller as an em dash, never a fabricated name', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));
    await screen.findByText('SELLER', undefined, { timeout: 5000 });

    // Z Ocean Hotel discloses a buyer but no seller.
    const row = screen.getByText('Z Ocean Hotel').parentElement as HTMLElement;
    expect(within(row).getByText('Sixty Hotels')).toBeInTheDocument(); // buyer disclosed
    // seller → em dash (the Interest cell is "—" too: neither is disclosed)
    expect(within(row).getAllByText('—')).toHaveLength(2);
  });

  it('R-064 — renders the INTEREST column: the stated interest, or "—" when the OM does not state it', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));
    await screen.findByText('INTEREST', undefined, { timeout: 5000 });
    const cells = screen.getAllByTestId('comp-interest').map((c) => c.textContent);
    expect(cells).toEqual(expect.arrayContaining(['Ground Lease', '—']));
    expect(cells).toHaveLength(2);
    expect(screen.queryByText('Fee Simple')).not.toBeInTheDocument();
  });

  it('shows real median anchors + a neutral context (no engine outputs, no placeholders)', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));
    await screen.findByText('SELLER', undefined, { timeout: 5000 });

    // FON-60 (60.1) — an uncurated deal is all-selected, so the tiles are the
    // worker's own whole-set medians to the digit: $337,500 = (265k + 410k)/2
    // and 6.20% = (5.8 + 6.6)/2, the same convention as market.py `_median`.
    expect(screen.getByText('$337,500')).toBeInTheDocument(); // median $/key
    expect(screen.getByText('6.20%')).toBeInTheDocument(); // median cap
    expect(
      screen.getByText('2 of 2 selected comps disclose a cap rate · 2 observations'),
    ).toBeInTheDocument();
    // Context falls back to the neutral anchor line when no engine basis exists.
    expect(screen.getByText('Anchor for entry / exit valuation.')).toBeInTheDocument();
  });
});

describe('MarketTab — Market Overview awaiting-data em dashes', () => {
  it('renders Demand / Supply Growth as em dashes and never wires prototype numbers', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);

    // Overview renders once the market-data fetch resolves.
    const demandLabel = await screen.findByText('Demand Growth', undefined, { timeout: 5000 });
    const demandTile = demandLabel.parentElement as HTMLElement;
    expect(within(demandTile).getByText('—')).toBeInTheDocument();

    const supplyTile = screen.getByText('Under Construction').parentElement as HTMLElement;
    expect(within(supplyTile).getByText('—')).toBeInTheDocument();

    // The canonical prototype placeholders must never appear as data.
    expect(screen.queryByText('+4.2%')).not.toBeInTheDocument();
    expect(screen.queryByText('612 keys')).not.toBeInTheDocument();

    // Recovered comp-set metric IS real data (subject ÷ published index).
    expect(screen.getAllByText('69.2%').length).toBeGreaterThan(0); // 71.4% ÷ 1.032
  });
});


// FON-60 §4 (Sam 09-11) — "remove property-name/source deep-linking from
// Transaction Comps for now. Property names should display as normal text."
// The old link was a raw worker download URL built from an unvalidated
// `source_document_id`, which returned "document not found on deal".
describe('MarketTab — Transaction Comps property names are plain text', () => {
  it('renders the property name as plain text, never a source link', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));
    await screen.findByText('SELLER', undefined, { timeout: 5000 });

    // The Betsy Hotel fixture carries BOTH source_document_id and source_page —
    // the exact case that used to render a deep link.
    expect(screen.getAllByText('The Betsy Hotel').length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: 'The Betsy Hotel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Z Ocean Hotel' })).not.toBeInTheDocument();

    // No anchor anywhere points at a raw worker document download.
    const hrefs = Array.from(document.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? '');
    expect(hrefs.some((h) => h.includes('/documents/'))).toBe(false);
  });

  it('the comps caption no longer promises source deep-linking', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByText('Transaction Comps'));
    await screen.findByText('SELLER', undefined, { timeout: 5000 });

    expect(
      screen.getByText('Extracted from Offering Memorandums and market reports in the Data Room'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/deep-link to the source page/)).not.toBeInTheDocument();
  });
});


// ─────────────────────────────────────────────────────────────────────────
// Sub-tab routing convention (FON-59 #4 / FON-61 §3)
//
// Every sub-tab is now a URL slug on the shared `useSubTab` hook, so a deep
// link lands where it says, the back button works, and `setSub` keeps every
// other query param (`doc`, `focus`, `reviewField`) intact.
// ─────────────────────────────────────────────────────────────────────────

describe('MarketTab — `?tab=market&sub=<slug>` routing', () => {
  const tabEl = (name: string) => screen.getByRole('tab', { name });
  const mount = () => render(<MarketTab projectId="deal-uuid-1" />);

  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
    nav.replace.mockClear();
  });

  it('opens Transaction Comps on ?sub=transaction-comps', async () => {
    nav.params = new URLSearchParams('tab=market&sub=transaction-comps');
    mount();
    await waitFor(() => expect(tabEl('Transaction Comps')).toHaveAttribute('aria-selected', 'true'));
    expect(tabEl('Market Overview')).toHaveAttribute('aria-selected', 'false');
  });

  it('opens Index Analysis on ?sub=index-analysis', async () => {
    nav.params = new URLSearchParams('tab=market&sub=index-analysis');
    mount();
    await waitFor(() => expect(tabEl('Index Analysis')).toHaveAttribute('aria-selected', 'true'));
  });

  it('falls back to Market Overview on an unknown sub value', async () => {
    nav.params = new URLSearchParams('tab=market&sub=not-a-sub-tab');
    mount();
    await waitFor(() => expect(tabEl('Market Overview')).toHaveAttribute('aria-selected', 'true'));
  });

  it('follows a param change while already mounted', async () => {
    nav.params = new URLSearchParams('tab=market&sub=transaction-comps');
    const { rerender } = mount();
    await waitFor(() => expect(tabEl('Transaction Comps')).toHaveAttribute('aria-selected', 'true'));

    nav.params = new URLSearchParams('tab=market&sub=index-analysis');
    rerender(<MarketTab projectId="deal-uuid-1" />);
    await waitFor(() => expect(tabEl('Index Analysis')).toHaveAttribute('aria-selected', 'true'));
  });

  it('setSub writes sub= and preserves doc / focus / reviewField', async () => {
    nav.params = new URLSearchParams('tab=market&doc=doc-9&focus=noi&reviewField=noi_usd');
    mount();
    await waitFor(() => expect(tabEl('Market Overview')).toHaveAttribute('aria-selected', 'true'));

    fireEvent.click(tabEl('Index Analysis'));
    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const written = new URLSearchParams(url.split('?')[1]);
    expect(written.get('sub')).toBe('index-analysis');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('noi');
    expect(written.get('reviewField')).toBe('noi_usd');
    expect(tabEl('Index Analysis')).toHaveAttribute('aria-selected', 'true');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-61 §1 — the STR card states a BASIS, not a rate
//
// Sam: "Market Overview currently says the model is using 65.2% Occupancy /
// $383 ADR as Year-1 assumptions. However, Financials → Projections actually
// shows Base Year 71.6% / $288."
//
// Both numbers were right. The card was printing the COMP-SET blend and
// asserting it was the underwriting input; the worker deliberately seeds Year-1
// from the subject's own STR trailing twelve months. The methodology stays; the
// sentence changes.
// ─────────────────────────────────────────────────────────────────────────

/** Revenue-engine years — ``years[0]`` is the row Financials → Projections
 *  renders as "Base Year (Year 1)". Occupancy is a 0..1 fraction. */
const STR_FLAG = { value: true, note: 'STR market rates enabled from the Market Comps tab' };

const REVENUE_OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    revenue: {
      outputs: {
        years: [
          { year: 1, occupancy: 0.716, adr: 288, revpar: 206.2 },
          { year: 2, occupancy: 0.73, adr: 300, revpar: 219 },
        ],
      },
    },
  },
};


// FON-116 / FON-117 — the "Use STR rates in the model" switch is retired: the
// comp set IS the model's benchmark by default (benchmark × penetration ×
// ramp). The card says so, names the benchmark the topline resolved, keeps the
// comp-set benchmark and the Base Year as two separate facts (FON-61 §1), and
// links to the Topline assumptions. It writes nothing.
describe('MarketTab — the comp set is the model benchmark (no STR toggle)', () => {
  it('states the default, the comp-set benchmark and the Base Year; no switch, no Revert', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'topline_benchmark', starting_adr: 'topline_benchmark' };
    mockOutputs = REVENUE_OUTPUTS;
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-benchmark');
    expect(card).toHaveTextContent("The comp set is the model's benchmark by default.");
    expect(card).toHaveTextContent('Comp-set benchmark:');
    expect(card).toHaveTextContent('69.2%');
    expect(card).toHaveTextContent('P&L → Future P&L Base Year:');
    expect(card).toHaveTextContent('71.6%');
    expect(screen.queryByText('Use STR rates in the model')).toBeNull();
    expect(screen.queryByText('Revert to T-12 actuals')).toBeNull();
    expect(screen.queryByTestId('str-card-active')).toBeNull();
    expect(screen.queryByTestId('str-card-pending')).toBeNull();
    expect(screen.queryByTestId('str-card-unavailable')).toBeNull();
  });

  it('links to the Topline assumptions on P&L → Future P&L', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-benchmark');
    const link = within(card).getByRole('link');
    expect(link).toHaveAttribute('href', '/projects/deal-uuid-1?tab=pl&sub=projections');
    expect(link).toHaveTextContent('Topline assumptions in Future P&L →');
  });

  it('claims no Base Year before the model has run', async () => {
    mockOutputs = null;
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByTestId('str-card-benchmark')).toHaveTextContent(
      'The Base Year in P&L → Future P&L is not available until the model has run.',
    );
  });

  it('writes nothing on render', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    await screen.findByTestId('str-card-benchmark');
    expect(api.deals.update).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-60 §1/§2/§3 — extracted transactions are not selected comps
// ─────────────────────────────────────────────────────────────────────────

const openComps = async () => {
  fireEvent.click(screen.getByText('Transaction Comps'));
  await screen.findByText('SELLER', undefined, { timeout: 5000 });
};

describe('MarketTab — Include as Comp (FON-60 §1)', () => {
  it('defaults to every comp selected, so an existing deal is unchanged', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    expect(screen.getByText('2 of 2 selected as comps')).toBeInTheDocument();
    const boxes = screen.getAllByRole('checkbox');
    expect(boxes).toHaveLength(2);
    for (const b of boxes) expect(b).toBeChecked();
    // …and the tiles are the worker's whole-set figures to the digit.
    expect(screen.getByText('$337,500')).toBeInTheDocument();
    expect(screen.getByText('6.20%')).toBeInTheDocument();
  });

  it('unchecking a row recomputes the tiles from the selected subset and PATCHes the deal', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    // Drop Z Ocean Hotel ($265,000 / 6.6%), leaving The Betsy ($410,000 / 5.8%).
    fireEvent.click(screen.getByLabelText('Include Z Ocean Hotel as a comp'));

    await waitFor(() => expect(api.deals.update).toHaveBeenCalledTimes(1));
    const [, body] = vi.mocked(api.deals.update).mock.calls[0] as unknown as [
      string,
      { field_overrides: Record<string, { value: string[]; note: string }> },
    ];
    expect(body.field_overrides['market.selected_comps'].value).toEqual([
      'The Betsy Hotel|Jun 2025|25010000',
    ]);
    expect(body.field_overrides['market.selected_comps'].note.length).toBeGreaterThan(0);

    // One observation left on each tile — so neither is a median any more.
    expect(await screen.findByText('1 of 2 selected as comps')).toBeInTheDocument();
    const perKey = screen.getByTestId('comp-tile-per-key');
    expect(within(perKey).getByText('Reported $ / Key')).toBeInTheDocument();
    expect(within(perKey).getByText('$410,000')).toBeInTheDocument();
    const cap = screen.getByTestId('comp-tile-cap-rate');
    expect(within(cap).getByText('Reported Cap Rate')).toBeInTheDocument();
    expect(within(cap).getByText('5.80%')).toBeInTheDocument();
    // The worker's whole-set figure stays visible.
    expect(within(perKey).getByText('All 2 extracted: $337,500')).toBeInTheDocument();
  });

  it('reads a persisted selection off the deal and computes on it', async () => {
    mockOverrides = {
      'market.selected_comps': {
        value: ['Z Ocean Hotel|Aug 2024|18020000'],
        note: 'Transaction comps included in the Market Comps tab summary',
      },
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    expect(screen.getByText('1 of 2 selected as comps')).toBeInTheDocument();
    expect(screen.getByLabelText('Include Z Ocean Hotel as a comp')).toBeChecked();
    expect(screen.getByLabelText('Include The Betsy Hotel as a comp')).not.toBeChecked();
    expect(within(screen.getByTestId('comp-tile-per-key')).getByText('$265,000')).toBeInTheDocument();
    expect(within(screen.getByTestId('comp-tile-cap-rate')).getByText('6.60%')).toBeInTheDocument();
  });
});

describe('MarketTab — the commentary does not conclude from an uncurated set (FON-60 §2)', () => {
  it('states the fact and drops the judgement while nothing has been curated', async () => {
    mockOutputs = {
      deal_id: 'deal-uuid-1',
      engines: { capital: { outputs: { price_per_key: 280_000, entry_cap_rate: 0.07 } } },
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    const tile = screen.getByTestId('comp-tile-per-key');
    expect(tile).toHaveTextContent('below the median of all 2 extracted transactions');
    expect(tile).not.toHaveTextContent('Supportive of');
    expect(tile).not.toHaveTextContent('supportive of');
    expect(tile).toHaveTextContent('Include the transactions that are genuinely comparable');
  });

  it('restores the conclusion once a subset is selected, naming the subset', async () => {
    mockOverrides = {
      'market.selected_comps': {
        value: ['The Betsy Hotel|Jun 2025|25010000', 'Z Ocean Hotel|Aug 2024|18020000'],
        note: 'Transaction comps included in the Market Comps tab summary',
      },
    };
    mockOutputs = {
      deal_id: 'deal-uuid-1',
      engines: { capital: { outputs: { price_per_key: 280_000, entry_cap_rate: 0.07 } } },
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    const tile = screen.getByTestId('comp-tile-per-key');
    expect(tile).toHaveTextContent('below the median of the 2 selected comps');
    expect(tile).toHaveTextContent('Supportive of the entry valuation on that set.');
  });
});

describe('MarketTab — a "median" of one observation is not a median (FON-60 §3)', () => {
  it('at n = 1 the cap tile uses neither "median" nor "anchor", and states the count', async () => {
    mockOverrides = {
      'market.selected_comps': {
        value: ['The Betsy Hotel|Jun 2025|25010000'],
        note: 'Transaction comps included in the Market Comps tab summary',
      },
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    const tile = screen.getByTestId('comp-tile-cap-rate');
    expect(tile).toHaveTextContent('Reported Cap Rate');
    expect(tile).toHaveTextContent('5.80%');
    expect(tile).toHaveTextContent('1 observation');
    // Neither word appears anywhere on the tile — Sam quoted both back at us.
    expect(tile.textContent ?? '').not.toMatch(/median/i);
    expect(tile.textContent ?? '').not.toMatch(/anchor/i);
    expect(screen.queryByText('Anchor for exit-cap rate selection.')).not.toBeInTheDocument();
    // …and it says what the number IS.
    expect(tile).toHaveTextContent('One disclosed cap rate');
  });

  it('at n = 0 it renders a dash and claims nothing', async () => {
    mockOverrides = {
      'market.selected_comps': {
        value: [],
        note: 'Transaction comps included in the Market Comps tab summary',
      },
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    expect(screen.getByText('0 of 2 selected as comps')).toBeInTheDocument();
    const tile = screen.getByTestId('comp-tile-cap-rate');
    expect(within(tile).getByText('Cap Rate')).toBeInTheDocument();
    expect(tile).toHaveTextContent('—');
    expect(tile).toHaveTextContent('0 observations');
    expect(tile.textContent ?? '').not.toMatch(/median/i);
    expect(tile.textContent ?? '').not.toMatch(/anchor/i);
    const perKey = screen.getByTestId('comp-tile-per-key');
    expect(within(perKey).getByText('$ / Key')).toBeInTheDocument();
    expect(perKey).toHaveTextContent('—');
    expect(perKey).toHaveTextContent('0 observations');
    // No conclusion is drawn from nothing.
    expect(perKey.textContent ?? '').not.toMatch(/supportive|rich versus/i);
  });

  it('at n >= 2 it is a median again, and says how many observations it is', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    await openComps();

    expect(screen.getByText('Median Cap Rate')).toBeInTheDocument();
    expect(screen.getByText('Median $ / Key')).toBeInTheDocument();
    expect(
      screen.getByText('2 of 2 selected comps disclose a cap rate · 2 observations'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Range $265,000 – $410,000 · 2 observations'),
    ).toBeInTheDocument();
  });
});
