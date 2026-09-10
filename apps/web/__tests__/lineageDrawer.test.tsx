/**
 * LineageDrawer — "walk any number down to the page it came from" (Phase 2.4).
 *
 * Locks the four states an analyst can land in, against a hand-written
 * lineage record (no worker, no fixtures generated from one):
 *
 *  1. LOADED — the walk renders as an ordered chain, root → terminal, with
 *     each step's label, its value with unit, and its source badge.
 *  2. PAGE — a `page` step names the document filename AND the page number.
 *  3. STALE — a stale record says the run predates the latest document or
 *     override.
 *  4. UNRESOLVED — a link the graph could not resolve renders the dash glyph
 *     plus the reason label from REASONS, never an empty row.
 *  5. ENDPOINT ABSENT — a 404 (api → null record) leaves the host component
 *     rendering exactly what it renders today; only the drawer says so.
 *
 * Plus the on-demand contract: nothing is fetched until the drawer opens.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { LineageRecord } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn() }),
}));

// ── the fixture: Levered IRR ← equity cash flows ← the T-12 rooms line ──
// ← Kimpton_T12_2025.pdf, page 4. Plus one link the graph cannot resolve
// (the exit cap has no appraisal on the deal).
const fx = vi.hoisted(() => {
  const RECORD = {
    deal_id: 'deal-uuid-1',
    run_id: 'run-7',
    registry_version: 3,
    pipeline_version: '2026.09.1',
    generated_at: '2026-09-08T12:00:00Z',
    roots: ['kpi:returns.levered_irr'],
    nodes: [
      {
        id: 'kpi:returns.levered_irr',
        kind: 'kpi',
        label: 'Levered IRR',
        value: 26.12,
        unit: 'percent',
        concept: 'levered_irr',
        source: null,
        state: 'calculated',
        reason: null,
        meta: {},
      },
      {
        id: 'engine:returns.equity_cash_flows',
        kind: 'engine_value',
        label: 'Equity cash flows',
        value: 17_250_000,
        unit: 'USD',
        concept: 'equity_cash_flows',
        source: null,
        state: 'calculated',
        reason: null,
        meta: {},
      },
      {
        id: 'field:er-9:rooms_revenue',
        kind: 'extracted_field',
        label: 'Rooms revenue',
        value: 12_300_000,
        unit: 'USD',
        concept: 'rooms_revenue',
        source: 't12_actual',
        state: 'document_sourced',
        reason: null,
        meta: {},
      },
      {
        id: 'doc:doc-88',
        kind: 'document',
        label: 'Kimpton_T12_2025.pdf',
        value: null,
        unit: null,
        concept: null,
        source: 't12_actual',
        state: 'document_sourced',
        reason: null,
        meta: { filename: 'Kimpton_T12_2025.pdf' },
      },
      {
        id: 'page:doc-88:4',
        kind: 'page',
        label: 'Statement of Operations',
        value: null,
        unit: null,
        concept: null,
        source: 't12_actual',
        state: 'document_sourced',
        reason: null,
        meta: { filename: 'Kimpton_T12_2025.pdf', page: 4 },
      },
    ],
    edges: [
      {
        src: 'kpi:returns.levered_irr',
        dst: 'engine:returns.equity_cash_flows',
        rel: 'computed_from',
        formula: 'levered_irr = xirr(equity_cash_flows)',
      },
      {
        src: 'engine:returns.equity_cash_flows',
        dst: 'field:er-9:rooms_revenue',
        rel: 'normalized_from',
        formula: null,
      },
      { src: 'field:er-9:rooms_revenue', dst: 'doc:doc-88', rel: 'extracted_from', formula: null },
      { src: 'field:er-9:rooms_revenue', dst: 'page:doc-88:4', rel: 'located_on', formula: null },
      // A link with no node — the exit cap the graph could not resolve.
      {
        src: 'kpi:returns.levered_irr',
        dst: 'assumption:exit_cap_rate',
        rel: 'computed_from',
        formula: null,
      },
    ],
    unresolved: [
      {
        code: 'no_document',
        concept: 'assumption:exit_cap_rate',
        detail: 'No appraisal or valuation is on the deal.',
      },
    ],
    stale: false,
  } as unknown as LineageRecord;
  return {
    RECORD,
    served: null as LineageRecord | null,
    spy: vi.fn(),
  };
});

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        lineage: async (id: string) => {
          fx.spy(id);
          return fx.served;
        },
      },
    },
  };
});

import { LineageDrawer, LineageDrawerHost } from '@/components/project/LineageDrawer';
import { clearLineageCache } from '@/lib/hooks/useLineage';
import { Sourced } from '@/components/help/Sourced';

const ROOT = 'kpi:returns.levered_irr';

function stepIds(): string[] {
  return Array.from(document.querySelectorAll('[data-testid^="lineage-step-"]')).map(
    (el) => el.getAttribute('data-testid') ?? '',
  );
}

beforeEach(() => {
  clearLineageCache();
  fx.spy.mockClear();
  fx.served = fx.RECORD;
});
afterEach(cleanup);

describe('LineageDrawer — loaded: the chain renders in order', () => {
  it('walks the root down to its terminal nodes, in edge order', async () => {
    render(
      <LineageDrawer
        open
        dealId="deal-uuid-1"
        rootId={ROOT}
        title="Levered IRR"
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(stepIds().length).toBeGreaterThan(1));

    expect(stepIds()).toEqual([
      'lineage-step-kpi:returns.levered_irr',
      'lineage-step-engine:returns.equity_cash_flows',
      'lineage-step-field:er-9:rooms_revenue',
      'lineage-step-doc:doc-88',
      'lineage-step-page:doc-88:4',
      'lineage-step-assumption:exit_cap_rate',
    ]);

    // …and the visible labels sit in that same order.
    const labels = Array.from(
      document.querySelectorAll('[data-testid^="lineage-step-"]'),
    ).map((el) => el.querySelector('.truncate')?.textContent ?? '');
    expect(labels).toEqual([
      'Levered IRR',
      'Equity cash flows',
      'Rooms revenue',
      'Kimpton_T12_2025.pdf',
      'Statement of Operations',
      'exit_cap_rate',
    ]);
  });

  it('shows each step’s value with its unit, its source badge and the formula', async () => {
    render(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);
    await waitFor(() => expect(stepIds().length).toBeGreaterThan(1));

    const kpi = within(screen.getByTestId('lineage-step-kpi:returns.levered_irr'));
    expect(kpi.getByText('26.1%')).toBeInTheDocument();

    const engine = within(screen.getByTestId('lineage-step-engine:returns.equity_cash_flows'));
    expect(engine.getByText('$17.25M')).toBeInTheDocument();
    expect(engine.getByText('Computed from')).toBeInTheDocument();
    expect(engine.getByText('levered_irr = xirr(equity_cash_flows)')).toBeInTheDocument();

    const field = within(screen.getByTestId('lineage-step-field:er-9:rooms_revenue'));
    expect(field.getByText('$12.30M')).toBeInTheDocument();
    // The source badge — the same label the Provenance Ledger prints.
    expect(field.getByText('T-12 actual')).toBeInTheDocument();
    expect(field.getByText('Normalized from')).toBeInTheDocument();
  });

  it('does not fetch until the drawer opens (on demand, not on mount)', async () => {
    const { rerender } = render(
      <LineageDrawer open={false} dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />,
    );
    expect(fx.spy).not.toHaveBeenCalled();

    rerender(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);
    await waitFor(() => expect(fx.spy).toHaveBeenCalledTimes(1));
  });
});

describe('LineageDrawer — a page node names the document and the page', () => {
  it('renders the filename and the page number on the page step', async () => {
    render(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);
    await waitFor(() => expect(stepIds().length).toBeGreaterThan(1));

    const page = within(screen.getByTestId('lineage-step-page:doc-88:4'));
    expect(page.getByText('Located on')).toBeInTheDocument();
    expect(page.getByText('Kimpton_T12_2025.pdf')).toBeInTheDocument();
    expect(page.getByText('Page 4')).toBeInTheDocument();
    expect(page.getByText('Open source document →')).toBeInTheDocument();
  });
});

describe('LineageDrawer — a stale record says so', () => {
  it('shows the notice that the run predates the latest document or override', async () => {
    fx.served = { ...fx.RECORD, stale: true };
    render(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);

    expect(
      await screen.findByText(/predates the latest document or override/i),
    ).toBeInTheDocument();
  });

  it('shows no notice when the record is current', async () => {
    render(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);
    await waitFor(() => expect(stepIds().length).toBeGreaterThan(1));
    expect(screen.queryByText(/predates the latest document or override/i)).toBeNull();
  });
});

describe('LineageDrawer — an unresolved link is a dash with its reason', () => {
  it('renders the refusal glyph and the REASONS label instead of an empty row', async () => {
    render(<LineageDrawer open dealId="deal-uuid-1" rootId={ROOT} onClose={() => {}} />);
    await waitFor(() => expect(stepIds().length).toBeGreaterThan(1));

    const dangling = within(screen.getByTestId('lineage-step-assumption:exit_cap_rate'));
    // The dash glyph stands in for the value…
    expect(dangling.getByLabelText('No value')).toHaveTextContent('—');
    // …and it is labelled with the reason, never left blank.
    expect(dangling.getByText('No source document')).toBeInTheDocument();
    expect(dangling.getByText('No appraisal or valuation is on the deal.')).toBeInTheDocument();

    // The record-level refusal is also collected under "Unresolved".
    expect(screen.getByText('Unresolved')).toBeInTheDocument();
    expect(screen.getAllByText('No source document').length).toBeGreaterThan(1);
  });
});

describe('LineageDrawer — the endpoint is absent (404)', () => {
  it('leaves the host component exactly as it renders today', async () => {
    fx.served = null; // what api.deals.lineage resolves on a 404

    render(
      <>
        <Sourced source="t12_actual" sourceKey="rooms_revenue" docId="doc-88">
          $12,300,000
        </Sourced>
        <LineageDrawerHost />
      </>,
    );

    const value = screen.getByText('$12,300,000');
    fireEvent.mouseEnter(value.parentElement as HTMLElement);

    // Today's hover content — untouched.
    expect(screen.getByText('T-12 actual')).toBeInTheDocument();
    expect(screen.getByText('View source document →')).toBeInTheDocument();
    expect(screen.getByText('Full trace in Analysis → Sources')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Trace to source →'));

    // The drawer degrades quietly — no error, no thrown render.
    expect(
      await screen.findByText(/No lineage recorded for this deal yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t load lineage/i)).toBeNull();
    expect(document.querySelectorAll('[data-testid^="lineage-step-"]').length).toBe(0);

    // …and the host still renders its number and its own affordances.
    expect(screen.getByText('$12,300,000')).toBeInTheDocument();
    expect(screen.getByText('T-12 actual')).toBeInTheDocument();
    expect(screen.getByText('View source document →')).toBeInTheDocument();
  });

  it('turns a 404 from the worker into a null record rather than an error', async () => {
    const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
    const fetchSpy = vi.fn(async () =>
      new Response('not found', { status: 404 }),
    );
    const original = global.fetch;
    global.fetch = fetchSpy as unknown as typeof fetch;
    try {
      await expect(actual.api.deals.lineage('deal-uuid-1')).resolves.toBeNull();
    } finally {
      global.fetch = original;
    }
  });
});
