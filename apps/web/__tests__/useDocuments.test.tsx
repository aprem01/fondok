/**
 * useDocuments — extraction fetch discipline (production finding, 2026-09-10).
 *
 * On a 17-document deal the browser issued 69 extraction fetches in 40 s even
 * though every document was already EXTRACTED: each mounted ``useDocuments``
 * (the deal header, SourceDocPane, PLTab, PLReviewSection, GroundedWorksheet …
 * five on the Financials tab) held its own extraction map and re-fetched all
 * 17 as its own list call landed. Contracts locked here:
 *
 *  1. 17 EXTRACTED docs → exactly 17 extraction fetches (one per doc), and
 *     NONE afterwards across list re-polls / time.
 *  2. Additional mounted instances on the same deal add ZERO fetches — they
 *     read the shared per-deal store and still see all 17 results.
 *  3. An EXTRACTED doc is never re-fetched merely because the list re-polled;
 *     a doc is fetched once when it reaches EXTRACTED; the list poll stops
 *     once every doc is terminal.
 *  4. ``refreshExtraction`` (FON-23) still force-fetches and broadcasts the
 *     fresh result to every instance (FON-41a).
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

// The per-deal store is module-level and deliberately outlives a mount (a
// tab that re-opens must not re-fetch), so each test gets its own deal id.
let dealSeq = 0;
let DEAL = 'deal-uuid-docs-0';

function doc(i: number, status = 'EXTRACTED'): WorkerDocument {
  return {
    id: `doc-${i}`,
    deal_id: DEAL,
    tenant_id: 't',
    filename: `file-${i}.pdf`,
    doc_type: 'T12',
    status,
  } as unknown as WorkerDocument;
}

function extraction(docId: string, status = 'EXTRACTED'): ExtractionResult {
  return { document_id: docId, status, fields: [] } as unknown as ExtractionResult;
}

/** Flush microtasks + any timers due now (fake-timer safe). */
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

let docs: WorkerDocument[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  DEAL = `deal-uuid-docs-${++dealSeq}`;
  docs = Array.from({ length: 17 }, (_, i) => doc(i + 1));
  listSpy.mockReset();
  extractionSpy.mockReset();
  listSpy.mockImplementation(async () => docs.map((d) => ({ ...d })));
  extractionSpy.mockImplementation(async (_deal: string, docId: string) => extraction(docId));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useDocuments — extraction fetch discipline', () => {
  it('17 EXTRACTED docs → exactly 17 extraction fetches, then none across 40 s and list re-polls', async () => {
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush(); // list lands → extraction fetches fire immediately

    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(extractionSpy).toHaveBeenCalledTimes(17);
    const fetchedIds = extractionSpy.mock.calls.map((c) => c[1] as string).sort();
    expect(fetchedIds).toEqual(docs.map((d) => d.id).sort());

    // Time passes; nothing is active so the list is not re-polled and no
    // extraction is fetched again.
    for (let i = 0; i < 20; i += 1) await flush(2_000);
    expect(extractionSpy).toHaveBeenCalledTimes(17);
    expect(listSpy).toHaveBeenCalledTimes(1);

    // Even an explicit list refresh (same 17 EXTRACTED docs) adds no fetches.
    act(() => result.current.refresh());
    await flush();
    await flush(5_000);
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(extractionSpy).toHaveBeenCalledTimes(17);

    expect(Object.keys(result.current.extractions)).toHaveLength(17);
  });

  it('three instances on the same deal share ONE fetch per doc (17 total), all three see 17 results', async () => {
    const a = renderHook(() => useDocuments(DEAL));
    const b = renderHook(() => useDocuments(DEAL));
    const c = renderHook(() => useDocuments(DEAL));
    await flush();
    for (let i = 0; i < 10; i += 1) await flush(2_000);

    expect(extractionSpy).toHaveBeenCalledTimes(17);
    expect(Object.keys(a.result.current.extractions)).toHaveLength(17);
    expect(Object.keys(b.result.current.extractions)).toHaveLength(17);
    expect(Object.keys(c.result.current.extractions)).toHaveLength(17);

    // A late-mounting instance (a tab that opens later) also adds no fetches.
    const d = renderHook(() => useDocuments(DEAL));
    await flush();
    await flush(3_000);
    expect(extractionSpy).toHaveBeenCalledTimes(17);
    expect(Object.keys(d.result.current.extractions)).toHaveLength(17);
  });

  it('an in-flight doc is fetched once when it becomes EXTRACTED; list polling stops when all docs are terminal', async () => {
    docs[16] = doc(17, 'EXTRACTING');
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    // 16 EXTRACTED docs fetched; the EXTRACTING one is not (its record isn't
    // final — the list poll drives the transition).
    expect(extractionSpy).toHaveBeenCalledTimes(16);
    expect(listSpy).toHaveBeenCalledTimes(1);

    // The list is polled while a doc is active, and the 16 EXTRACTED docs are
    // NOT re-fetched on any of those polls.
    await flush(6_000);
    expect(listSpy.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(extractionSpy).toHaveBeenCalledTimes(16);

    // The doc finishes → exactly one more extraction fetch.
    docs[16] = doc(17, 'EXTRACTED');
    await flush(2_500);
    expect(extractionSpy).toHaveBeenCalledTimes(17);
    expect(result.current.extractions['doc-17']?.status).toBe('EXTRACTED');

    // Every doc is terminal → the list poll stops and nothing else is fetched.
    const listCalls = listSpy.mock.calls.length;
    for (let i = 0; i < 15; i += 1) await flush(2_000);
    expect(listSpy.mock.calls.length).toBe(listCalls);
    expect(extractionSpy).toHaveBeenCalledTimes(17);
  });

  it('refreshExtraction force-fetches one doc and broadcasts to every instance (FON-23 / FON-41a)', async () => {
    const a = renderHook(() => useDocuments(DEAL));
    const b = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(extractionSpy).toHaveBeenCalledTimes(17);

    extractionSpy.mockImplementationOnce(async (_deal: string, docId: string) => ({
      ...extraction(docId),
      fields: [{ field_name: 'reviewed', value: 1 }],
    }));
    await act(async () => {
      await a.result.current.refreshExtraction('doc-3');
    });
    expect(extractionSpy).toHaveBeenCalledTimes(18);
    expect((a.result.current.extractions['doc-3'] as { fields: unknown[] }).fields).toHaveLength(1);
    expect((b.result.current.extractions['doc-3'] as { fields: unknown[] }).fields).toHaveLength(1);
  });
});
