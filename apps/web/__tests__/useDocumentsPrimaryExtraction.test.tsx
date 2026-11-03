/**
 * FON-22 R2-I1 — the statement the model reads keeps its extraction on the
 * page while it re-extracts or after its re-extract failed: the store fetches
 * the extraction of a document whose worker role is a period's chosen
 * statement whatever its list status (the worker serves the last extraction),
 * once — never on every poll — and never for a supplemental one.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { WorkerDocument, ExtractionResult } from '@/lib/api';

const listSpy = vi.fn();
const extractionSpy = vi.fn();

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      documents: {
        ...actual.api.documents,
        list: (...a: unknown[]) => listSpy(...a),
        extraction: (...a: unknown[]) => extractionSpy(...a),
      },
    },
  };
});

import { useDocuments } from '@/lib/hooks/useDocuments';

let seq = 0;
let DEAL = 'deal-uuid-primary-0';

function doc(id: string, status: string, statement_role: string | null): WorkerDocument {
  return { id, deal_id: DEAL, tenant_id: 't', filename: `${id}.xlsx`, doc_type: 'PNL', status, statement_role } as unknown as WorkerDocument;
}

async function flush(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  vi.useFakeTimers();
  DEAL = `deal-uuid-primary-${++seq}`;
  listSpy.mockReset();
  extractionSpy.mockReset();
  extractionSpy.mockImplementation(
    async (_d: string, docId: string) =>
      ({ document_id: docId, status: 'FAILED', fields: [{ field_name: 'rooms_revenue', value: 1 }] }) as unknown as ExtractionResult,
  );
});

afterEach(() => { vi.useRealTimers(); });

describe('useDocuments — the chosen statement’s last extraction', () => {
  it('fetches a FAILED year_one_primary once and holds it across polls; a FAILED supplemental is not fetched', async () => {
    listSpy.mockImplementation(async () => [doc('p', 'FAILED', 'year_one_primary'), doc('s', 'FAILED', 'supplemental')]);
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    await flush(10_000);
    const calls = extractionSpy.mock.calls.map((c) => c[1]);
    expect(calls).toContain('p');
    expect(calls).not.toContain('s');
    expect(result.current.extractions.p?.fields?.length).toBe(1);
    expect(result.current.settled).toBe(true);
  });

  it('an EXTRACTING period_primary is fetched once, not on every poll', async () => {
    listSpy.mockImplementation(async () => [doc('p', 'EXTRACTING', 'period_primary')]);
    extractionSpy.mockImplementation(
      async (_d: string, docId: string) =>
        ({ document_id: docId, status: 'EXTRACTING', fields: [{ field_name: 'rooms_revenue', value: 1 }] }) as unknown as ExtractionResult,
    );
    renderHook(() => useDocuments(DEAL));
    await flush();
    for (let i = 0; i < 10; i++) await flush(2_500);
    expect(extractionSpy.mock.calls.filter((c) => c[1] === 'p').length).toBe(1);
  });
});
