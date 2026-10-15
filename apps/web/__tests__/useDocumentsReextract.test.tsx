/**
 * #18 — useDocuments().reextract: the re-run goes to the right worker route
 * with force, the row flips to an in-flight status at once (so the existing
 * poller tracks it), a list fetch that started before the request cannot
 * overwrite that, and the held extraction is re-fetched when the re-run lands
 * — not treated as final because the previous run's record was EXTRACTED.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { WorkerDocument, ExtractionResult } from '@/lib/api';

const listSpy = vi.fn();
const extractionSpy = vi.fn();
const extractSpy = vi.fn();
const reprocessSpy = vi.fn();

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
        extract: (...a: unknown[]) => extractSpy(...a),
        reprocess: (...a: unknown[]) => reprocessSpy(...a),
      },
    },
  };
});

import { useDocuments } from '@/lib/hooks/useDocuments';

let dealSeq = 0;
let DEAL = 'deal-uuid-reextract-0';

function doc(id: string, status: string): WorkerDocument {
  return { id, deal_id: DEAL, tenant_id: 't', filename: `${id}.pdf`, doc_type: 'T12', status } as unknown as WorkerDocument;
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

let docs: WorkerDocument[] = [];
let run = 1;

beforeEach(() => {
  vi.useFakeTimers();
  DEAL = `deal-uuid-reextract-${++dealSeq}`;
  run = 1;
  docs = [doc('a', 'EXTRACTED'), doc('b', 'FAILED')];
  listSpy.mockReset();
  extractionSpy.mockReset();
  extractSpy.mockReset();
  reprocessSpy.mockReset();
  listSpy.mockImplementation(async () => docs.map((d) => ({ ...d })));
  extractionSpy.mockImplementation(
    async (_deal: string, docId: string) =>
      ({ document_id: docId, status: 'EXTRACTED', fields: [{ field_name: `run${run}`, value: run }] }) as unknown as ExtractionResult,
  );
  extractSpy.mockResolvedValue({ status: 'extraction_started' });
  reprocessSpy.mockResolvedValue({ status: 'reprocess_started' });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useDocuments().reextract', () => {
  it('sends EXTRACTED docs to /extract with force and FAILED docs to /reprocess', async () => {
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    // what the worker's rows say once it has answered 202
    docs = [doc('a', 'CLASSIFYING'), doc('b', 'PARSING')];
    let out: Awaited<ReturnType<typeof result.current.reextract>> | undefined;
    await act(async () => {
      out = await result.current.reextract([
        { id: 'a', endpoint: 'extract' },
        { id: 'b', endpoint: 'reprocess' },
      ]);
    });
    expect(extractSpy).toHaveBeenCalledWith(DEAL, 'a', { force: true });
    expect(reprocessSpy).toHaveBeenCalledWith(DEAL, 'b');
    expect(out).toEqual({ accepted: ['a', 'b'], failed: [] });
    // flipped in-flight at once (the worker already did, before its 202)
    const status = Object.fromEntries(result.current.documents.map((d) => [d.id, d.status]));
    expect(status).toEqual({ a: 'CLASSIFYING', b: 'PARSING' });
  });

  it('reports a refused request and leaves that document as it was', async () => {
    extractSpy.mockRejectedValueOnce(Object.assign(new Error('busy'), { status: 409 }));
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    let out: Awaited<ReturnType<typeof result.current.reextract>> | undefined;
    await act(async () => {
      out = await result.current.reextract([{ id: 'a', endpoint: 'extract' }]);
    });
    expect(out?.accepted).toEqual([]);
    expect(out?.failed).toHaveLength(1);
    expect((out?.failed[0].error as { status: number }).status).toBe(409);
    expect(result.current.documents.find((d) => d.id === 'a')?.status).toBe('EXTRACTED');
  });

  it('polls the re-run to the end and re-fetches the NEW extraction once it lands', async () => {
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    expect(extractionSpy).toHaveBeenCalledTimes(1); // doc a, run 1
    expect(result.current.extractions.a?.fields[0].field_name).toBe('run1');

    // the worker is now extracting doc a again
    docs = [doc('a', 'EXTRACTING'), doc('b', 'FAILED')];
    await act(async () => {
      await result.current.reextract([{ id: 'a', endpoint: 'extract' }]);
    });
    await flush(2_500); // the poller is running again
    expect(result.current.documents.find((d) => d.id === 'a')?.status).toBe('EXTRACTING');
    // the previous run's result is still what is held meanwhile
    expect(extractionSpy).toHaveBeenCalledTimes(1);

    // the re-run lands
    run = 2;
    docs = [doc('a', 'EXTRACTED'), doc('b', 'FAILED')];
    await flush(2_500);
    expect(extractionSpy).toHaveBeenCalledTimes(2);
    expect(result.current.extractions.a?.fields[0].field_name).toBe('run2');

    // and it is final again — no further fetches
    for (let i = 0; i < 5; i += 1) await flush(2_000);
    expect(extractionSpy).toHaveBeenCalledTimes(2);
  });

  it('a list fetch that started before the request cannot overwrite the in-flight status', async () => {
    const { result } = renderHook(() => useDocuments(DEAL));
    await flush();
    // a slow list fetch (old statuses) is in flight when the re-run is sent
    let releaseSlow: (rows: WorkerDocument[]) => void = () => {};
    listSpy.mockImplementationOnce(
      () => new Promise<WorkerDocument[]>((res) => { releaseSlow = res; }),
    );
    act(() => result.current.refresh());
    docs = [doc('a', 'CLASSIFYING'), doc('b', 'FAILED')];
    await act(async () => {
      await result.current.reextract([{ id: 'a', endpoint: 'extract' }]);
    });
    await act(async () => {
      releaseSlow([doc('a', 'EXTRACTED'), doc('b', 'FAILED')]);
    });
    await flush();
    expect(result.current.documents.find((d) => d.id === 'a')?.status).toBe('CLASSIFYING');
  });
});
