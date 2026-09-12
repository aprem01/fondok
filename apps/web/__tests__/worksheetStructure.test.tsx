/**
 * Financials → Historicals — structure editing is an affordance again (FON-41 §3).
 *
 * Sam, 2026-09-11: "I could not add or reorder rows in the worksheet."
 * Everything was built and wired — add line, move up/down, rename, hide,
 * split, reset — behind one hard-coded constant:
 *
 *     // Design rewire: structure-editing (Customize) removed from Historicals
 *     const customize = false;
 *
 * The contracts locked here:
 *   1. "Customize structure" reveals add + move, and "Reset layout" appears
 *      once the layout is customized.
 *   2. A manually added row is an ANALYST line — assumption dot + "Analyst
 *      line" chip, never a green document-sourced dot. It is presentation only:
 *      the layout persists to the deal (FON-41 — an IC reviewer must see the
 *      same statement), but the ONLY `field_overrides` key it ever touches is
 *      `worksheet_layout`; no engine input is written.
 *   3. Reordering a SOURCED row preserves its lineage: the SOURCE panel still
 *      names the same statement afterwards (the layout stores row ids).
 *   4. Reset restores the canonical order.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { LIVE_DEAL_ID } from './helpers/fon41LiveFixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

const updateSpy = vi.fn(async () => ({}));
vi.mock('@/lib/api', async () => {
  const fx = await import('./helpers/fon41LiveFixture');
  return {
    isWorkerConnected: () => true,
    workerUrl: () => 'http://worker.test',
    api: {
      documents: {
        list: async () => fx.LIVE_DOCS,
        extraction: async (_deal: string, docId: string) => {
          const r = fx.LIVE_EXTRACTIONS[docId];
          if (!r) throw new Error(`no extraction for ${docId}`);
          return r;
        },
        reviewField: vi.fn(async () => ({})),
      },
      deals: { update: (...a: unknown[]) => updateSpy(...(a as [])), get: vi.fn(), status: vi.fn() },
    },
  };
});

const OUTPUTS = {
  deal_id: LIVE_DEAL_ID,
  engines: {
    expense: {
      deal_id: LIVE_DEAL_ID, engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
  },
} as unknown as EngineOutputsResponse;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777', keys: 132, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const SOURCE_DOT = 'See where this came from';
const grid = () => document.querySelector('table') as HTMLElement;
const rowLabels = () =>
  Array.from(grid().querySelectorAll('tbody tr')).map((tr) => {
    const input = tr.querySelector('td input') as HTMLInputElement | null;
    return input ? input.value : (tr.querySelector('td')?.textContent ?? '').trim();
  });
const customizeToggle = () => screen.getByRole('button', { name: /Customize structure/ });

/**
 * The layout persists to the deal now, so a structure edit DOES PATCH. What it
 * may never do is write anything else: every call must carry exactly one
 * `field_overrides` key, `worksheet_layout`.
 */
function expectOnlyLayoutWrites() {
  for (const call of updateSpy.mock.calls as unknown as [string, Record<string, unknown>][]) {
    const patch = call[1];
    expect(Object.keys(patch)).toEqual(['field_overrides']);
    const fo = patch.field_overrides as Record<string, unknown>;
    expect(Object.keys(fo)).toEqual(['worksheet_layout']);
  }
}

/** The first structure-editable row that also carries an extracted value. */
function firstSourcedEditableRow(): { tr: HTMLElement; label: string } {
  for (const tr of Array.from(grid().querySelectorAll('tbody tr')) as HTMLElement[]) {
    const input = tr.querySelector('td input') as HTMLInputElement | null;
    if (!input) continue;
    if (within(tr).queryAllByRole('button', { name: SOURCE_DOT }).length === 0) continue;
    return { tr, label: input.value };
  }
  throw new Error('no sourced, editable row in the grid');
}

/** Open the first cell's SOURCE panel on a row and read the document it names. */
function sourceDocOf(tr: HTMLElement): string {
  fireEvent.click(within(tr).getAllByRole('button', { name: SOURCE_DOT })[0]);
  const panel = document.querySelector('.fixed.inset-0') as HTMLElement;
  const name = within(panel).getByText(/\.xls[xm]$/).textContent ?? '';
  fireEvent.click(within(panel).getAllByRole('button')[0]); // close (X)
  return name;
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
  updateSpy.mockClear();
});
afterEach(cleanup);

describe('Historicals — Customize structure reveals the editing affordances', () => {
  it('hides add / move until Customize is on, then shows them', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(grid()).toBeTruthy(), { timeout: 8000 });

    expect(screen.queryByRole('button', { name: /Add line/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Move up' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Reset layout/ })).toBeNull();

    fireEvent.click(customizeToggle());
    expect(screen.getAllByRole('button', { name: /Add line/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: 'Move up' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: 'Move down' }).length).toBeGreaterThan(0);
    // …and it says what structure editing is (and is not).
    expect(screen.getByText(/never what the model computes/)).toBeInTheDocument();
  }, 12000);
});

describe('Historicals — a manually added row is an analyst line, not a document line', () => {
  it('renders the assumption dot and an "Analyst line" chip, never a document dot', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(grid()).toBeTruthy(), { timeout: 8000 });
    fireEvent.click(customizeToggle());

    fireEvent.click(screen.getAllByRole('button', { name: /Add line/ })[0]);
    const added = await screen.findByDisplayValue('New line');
    const tr = added.closest('tr') as HTMLElement;

    expect(within(tr).getByText('Analyst line')).toBeInTheDocument();
    expect(within(tr).getAllByRole('img', { name: 'Assumption' }).length).toBeGreaterThan(0);
    expect(within(tr).queryByRole('img', { name: 'Document sourced' })).toBeNull();
    expect(within(tr).queryByRole('img', { name: 'Needs review' })).toBeNull();
    // Presentation only — the layout is saved on the deal, but the ONLY key it
    // writes is `worksheet_layout`; no engine input moves.
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });
    expectOnlyLayoutWrites();
  }, 12000);
});

describe('Historicals — reordering a sourced row keeps its lineage', () => {
  it('still names the same statement in the SOURCE panel after a move', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(grid()).toBeTruthy(), { timeout: 8000 });
    fireEvent.click(customizeToggle());

    const { tr, label } = firstSourcedEditableRow();
    const before = sourceDocOf(tr);
    expect(before).toMatch(/\.xls[xm]$/);
    const orderBefore = rowLabels();

    fireEvent.click(within(tr).getByRole('button', { name: 'Move down' }));
    await waitFor(() => expect(rowLabels()).not.toEqual(orderBefore));

    const moved = (screen.getByDisplayValue(label).closest('tr')) as HTMLElement;
    expect(sourceDocOf(moved)).toBe(before);
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });
    expectOnlyLayoutWrites();
  }, 12000);

  it('Reset layout restores the canonical order and drops the added line', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(grid()).toBeTruthy(), { timeout: 8000 });
    fireEvent.click(customizeToggle());

    const canonical = rowLabels();
    const { tr, label } = firstSourcedEditableRow();
    fireEvent.click(within(tr).getByRole('button', { name: 'Move down' }));
    fireEvent.click(screen.getAllByRole('button', { name: /Add line/ })[0]);
    await waitFor(() => expect(screen.queryByDisplayValue('New line')).toBeInTheDocument());
    expect(rowLabels()).not.toEqual(canonical);

    fireEvent.click(screen.getByRole('button', { name: /Reset layout/ }));
    await waitFor(() => expect(screen.queryByDisplayValue('New line')).toBeNull());
    expect(rowLabels()).toEqual(canonical);
    expect(screen.getByDisplayValue(label)).toBeInTheDocument();
  }, 12000);
});
