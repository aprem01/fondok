/**
 * W5 "price" — the deal's provenance map is re-read after a model run and on
 * an explicit refresh, so a just-extracted OM asking price (and its badge)
 * appears without a page reload. The previous payload stays on screen while
 * the re-read is in flight.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, cleanup } from '@testing-library/react';
import React from 'react';

const sourcesSpy = vi.fn();
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, deals: { ...actual.api.deals, assumptionSources: (...a: unknown[]) => sourcesSpy(...a) } },
  };
});

import { ProvenanceProvider, useProvenanceRefresh, useSource } from '@/lib/hooks/useDealProvenance';
import { notifyEngineOutputsChanged } from '@/lib/hooks/useEngineOutputs';

let refreshFn: () => void = () => {};
function Probe() {
  const s = useSource('purchase_price');
  refreshFn = useProvenanceRefresh();
  return <span data-testid="src">{s ? `${s.source}:${String(s.value)}` : 'none'}</span>;
}

const payload = (source: string | null, value: number | null) => ({
  deal_id: 'd1', sources: source ? { purchase_price: source } : {}, values: value ? { purchase_price: value } : {},
});

beforeEach(() => { cleanup(); sourcesSpy.mockReset(); });

describe('ProvenanceProvider re-reads its sources', () => {
  it('after a model run on the deal', async () => {
    sourcesSpy.mockResolvedValueOnce(payload(null, null)).mockResolvedValueOnce(payload('om_broker', 40_000_000));
    render(<ProvenanceProvider dealId="d1"><Probe /></ProvenanceProvider>);
    await waitFor(() => expect(sourcesSpy).toHaveBeenCalledTimes(1));
    act(() => notifyEngineOutputsChanged('d1'));
    await waitFor(() => expect(screen.getByTestId('src')).toHaveTextContent('om_broker:40000000'));
    expect(sourcesSpy).toHaveBeenCalledTimes(2);
  });

  it('on an explicit refresh, keeping the old payload until the new one lands', async () => {
    let release: (v: unknown) => void = () => {};
    sourcesSpy
      .mockResolvedValueOnce(payload('deal_row', 36_400_000))
      .mockReturnValueOnce(new Promise((r) => { release = r; }));
    render(<ProvenanceProvider dealId="d1"><Probe /></ProvenanceProvider>);
    await waitFor(() => expect(screen.getByTestId('src')).toHaveTextContent('deal_row:36400000'));
    act(() => refreshFn());
    await waitFor(() => expect(sourcesSpy).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('src')).toHaveTextContent('deal_row:36400000');
    await act(async () => { release(payload('om_broker', 40_000_000)); });
    await waitFor(() => expect(screen.getByTestId('src')).toHaveTextContent('om_broker:40000000'));
  });
});
