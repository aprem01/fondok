/**
 * useDocuments — per-deal `settled` + `extractionFailures` (FON-41a).
 *
 * The Financials worksheet holds a skeleton on `!settled` instead of a false
 * empty state (Sam QA 9/9: "Run the model…" showed for ~50 s on a complete
 * deal). Contracts locked here on the shared per-deal store:
 *
 *  1. `settled` is false until the first list fetch has completed AND every
 *     EXTRACTED doc's extraction has loaded — then true, for every instance.
 *  2. A doc whose extraction fetch fails 3× is recorded in
 *     `extractionFailures` and stops holding `settled` back.
 *  3. `settled` is LATCHED: a doc that reaches EXTRACTED later just appears
 *     when its record lands — it never flips a rendered grid back to a
 *     skeleton.
 *  4. A failed first list fetch still settles (with `error` set) so nothing
 *     waits forever.
 *  5. A late-mounting instance on a warm store is settled immediately.
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

// The per-deal store is module-level and outlives a mount, so each test gets
// its own deal id (same discipline as useDocuments.test.tsx).
let dealSeq = 0;
let DEAL = 'deal-uuid-settled-0';

function doc(i: number, status = 'EXTRACTED'): WorkerDocument {
  return { id: `doc-${i}`, deal_id: DEAL, tenant_id: 't', filename: `file-${i}.pdf`, doc_type: 'PNL', status } as unknown as WorkerDocument;
}
function extraction(docId: string, status = 'EXTRACTED'): ExtractionResult {
  return { document_id: docId, status, fields: [] } as unknown as ExtractionResult;
}
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

let docs: WorkerDocument[] = [];
/** Extraction fetches resolve only when released — lets a test observe the
 *  "list landed, extractions pending" window. */
let release: (() => void) | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  DEAL = `deal-uuid-settled-${++dealSeq}`;
  docs = [doc(1), doc(2), doc(3)];
  release = null;
  listSpy.mockReset();
  extractionSpy.mockReset();
  listSpy.mockImplementation(async () => docs.map((d) => ({ ...d })));
  extractionSpy.mockImplementation(async (_deal: string, docId: string) => extraction(docId));
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useDocuments — settled + extractionFailures (FON-41a)', () => {
  it('is unsettled until the list AND every EXTRACTED doc’s extraction have landed', async () => {
    const gate = new Promise<void>((r) => { release = r; });
    extractionSpy.mockImplementation(async (_deal: string, docId: string) => { await gate; return extraction(docId); });

    const { result } = renderHook(() => useDocuments(DEAL));
    expect(result.current.settled).toBe(false);
    await flush(); // list lands; 3 extraction fetches are in flight
    expect(result.current.documents).toHaveLength(3);
    expect(result.current.settled).toBe(false);

    act(() => release!());
    await flush();
    expect(Object.keys(result.current.extractions)).toHaveLength(3);
    expect(result.current.settled).toBe(true);
    expect(result.current.extractionFailures).toEqual({});
  });

  it('records a doc whose extraction fetch fails 3× and settles without it', async () => {
    extractionSpy.mockImplementation(async (_deal: string, docId: string) => {
      if (docId === 'doc-2') throw new Error('500');
      return extraction(docId);
    });
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    // Two loaded, one still retrying (3 s, then 6 s backoff) → not settled yet.
    expect(Object.keys(result.current.extractions).sort()).toEqual(['doc-1', 'doc-3']);
    expect(result.current.settled).toBe(false);

    await flush(3_000);
    await flush(6_000);
    await flush(1_000);
    expect(extractionSpy.mock.calls.filter((c) => c[1] === 'doc-2')).toHaveLength(3);
    expect(result.current.extractionFailures).toEqual({ 'doc-2': true });
    expect(result.current.settled).toBe(true);
  });

  it('stays settled (latched) when a doc finishes extracting later, and its record still lands', async () => {
    docs[2] = doc(3, 'EXTRACTING');
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(result.current.settled).toBe(true); // 2 loaded; the 3rd has nothing to fetch yet
    expect(Object.keys(result.current.extractions)).toHaveLength(2);

    docs[2] = doc(3, 'EXTRACTED');
    await flush(2_500); // list poll picks it up → one fetch
    expect(result.current.settled).toBe(true);
    expect(Object.keys(result.current.extractions)).toHaveLength(3);
  });

  it('settles (with error) when the first list fetch fails, so a consumer never waits forever', async () => {
    listSpy.mockImplementation(async () => { throw new Error('worker down'); });
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(result.current.error).toBe('worker down');
    expect(result.current.documents).toEqual([]);
    expect(result.current.settled).toBe(true);
  });

  it('a late-mounting instance on a warm store is settled immediately with zero fetches', async () => {
    const a = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(a.result.current.settled).toBe(true);
    const calls = extractionSpy.mock.calls.length;

    const b = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(b.result.current.settled).toBe(true);
    expect(Object.keys(b.result.current.extractions)).toHaveLength(3);
    expect(extractionSpy.mock.calls.length).toBe(calls);
  });

  it('is settled at once for a mock / offline deal (nothing will ever load)', async () => {
    const { result } = renderHook(() => useDocuments('7'));
    await flush();
    expect(result.current.settled).toBe(true);
    expect(listSpy).not.toHaveBeenCalled();
  });
});
