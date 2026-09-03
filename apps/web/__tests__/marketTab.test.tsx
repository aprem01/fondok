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
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  // Non-numeric id → the live worker fetch path runs (numeric ids are treated
  // as mock/demo and skip the fetch).
  useParams: () => ({ id: 'deal-uuid-1' }),
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
  median_price_per_key: 337_000,
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

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, city: 'Miami Beach', field_overrides: {} },
    refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle' }),
}));

// Keep the REAL getEngineField; swap the hook to serve no outputs (context
// callouts fall back to the neutral anchor line — never a fabricated number).
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: null, previous: null, loading: false, lastRunAt: null, refresh: vi.fn() }),
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import MarketTab from '@/components/project/MarketTab';

beforeEach(() => {
  cleanup();
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

    expect(screen.getByText('$337,000')).toBeInTheDocument(); // median $/key
    expect(screen.getByText('6.20%')).toBeInTheDocument(); // median cap
    expect(screen.getByText('2 of 2 comps disclose a cap rate')).toBeInTheDocument();
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
