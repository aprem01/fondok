/**
 * Worksheet layout lives ON THE DEAL, not on the device (FON-41, decision 6).
 *
 * A reordered statement is a deal artifact an IC reviewer must see, not a
 * per-browser preference, so `useWorksheetLayout` persists to
 * `field_overrides.worksheet_layout` instead of `localStorage`.
 *
 * Contracts locked here:
 *
 *  1. A REORDER PERSISTS TO THE DEAL. It survives a remount on a browser whose
 *     `localStorage` has been wiped — the order comes back off the deal row.
 *
 *  2. A PRE-EXISTING LOCAL LAYOUT IS MIGRATED UP ONCE. A layout built before the
 *     lift (device-local only) is adopted on the next load, written up in a
 *     single PATCH, and never silently discarded.
 *
 *  3. A FAILED WRITE IS VISIBLE. When the PATCH is rejected the analyst is told
 *     ("Layout not saved" + Retry + an error toast) rather than being left to
 *     believe a reorder saved when it did not.
 *
 *  4. THE PAYLOAD CARRIES ONLY LAYOUT KEYS. The PATCH touches exactly one
 *     `field_overrides` key (`worksheet_layout`), whose own keys are exactly the
 *     six layout fields; every other override on the deal is carried through
 *     untouched. Nothing on this path can become engine input.
 *
 *  5. FON-74 — AND THEREFORE IT NEEDS NO JUSTIFICATION. `worksheet_layout` is
 *     the one key in the worker's `_OVERRIDE_NON_ENGINE_KEYS`: presentation
 *     only. A reorder must not prompt for a reason and must not be rejected by
 *     the API gate. The worksheet's VALUE cells, which are engine input, must.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent, within, act } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { LIVE_DEAL_ID } from './helpers/fon41LiveFixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: LIVE_DEAL_ID_CONST }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));
// vi.mock factories are hoisted above the imports, so the id is inlined.
const LIVE_DEAL_ID_CONST = 'e577f547-a3cd-4e78-9ee1-8d761b0c4777';

/** The deal row the worker would return — mutated by a successful PATCH. */
const DEAL: { id: string; keys: number; field_overrides: Record<string, unknown> } = {
  id: LIVE_DEAL_ID_CONST,
  keys: 132,
  field_overrides: {},
};

/** Set to make the next PATCH reject (the "worker rejected it" path). */
let failWrites = false;

const updateSpy = vi.fn(async (_id: string, patch: Record<string, unknown>) => {
  if (failWrites) throw new Error('worker rejected update');
  DEAL.field_overrides = { ...(patch.field_overrides as Record<string, unknown>) };
  return DEAL;
});
const toastSpy = vi.fn();

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
      deals: {
        update: (...a: unknown[]) => updateSpy(...(a as [string, Record<string, unknown>])),
        get: vi.fn(),
        status: vi.fn(),
      },
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
  useDeal: () => ({ deal: DEAL, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: toastSpy }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';
import { WORKSHEET_LAYOUT_FIELDS } from '@/lib/hooks/useWorksheetLayout';

const LOCAL_KEY = `fondok:wslayout:${LIVE_DEAL_ID_CONST}`;
const grid = () => document.querySelector('table') as HTMLElement;
const rowLabels = () =>
  Array.from(grid().querySelectorAll('tbody tr')).map((tr) => {
    const input = tr.querySelector('td input') as HTMLInputElement | null;
    return input ? input.value : (tr.querySelector('td')?.textContent ?? '').trim();
  });
const customizeToggle = () => screen.getByRole('button', { name: /Customize structure/ });

/** The first structure-editable row (it is the one we move). */
function firstEditableRow(): HTMLElement {
  for (const tr of Array.from(grid().querySelectorAll('tbody tr')) as HTMLElement[]) {
    if (tr.querySelector('td input')) return tr;
  }
  throw new Error('no editable row in the grid');
}

async function mountWorksheet() {
  render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
  await waitFor(() => expect(grid()).toBeTruthy(), { timeout: 8000 });
}

/** The `field_overrides` patch of the nth (default last) recorded PATCH. */
function patchAt(i = -1): Record<string, unknown> {
  const calls = updateSpy.mock.calls;
  const call = calls.at(i);
  if (!call) throw new Error('no PATCH recorded');
  return call[1];
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
  DEAL.field_overrides = {};
  failWrites = false;
  updateSpy.mockClear();
  toastSpy.mockClear();
});
afterEach(cleanup);

describe('Worksheet layout — the deal is the store, not the browser', () => {
  it('a reorder survives a remount on a browser with no local layout', async () => {
    await mountWorksheet();
    fireEvent.click(customizeToggle());

    const canonical = rowLabels();
    fireEvent.click(within(firstEditableRow()).getByRole('button', { name: 'Move down' }));
    await waitFor(() => expect(rowLabels()).not.toEqual(canonical));
    const reordered = rowLabels();

    // The reorder reached the deal row (one debounced PATCH).
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });
    expect(DEAL.field_overrides.worksheet_layout).toBeTruthy();

    // A different browser: nothing in localStorage, same deal row.
    cleanup();
    window.localStorage.clear();
    await mountWorksheet();
    expect(rowLabels()).toEqual(reordered);
    expect(rowLabels()).not.toEqual(canonical);
  }, 20000);

  it('coalesces a burst of moves into a single PATCH', async () => {
    await mountWorksheet();
    fireEvent.click(customizeToggle());

    const canonical = rowLabels();
    for (let i = 0; i < 3; i += 1) {
      fireEvent.click(within(firstEditableRow()).getByRole('button', { name: 'Move down' }));
    }
    await waitFor(() => expect(rowLabels()).not.toEqual(canonical));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });
    // Three drags, one round-trip — the debounce, not three PATCHes.
    await act(async () => { await new Promise((r) => setTimeout(r, 900)); });
    expect(updateSpy).toHaveBeenCalledTimes(1);
  }, 20000);

  it('migrates a pre-existing device-local layout up exactly once', async () => {
    // Build a genuine local layout the way the old device-local hook did.
    await mountWorksheet();
    fireEvent.click(customizeToggle());
    fireEvent.click(screen.getAllByRole('button', { name: /Add line/ })[0]);
    await screen.findByDisplayValue('New line');
    await waitFor(() => expect(window.localStorage.getItem(LOCAL_KEY)).toBeTruthy());
    const legacyLocal = window.localStorage.getItem(LOCAL_KEY) as string;

    // Rewind the world to before the lift: the layout exists only on this
    // device; the deal row has never seen one.
    cleanup();
    DEAL.field_overrides = {};
    updateSpy.mockClear();
    window.localStorage.setItem(LOCAL_KEY, legacyLocal);

    await mountWorksheet();
    // Not discarded — the analyst's line is still there…
    // (Customize is off after a remount, so the analyst line reads as text.)
    expect(screen.getByText('New line')).toBeInTheDocument();
    // …and it was written UP, once.
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });
    await act(async () => { await new Promise((r) => setTimeout(r, 900)); });
    expect(updateSpy).toHaveBeenCalledTimes(1);
    const migrated = DEAL.field_overrides.worksheet_layout as { curated: { label: string }[] };
    expect(migrated.curated.map((c) => c.label)).toEqual(['New line']);

    // A later load reads the deal, not the device, and does not re-migrate.
    cleanup();
    updateSpy.mockClear();
    window.localStorage.clear();
    await mountWorksheet();
    // (Customize is off after a remount, so the analyst line reads as text.)
    expect(screen.getByText('New line')).toBeInTheDocument();
    await act(async () => { await new Promise((r) => setTimeout(r, 900)); });
    expect(updateSpy).not.toHaveBeenCalled();
  }, 25000);
});

describe('Worksheet layout — a failed write is never mistaken for a saved one', () => {
  it('surfaces "Layout not saved" with a Retry, and an error toast', async () => {
    failWrites = true;
    await mountWorksheet();
    fireEvent.click(customizeToggle());

    const canonical = rowLabels();
    fireEvent.click(within(firstEditableRow()).getByRole('button', { name: 'Move down' }));
    await waitFor(() => expect(rowLabels()).not.toEqual(canonical));

    const chip = await screen.findByText(/Layout not saved/, {}, { timeout: 4000 });
    expect(chip).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(toastSpy).toHaveBeenCalledWith(
      expect.stringContaining('Layout not saved'),
      expect.objectContaining({ type: 'error' }),
    );
    // Nothing reached the deal.
    expect(DEAL.field_overrides.worksheet_layout).toBeUndefined();

    // Retry once the worker is healthy again clears the warning and saves.
    failWrites = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(DEAL.field_overrides.worksheet_layout).toBeTruthy(), { timeout: 4000 });
    await waitFor(() => expect(screen.queryByText(/Layout not saved/)).toBeNull());
  }, 20000);
});

describe('Worksheet layout — the payload can never become engine input', () => {
  it('writes only worksheet_layout, and only the six layout keys', async () => {
    // A real engine override already on the deal must survive untouched.
    DEAL.field_overrides = { purchase_price: 42_000_000 };
    await mountWorksheet();
    fireEvent.click(customizeToggle());

    const canonical = rowLabels();
    fireEvent.click(within(firstEditableRow()).getByRole('button', { name: 'Move down' }));
    await waitFor(() => expect(rowLabels()).not.toEqual(canonical));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled(), { timeout: 4000 });

    const patch = patchAt();
    // The PATCH body carries nothing but field_overrides.
    expect(Object.keys(patch)).toEqual(['field_overrides']);
    const fo = patch.field_overrides as Record<string, unknown>;
    // …exactly one NEW key, and the pre-existing override is carried through.
    expect(Object.keys(fo).sort()).toEqual(['purchase_price', 'worksheet_layout']);
    expect(fo.purchase_price).toBe(42_000_000);
    // …and the layout blob itself is only the six presentation fields.
    const layout = fo.worksheet_layout as Record<string, unknown>;
    expect(Object.keys(layout).sort()).toEqual([...WORKSHEET_LAYOUT_FIELDS].sort());
    expect(layout.v).toBe(1);
    // FON-74 — and it is a bare layout blob, not a `{value, note}` envelope:
    // a presentation key is never asked to justify itself.
    expect('note' in layout).toBe(false);
    expect(screen.queryByLabelText('Override justification')).not.toBeInTheDocument();
  }, 20000);
});
