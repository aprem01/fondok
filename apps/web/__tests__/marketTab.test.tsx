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
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
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
import { STR_MARKET_OVERRIDE_NOTE } from '@/lib/provenance';

beforeEach(() => {
  cleanup();
  mockOverrides = {};
  mockSources = {};
  mockReasons = {};
  mockProvSettled = true;
  mockOutputs = null;
  engineRunSpy.mockClear();
  vi.mocked(api.deals.update).mockClear();
});

const STR_FLAG = { value: true, note: 'STR market rates enabled from the Market tab' };

// FON-61 (D4) — Market → Financials propagation is EXPLICIT: "Use STR rates"
// writes starting_occupancy / starting_adr = the comp-set values the card
// shows, each carrying the exact note the worker keys the ``str_forecast``
// tag on. "Revert" removes the flag + both STR-noted keys (an analyst's own
// override on either key survives).
describe('MarketTab — "Use STR rates in the model" writes explicit Year-1 overrides', () => {
  // From MARKET: occ = 71.4 / 1.032 = 69.186… → card shows 69.2% → 0.692;
  // ADR = 278 / 0.942 = 295.1… → card shows $295 → 295.
  it('writes starting_occupancy / starting_adr = the displayed comp-set values with the exact STR note', async () => {
    render(<MarketTab projectId="deal-uuid-1" />);
    // The market payload loads async — wait for the card. The values the card
    // displays are the values that get written.
    expect((await screen.findAllByText('69.2%')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('$295').length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByText('Use STR rates in the model'));
    await waitFor(() => expect(api.deals.update).toHaveBeenCalledTimes(1));

    const [, body] = vi.mocked(api.deals.update).mock.calls[0] as unknown as [string, { field_overrides: Record<string, { value: unknown; note: string }> }];
    const ov = body.field_overrides;
    expect(ov.revenue_seed_from_str_forecast).toEqual({ value: true, note: 'STR market rates enabled from the Market tab' });
    expect(STR_MARKET_OVERRIDE_NOTE).toBe('STR comp-set market rates (Market tab)');
    expect(ov.starting_occupancy.note).toBe('STR comp-set market rates (Market tab)');
    expect(ov.starting_occupancy.value).toBeCloseTo(0.692, 9);
    expect(ov.starting_adr).toEqual({ value: 295, note: 'STR comp-set market rates (Market tab)' });
  });

  it('"Revert" deletes the flag and the STR-noted keys, keeping an analyst override on the same key', async () => {
    mockOverrides = {
      revenue_seed_from_str_forecast: STR_FLAG,
      starting_occupancy: { value: 0.692, note: STR_MARKET_OVERRIDE_NOTE },
      // The analyst later pinned ADR themselves — that intent must survive.
      starting_adr: { value: 310, note: 'Broker guidance' },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' },
    };
    // The worker tags the STR-noted key ``str_forecast`` and the analyst's own
    // ADR override ``analyst_override`` — occupancy on STR = basis is active.
    mockSources = { starting_occupancy: 'str_forecast', starting_adr: 'analyst_override' };
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByText('STR / Market basis active')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Revert to T-12 actuals'));
    await waitFor(() => expect(api.deals.update).toHaveBeenCalledTimes(1));

    const [, body] = vi.mocked(api.deals.update).mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({
      starting_adr: { value: 310, note: 'Broker guidance' },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' },
    });
  });
});

// The "STR rates" card is TAG-HONEST: its state is the worker's source tag on
// starting_occupancy / starting_adr (the same tags Financials → Projections
// reads), never the ``revenue_seed_from_str_forecast`` flag alone.
describe('MarketTab — the STR rates card reads the worker source tags, not the flag', () => {
  it('flag on + str_forecast tag → the active basis card', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'str_forecast', starting_adr: 'str_forecast' };
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByTestId('str-card-active')).toHaveTextContent('STR / Market basis active');
    expect(screen.queryByTestId('str-card-unavailable')).not.toBeInTheDocument();
    expect(screen.queryByTestId('str-card-pending')).not.toBeInTheDocument();
  });

  it('flag on + str_forecast_unavailable tag → honest "STR rates unavailable — using T-12 base"; Clear STR request drops the flag', async () => {
    mockOverrides = {
      revenue_seed_from_str_forecast: STR_FLAG,
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' },
    };
    mockSources = {
      revenue_seed_from_str_forecast: 'str_forecast_unavailable',
      starting_occupancy: 't12_actual',
      starting_adr: 't12_actual',
    };
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-unavailable');
    expect(card).toHaveTextContent('STR rates unavailable — using T-12 base');
    expect(card).toHaveTextContent('the model is on the T-12 base');
    expect(screen.queryByText('STR / Market basis active')).not.toBeInTheDocument();

    // Same write as Revert: the flag goes, unrelated overrides survive.
    fireEvent.click(screen.getByText('Clear STR request'));
    await waitFor(() => expect(api.deals.update).toHaveBeenCalledTimes(1));
    const [, body] = vi.mocked(api.deals.update).mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({ mgmt_fee_pct: { value: 0.03, note: 'Analyst' } });
  });

  it('flag on + NO tag from the worker → "Pending re-run", never "active"; Re-run model triggers the run', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {}; // provenance loaded, but the worker returned no tag for these keys
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-pending');
    expect(card).toHaveTextContent('Pending re-run');
    expect(screen.queryByText('STR / Market basis active')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Re-run model'));
    await waitFor(() => expect(engineRunSpy).toHaveBeenCalledTimes(1));
    expect(api.deals.update).not.toHaveBeenCalled(); // re-run writes nothing
  });

  it('flag on while the provenance map is still loading → "Checking model basis…" (no re-run offered yet)', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {};
    mockProvSettled = false;
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-pending');
    expect(card).toHaveTextContent('Checking model basis…');
    expect(screen.queryByText('Re-run model')).not.toBeInTheDocument();
    expect(screen.queryByText('STR / Market basis active')).not.toBeInTheDocument();
  });

  // Phase 4.4 — the card now reads the worker's REFUSAL CODE first and only
  // then sniffs the source tag. Both paths must land on the same card, so the
  // strip is identical before and after the worker starts emitting codes.
  it('flag on + worker reason `str_unavailable` (no source tag at all) → the same unavailable card', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {}; // the worker tagged nothing — the CODE is the whole signal
    mockReasons = { revenue_seed_from_str_forecast: 'str_unavailable' };
    render(<MarketTab projectId="deal-uuid-1" />);
    const card = await screen.findByTestId('str-card-unavailable');
    expect(card).toHaveTextContent('STR rates unavailable — using T-12 base');
    expect(card).toHaveTextContent('the model is on the T-12 base');
    // Same copy, same actions — the code changed nothing a tester can see.
    expect(screen.getByText('Clear STR request')).toBeInTheDocument();
    expect(screen.queryByTestId('str-card-pending')).not.toBeInTheDocument();
    expect(screen.queryByTestId('str-card-active')).not.toBeInTheDocument();
  });

  it('flag on + the code ABSENT → falls back to the str_forecast_unavailable tag, same card', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { revenue_seed_from_str_forecast: 'str_forecast_unavailable' };
    mockReasons = {}; // every worker build today
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByTestId('str-card-unavailable')).toHaveTextContent(
      'STR rates unavailable — using T-12 base',
    );
    expect(screen.queryByTestId('str-card-pending')).not.toBeInTheDocument();
  });

  it('a populated model still wins: an active str_forecast tag is never re-labelled by a stale code', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'str_forecast', starting_adr: 'str_forecast' };
    mockReasons = { revenue_seed_from_str_forecast: 'str_unavailable' };
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByTestId('str-card-active')).toHaveTextContent('STR / Market basis active');
    expect(screen.queryByTestId('str-card-unavailable')).not.toBeInTheDocument();
  });

  it('an unrelated reason code on the seed key does NOT flip the card to unavailable', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {};
    mockReasons = { revenue_seed_from_str_forecast: 'as_of_unknown' };
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByTestId('str-card-pending')).toHaveTextContent('Pending re-run');
    expect(screen.queryByTestId('str-card-unavailable')).not.toBeInTheDocument();
  });

  it('flag off → the "Model input" card with "Use STR rates in the model", whatever the tags say', async () => {
    mockOverrides = {};
    mockSources = { starting_occupancy: 't12_actual', starting_adr: 't12_actual' };
    render(<MarketTab projectId="deal-uuid-1" />);
    expect(await screen.findByText('Use STR rates in the model')).toBeInTheDocument();
    expect(screen.queryByTestId('str-card-active')).not.toBeInTheDocument();
    expect(screen.queryByTestId('str-card-pending')).not.toBeInTheDocument();
  });
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
    expect(within(row).getByText('—')).toBeInTheDocument(); // seller → em dash
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

    const supplyTile = screen.getByText('Supply Growth').parentElement as HTMLElement;
    expect(within(supplyTile).getByText('—')).toBeInTheDocument();

    // The canonical prototype placeholders must never appear as data.
    expect(screen.queryByText('+4.2%')).not.toBeInTheDocument();
    expect(screen.queryByText('612 keys')).not.toBeInTheDocument();

    // Recovered comp-set metric IS real data (subject ÷ published index).
    expect(screen.getAllByText('69.2%').length).toBeGreaterThan(0); // 71.4% ÷ 1.032
  });
});

// FON-61 §3 — "View Projections →" from Market used to open Financials →
// Historicals because it carried only `?tab=pl`. Both STR cards now name the
// sub-tab (`?tab=pl&sub=projections`).
describe('MarketTab — "View Projections →" deep-links to Financials → Projections', () => {
  it('the active STR card links to ?tab=pl&sub=projections', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'str_forecast', starting_adr: 'str_forecast' };
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-active');
    const link = within(card).getByRole('link', { name: 'View Projections →' });
    expect(link).toHaveAttribute('href', '/projects/deal-uuid-1?tab=pl&sub=projections');
  });

  it('the STR-unavailable card links to ?tab=pl&sub=projections', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {
      revenue_seed_from_str_forecast: 'str_forecast_unavailable',
      starting_occupancy: 't12_actual',
      starting_adr: 't12_actual',
    };
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-unavailable');
    const link = within(card).getByRole('link', { name: 'View Projections →' });
    expect(link).toHaveAttribute('href', '/projects/deal-uuid-1?tab=pl&sub=projections');
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

describe('MarketTab — the active card names the basis and the Base Year separately', () => {
  it('states the comp-set benchmark and the Base Year as two different facts', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    // The worker seeded from the subject's own TTM — an actual, not a forecast.
    mockSources = { starting_occupancy: 'str_subject_ttm', starting_adr: 'str_subject_ttm' };
    mockOutputs = REVENUE_OUTPUTS;
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-active');
    // The eyebrow names a basis, not a rate.
    expect(card).toHaveTextContent('STR / Market basis active');
    // Clause 1 — the comp-set benchmark, labelled as a benchmark.
    expect(card).toHaveTextContent('Comp-set benchmark:');
    expect(card).toHaveTextContent('69.2%'); // 71.4 ÷ 1.032
    expect(card).toHaveTextContent('$295'); // 278 ÷ 0.942
    // Clause 2 — the Base Year the model actually uses, from the engine.
    expect(card).toHaveTextContent('Financials → Projections Base Year:');
    expect(card).toHaveTextContent('71.6%');
    expect(card).toHaveTextContent('$288');
    // …and it names which STR basis the model is on.
    expect(card).toHaveTextContent('STR · Subject TTM actual');
  });

  it('never asserts the comp-set number IS the Year-1 assumption', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'str_subject_ttm', starting_adr: 'str_subject_ttm' };
    mockOutputs = REVENUE_OUTPUTS;
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-active');
    expect(card).not.toHaveTextContent('The model is using these STR market rates for Year-1');
    expect(card).toHaveTextContent('The model does not substitute these');
  });

  it('says so plainly when the comp-set rates ARE the applied Year-1 input', async () => {
    // The analyst clicked "Use STR rates": the comp-set values were written as
    // explicit overrides, and the worker badges them ``str_comp_set``.
    mockOverrides = {
      revenue_seed_from_str_forecast: STR_FLAG,
      starting_occupancy: { value: 0.692, note: STR_MARKET_OVERRIDE_NOTE },
      starting_adr: { value: 295, note: STR_MARKET_OVERRIDE_NOTE },
    };
    mockSources = { starting_occupancy: 'str_comp_set', starting_adr: 'str_comp_set' };
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-active');
    expect(card).toHaveTextContent('The analyst applied these comp-set rates as the Year-1 input.');
    expect(card).not.toHaveTextContent('The model does not substitute these');
  });

  it('claims no Base Year at all before the model has run', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = { starting_occupancy: 'str_subject_ttm', starting_adr: 'str_subject_ttm' };
    mockOutputs = null; // no engine outputs, and the provenance map carries no value
    render(<MarketTab projectId="deal-uuid-1" />);

    const card = await screen.findByTestId('str-card-active');
    expect(card).toHaveTextContent(
      'The Base Year in Financials → Projections is not available until the model has run.',
    );
    // The comp-set figures are still shown — as a benchmark, which they are.
    expect(card).toHaveTextContent('Comp-set benchmark:');
  });

  it('the pending and model-input cards carry the same two-clause split', async () => {
    mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
    mockSources = {};
    mockOutputs = REVENUE_OUTPUTS;
    const { unmount } = render(<MarketTab projectId="deal-uuid-1" />);
    const pending = await screen.findByTestId('str-card-pending');
    expect(pending).toHaveTextContent('Comp-set benchmark:');
    expect(pending).toHaveTextContent('Financials → Projections Base Year:');
    expect(pending).toHaveTextContent('71.6%');
    unmount();

    mockOverrides = {};
    render(<MarketTab projectId="deal-uuid-1" />);
    const off = await screen.findByText('Use STR rates in the model');
    const card = off.closest('div')?.parentElement as HTMLElement;
    expect(card).toHaveTextContent('Comp-set benchmark:');
    expect(card).toHaveTextContent('Financials → Projections Base Year:');
    expect(card).toHaveTextContent('71.6%');
  });

  it('an STR basis is still "active" under any of the three STR source ids', async () => {
    for (const source of ['str_forecast', 'str_subject_ttm', 'str_comp_set']) {
      cleanup();
      mockOverrides = { revenue_seed_from_str_forecast: STR_FLAG };
      mockSources = { starting_occupancy: source, starting_adr: source };
      render(<MarketTab projectId="deal-uuid-1" />);
      expect(await screen.findByTestId('str-card-active')).toBeInTheDocument();
    }
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
        note: 'Transaction comps included in the Market tab summary',
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
        note: 'Transaction comps included in the Market tab summary',
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
        note: 'Transaction comps included in the Market tab summary',
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
        note: 'Transaction comps included in the Market tab summary',
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
