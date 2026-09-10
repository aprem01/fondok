/**
 * ProvenanceLedger — the two additive lineage columns (Phase 2.4).
 *
 * "Field / page" comes from `source_fields` and "Reason" from `reasons` on
 * GET /deals/{id}/assumption_sources. Both blocks are OPTIONAL: a worker
 * build that predates lineage sends neither, and the ledger must then render
 * exactly the table it rendered before — same columns, same values.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { AssumptionSourcesResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

const fx = vi.hoisted(() => ({ served: null as unknown }));

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        assumptionSources: async () => fx.served,
      },
    },
  };
});

import { ProvenanceLedger } from '@/components/project/ProvenanceLedger';

const BASE = {
  id: 'deal-uuid-1',
  sources: { rooms_revenue_usd: 't12_actual', exit_cap_rate: 'seed' },
  values: { rooms_revenue_usd: 12_300_000, exit_cap_rate: 0.0725 },
  source_documents: { rooms_revenue_usd: 'doc-88' },
} as unknown as AssumptionSourcesResponse;

const WITH_LINEAGE = {
  ...BASE,
  source_fields: {
    rooms_revenue_usd: {
      field: 'Rooms revenue',
      page: 4,
      document_id: 'doc-88',
      filename: 'Kimpton_T12_2025.pdf',
    },
  },
  reasons: { exit_cap_rate: 'no_document' },
} as unknown as AssumptionSourcesResponse;

function rowFor(label: string): HTMLElement {
  const cell = screen.getByText(label);
  const tr = cell.closest('tr');
  if (!tr) throw new Error(`no row for ${label}`);
  return tr as HTMLElement;
}

beforeEach(() => {
  fx.served = BASE;
});
afterEach(cleanup);

describe('ProvenanceLedger — without the new blocks', () => {
  it('renders today’s table: no "Field / page", no "Reason"', async () => {
    render(<ProvenanceLedger dealId="deal-uuid-1" />);
    expect(await screen.findByText('Provenance ledger')).toBeInTheDocument();

    // Today's four columns, unchanged.
    expect(screen.getByText('Assumption')).toBeInTheDocument();
    expect(screen.getByText('Value')).toBeInTheDocument();
    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Doc')).toBeInTheDocument();

    // The additive ones stay away entirely when the worker sent no block.
    expect(screen.queryByText('Field / page')).toBeNull();
    expect(screen.queryByText('Reason')).toBeNull();

    // …and the rows still read as they always did.
    expect(within(rowFor('Rooms Revenue')).getByText('T-12 actual')).toBeInTheDocument();
    expect(within(rowFor('Rooms Revenue')).getByText('View')).toBeInTheDocument();
  });
});

describe('ProvenanceLedger — with the new blocks', () => {
  it('adds "Field / page" and "Reason", dashing the rows the worker did not tag', async () => {
    fx.served = WITH_LINEAGE;
    render(<ProvenanceLedger dealId="deal-uuid-1" />);
    expect(await screen.findByText('Provenance ledger')).toBeInTheDocument();

    expect(screen.getByText('Field / page')).toBeInTheDocument();
    expect(screen.getByText('Reason')).toBeInTheDocument();

    // The grounded row names the field it was read off, and its page.
    const rooms = within(rowFor('Rooms Revenue'));
    expect(rooms.getByText('Rooms revenue · p.4')).toBeInTheDocument();

    // The ungrounded row says why it is not grounded — the REASONS label.
    const cap = within(rowFor('Exit Cap Rate'));
    expect(cap.getByText('No source document')).toBeInTheDocument();

    // Each row dashes the column it has nothing for — never a blank cell.
    const roomsCells = Array.from(rowFor('Rooms Revenue').querySelectorAll('td'));
    expect(roomsCells[5]?.textContent).toBe('—'); // Reason
    const capCells = Array.from(rowFor('Exit Cap Rate').querySelectorAll('td'));
    expect(capCells[4]?.textContent).toBe('—'); // Field / page
  });

  it('shows a column as soon as EITHER block carries a value', async () => {
    fx.served = { ...BASE, reasons: { exit_cap_rate: 'no_document' } } as unknown as AssumptionSourcesResponse;
    render(<ProvenanceLedger dealId="deal-uuid-1" />);
    expect(await screen.findByText('Provenance ledger')).toBeInTheDocument();

    expect(screen.getByText('Reason')).toBeInTheDocument();
    expect(screen.queryByText('Field / page')).toBeNull();
  });
});
