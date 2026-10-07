/**
 * FON-61 (E-009 / E-007 / E-008) — Market tab reads the worker's single
 * comp-set derivation, prints the TTM blend's methodology from its inputs,
 * and fills Demand / Supply Growth from the MARKET_STUDY extraction.
 *
 * Contracts locked here:
 *
 *  1. ONE DERIVATION. The Inventory tile's hotel count and key count are the
 *     worker's `comp_set.active_count` / `active_keys` — 4 hotels / 344 keys
 *     when the STR roster lists 5 with one closed. The report's rollup
 *     (`comp_set_size` = 5) is never the headline.
 *
 *  2. CLOSED IS EXPLICIT AND VISIBLE. The roster lists the closed hotel with a
 *     "closed · excluded" chip and its reported keys struck through; the note
 *     names the marker (STR's "Closed - " label). With no marker, every hotel
 *     is counted and the note says so.
 *
 *  3. THE BLEND IS DEFINED FROM ITS INPUTS. The methodology line names the
 *     formula, the document, the period and the hotels — and is absent (not
 *     a generic claim) when the worker supplied no blend.
 *
 *  4. GROWTH TILES NEVER FABRICATE. A value renders with its period and
 *     document; a missing series renders the dash with the worker's reason
 *     ("not in the uploaded reports (no_source)").
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import {
  CompSetRoster,
  SubmarketSnapshot,
  SupplyPipeline,
  ttmBlendMethodology,
  demandGrowthTile,
  supplyGrowthTile,
  monthLabel,
} from '@/components/project/MarketTab';
import type {
  MarketCompSetBlock,
  MarketTtmBlendBlock,
  MarketGrowthBlock,
  MarketSupplyGrowthBlock,
} from '@/lib/api';

afterEach(cleanup);

const STR_DOC = 'STR Trend - Anglers - Jun 2026.xlsx';
const MS_DOC = 'CoStar Submarket Report - South Beach.pdf';

// The tester's roster: 5 hotels listed, Blue Moon closed (STR label, 0 rooms)
// → 4 active hotels / 344 keys. `reported_comp_set_size` is the old "5 hotels".
const COMP_SET: MarketCompSetBlock = {
  hotels: [
    { index: 1, name: 'Z Ocean Hotel', name_as_reported: 'Z Ocean Hotel', keys: 40, status: 'active', status_source: null },
    { index: 2, name: 'Blue Moon Hotel', name_as_reported: 'Closed - Blue Moon Hotel', keys: 0, status: 'closed', status_source: 'str_closed_label' },
    { index: 3, name: 'The Betsy South Beach', name_as_reported: 'The Betsy South Beach', keys: 129, status: 'active', status_source: null },
    { index: 4, name: 'The Tony Hotel', name_as_reported: 'The Tony Hotel', keys: 68, status: 'active', status_source: null },
    { index: 5, name: 'Dream South Beach', name_as_reported: 'Dream South Beach', keys: 107, status: 'active', status_source: null },
  ],
  active_count: 4,
  active_keys: 344,
  closed_count: 1,
  closed_names: ['Blue Moon Hotel'],
  count_basis: 'active_roster',
  keys_basis: 'active_roster',
  status_available: true,
  reported_comp_set_size: 5,
  reported_total_keys: 344,
  source_doc_name: STR_DOC,
  source_doc_id: 'doc-str',
  source_page: 22,
  note: '1 closed hotel (Blue Moon Hotel) excluded from the count and the keys — marked closed by STR\'s "Closed - " roster label.',
};

const BLEND: MarketTtmBlendBlock = {
  occupancy_pct: 69.186,
  adr_usd: 295.1168,
  revpar_usd: 204.2181,
  subject_occupancy_pct: 71.4,
  subject_adr_usd: 278,
  subject_revpar_usd: 198.5,
  mpi: 1.032,
  ari: 0.942,
  rgi: 0.972,
  period_start: '2025-07',
  period_end: '2026-06',
  months: 12,
  period_basis: 'subject_monthly_series',
  report_year: 2026,
  inputs: [
    { field_name: 'ttm_performance.subject.occupancy_pct', value: 0.714, doc_name: STR_DOC, doc_id: 'doc-str', page: 3 },
    { field_name: 'ttm_performance.indices.mpi_occupancy_index', value: 103.2, doc_name: STR_DOC, doc_id: 'doc-str', page: 4 },
  ],
  documents: [STR_DOC],
  method: 'Comp-set Occupancy = subject TTM Occupancy ÷ MPI; …',
};

const DEMAND: MarketGrowthBlock = {
  value_pct: 4.2,
  period_label: 'TTM',
  basis: 'reported',
  inputs: [{ field_name: 'market_study.trend.ttm.demand_change_pct', value: 0.042, doc_name: MS_DOC, doc_id: 'doc-ms', page: 6 }],
  reason: null,
  detail: null,
};

const SUPPLY: MarketSupplyGrowthBlock = {
  existing_rooms: 12000,
  existing_period_label: null,
  under_construction_rooms: 600,
  final_planning_rooms: 300,
  planned_rooms: 1200,
  under_construction_pct: 5,
  final_planning_pct: 2.5,
  reported_supply_change_pct: 1.2,
  reported_supply_change_period: 'TTM',
  inputs: [
    { field_name: 'market_study.supply.existing_rooms', value: 12000, doc_name: MS_DOC, doc_id: 'doc-ms', page: 3 },
    { field_name: 'under_construction.total_rooms', value: 600, doc_name: MS_DOC, doc_id: 'doc-ms', page: 9 },
  ],
  reason: null,
  detail: null,
};

const NO_SOURCE: MarketGrowthBlock = {
  value_pct: null,
  period_label: null,
  basis: null,
  inputs: [],
  reason: 'no_source',
  detail: 'Demand growth (or a two-year demand series) is not in the uploaded reports.',
};

// Per-property perf rows from /market-data — anonymized by STR (all null).
const PERF = COMP_SET.hotels.map((h) => ({
  name: h.name_as_reported,
  keys: h.keys ?? null,
  occupancy_pct: null,
  adr_usd: null,
  revpar_usd: null,
}));

describe('CompSetRoster — closed hotels are marked and excluded (E-009)', () => {
  it('renders the closed row with a chip, struck keys, and the marker note', () => {
    render(<CompSetRoster hotels={COMP_SET.hotels} perf={PERF} note={COMP_SET.note} />);
    expect(screen.getAllByTestId('comp-set-hotel')).toHaveLength(4);
    const closed = screen.getByTestId('comp-set-hotel-closed');
    expect(within(closed).getByText('Blue Moon Hotel')).toBeInTheDocument();
    expect(within(closed).getByTestId('comp-set-closed-chip')).toHaveTextContent('closed · excluded');
    // The reported keys are shown struck through, never summed.
    expect(within(closed).getByTitle('Excluded from the comp-set keys').tagName).toBe('S');
    expect(screen.getByTestId('comp-set-note')).toHaveTextContent('Blue Moon Hotel');
    expect(screen.getByTestId('comp-set-note')).toHaveTextContent('"Closed - " roster label');
    // STR's label itself is not shown as the hotel's name.
    expect(screen.queryByText('Closed - Blue Moon Hotel')).not.toBeInTheDocument();
  });

  it('with no closed marker every hotel is a plain row and the note says so', () => {
    const hotels = COMP_SET.hotels.map((h) => ({ ...h, status: 'active' as const, status_source: null }));
    const note = 'No hotel in the roster carries a closed marker (status field or STR "Closed - " label), so every listed hotel is counted as active.';
    render(<CompSetRoster hotels={hotels} perf={PERF} note={note} />);
    expect(screen.getAllByTestId('comp-set-hotel')).toHaveLength(5);
    expect(screen.queryByTestId('comp-set-hotel-closed')).not.toBeInTheDocument();
    expect(screen.getByTestId('comp-set-note')).toHaveTextContent('every listed hotel is counted as active');
  });

  it('falls back to the /market-data rows (all active) on an older worker', () => {
    render(<CompSetRoster hotels={null} perf={PERF} note={null} />);
    expect(screen.getAllByTestId('comp-set-hotel')).toHaveLength(5);
    expect(screen.queryByTestId('comp-set-note')).not.toBeInTheDocument();
  });
});

describe('SubmarketSnapshot — one derivation feeds the count and the keys', () => {
  const derived = { occ: 69.186, adr: 295.1168, revpar: 204.2181 };

  it('shows 4 hotels / 344 keys with the closed exclusion, never the 5-hotel rollup', () => {
    render(
      <SubmarketSnapshot
        derivedComp={derived}
        compSet={COMP_SET}
        rosterCount={5}
        compKeyCount={344}
        blend={BLEND}
        demand={DEMAND}
        supply={SUPPLY}
        submarketLabel="Miami Beach"
      />,
    );
    const inventory = screen.getByText('Inventory').parentElement as HTMLElement;
    expect(within(inventory).getByText('4 hotels')).toBeInTheDocument();
    expect(within(inventory).getByText('344 keys in comp set · 1 closed excluded')).toBeInTheDocument();
    expect(screen.queryByText('5 hotels')).not.toBeInTheDocument();
  });

  it('prints the TTM blend methodology from the inputs: formula, document, period, hotels', () => {
    render(
      <SubmarketSnapshot
        derivedComp={derived}
        compSet={COMP_SET}
        rosterCount={5}
        compKeyCount={344}
        blend={BLEND}
        demand={null}
        supply={null}
        submarketLabel={null}
      />,
    );
    const line = screen.getByTestId('ttm-blend-methodology');
    expect(line).toHaveTextContent('Occupancy ÷ MPI, ADR ÷ ARI, RevPAR ÷ RGI');
    expect(line).toHaveTextContent(`Source: ${STR_DOC}`);
    expect(line).toHaveTextContent('Jul 2025 – Jun 2026 (12 months, subject monthly series)');
    expect(line).toHaveTextContent('4 active hotels: Z Ocean Hotel, The Betsy South Beach, The Tony Hotel, Dream South Beach; 344 keys; Blue Moon Hotel closed — excluded');
    expect(line).toHaveTextContent('Fondok applies no weighting of its own');
  });

  it('renders Demand / Supply Growth from the market study with period + document', () => {
    render(
      <SubmarketSnapshot
        derivedComp={derived}
        compSet={COMP_SET}
        rosterCount={5}
        compKeyCount={344}
        blend={BLEND}
        demand={DEMAND}
        supply={SUPPLY}
        submarketLabel={null}
      />,
    );
    const demand = screen.getByText('Demand Growth').parentElement as HTMLElement;
    expect(within(demand).getByText('+4.2%')).toBeInTheDocument();
    expect(within(demand).getByText(`TTM · ${MS_DOC}`)).toBeInTheDocument();
    const supply = screen.getByText('Supply Growth').parentElement as HTMLElement;
    expect(within(supply).getByText('+5.0%')).toBeInTheDocument();
    expect(within(supply).getByText('600 rooms under construction ÷ 12,000 existing · final planning +2.5%')).toBeInTheDocument();
  });

  it('a missing series is a dash with the worker reason, and no methodology line without a blend', () => {
    render(
      <SubmarketSnapshot
        derivedComp={derived}
        compSet={null}
        rosterCount={4}
        compKeyCount={344}
        blend={null}
        demand={NO_SOURCE}
        supply={{ ...SUPPLY, under_construction_rooms: null, under_construction_pct: null, final_planning_pct: null, inputs: [], reason: 'no_source', detail: 'under-construction rooms not in the uploaded reports.' }}
        submarketLabel={null}
      />,
    );
    const demand = screen.getByText('Demand Growth').parentElement as HTMLElement;
    expect(within(demand).getByText('—')).toBeInTheDocument();
    expect(within(demand).getByText('not in the uploaded reports (no_source)')).toBeInTheDocument();
    expect(within(demand).getByLabelText('No matching field')).toBeInTheDocument();
    const supply = screen.getByText('Supply Growth').parentElement as HTMLElement;
    expect(within(supply).getByText('—')).toBeInTheDocument();
    // Older worker (no comp_set block): the fallback count is the roster rows
    // the keys are summed over — 4 — not a rollup.
    const inventory = screen.getByText('Inventory').parentElement as HTMLElement;
    expect(within(inventory).getByText('4 hotels')).toBeInTheDocument();
    expect(screen.queryByTestId('ttm-blend-methodology')).not.toBeInTheDocument();
  });
});

describe('SupplyPipeline — submarket rows from the supply block', () => {
  it('lists inventory, under construction and final planning as rooms + share', () => {
    render(<SupplyPipeline compKeyCount={344} supply={SUPPLY} />);
    expect(screen.getByText('12,000 rooms')).toBeInTheDocument();
    expect(screen.getByText('600 rooms (+5.0%)')).toBeInTheDocument();
    expect(screen.getByText('300 rooms (+2.5%)')).toBeInTheDocument();
    expect(screen.getByText('1,200 rooms')).toBeInTheDocument();
    expect(screen.getByText('+1.2% · TTM')).toBeInTheDocument();
    expect(screen.getByText(`Pipeline shares = rooms ÷ existing submarket inventory. Source: ${MS_DOC}.`)).toBeInTheDocument();
  });

  it('without a market study every submarket row is a dash', () => {
    render(<SupplyPipeline compKeyCount={344} supply={null} />);
    expect(screen.getByText('344 keys')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText(/rooms \(/)).not.toBeInTheDocument();
  });
});

describe('pure helpers', () => {
  it('monthLabel', () => {
    expect(monthLabel('2025-07')).toBe('Jul 2025');
    expect(monthLabel('2026')).toBe('2026');
  });

  it('ttmBlendMethodology falls back to the report year and is null without a blend', () => {
    expect(ttmBlendMethodology(null, COMP_SET)).toBeNull();
    expect(ttmBlendMethodology({ ...BLEND, occupancy_pct: null, adr_usd: null, revpar_usd: null }, COMP_SET)).toBeNull();
    const line = ttmBlendMethodology({ ...BLEND, period_basis: 'report_year', period_start: null, period_end: null, months: null }, null);
    expect(line).toContain('trailing twelve months of the 2026 report');
    expect(line).toContain('comp set: the comp set as reported');
  });

  it('growth tiles: reported vs derived vs refused', () => {
    expect(demandGrowthTile({ ...DEMAND, basis: 'derived_from_series', period_label: '2024→2025' }).sub).toBe(
      `2024→2025 · ${MS_DOC} · derived from the demand series`,
    );
    expect(demandGrowthTile(null).sub).toBe('awaiting CoStar submarket report');
    expect(demandGrowthTile({ ...NO_SOURCE, reason: 'no_document' }).sub).toBe('awaiting CoStar submarket report (no_document)');
    expect(supplyGrowthTile({ ...SUPPLY, final_planning_pct: null }).sub).toBe('600 rooms under construction ÷ 12,000 existing');
    expect(supplyGrowthTile({ ...SUPPLY, under_construction_pct: -0.4 }).value).toBe('−0.4%');
  });
});
