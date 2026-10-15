/**
 * #18 — Data Room "Re-run extraction" (per document + for all).
 *
 *  • the per-row action and the bulk button open a confirm step that says
 *    what happens (fresh run under the current pipeline; field reviews are
 *    not carried over) before anything is sent;
 *  • both are disabled with the reason while a document is processing;
 *  • the model auto-runs ONCE when the whole re-run batch has finished —
 *    not per document, and not on page load (FON-105 stays fixed).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, act, fireEvent, screen } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: { documents: {
    downloadUrlSigned: vi.fn(), download: vi.fn(), reviewField: vi.fn(),
    reclassify: vi.fn(), acceptClassification: vi.fn(), acceptYear: vi.fn(),
  } },
}));

const doc = (id: string, status = 'EXTRACTED') => ({
  id, filename: `${id}.pdf`, doc_type: 'T12', status,
  uploaded_at: '2026-01-01T00:00:00Z', size_bytes: 10,
});
const docsState: { documents: ReturnType<typeof doc>[]; settled: boolean } = { documents: [], settled: true };
const reextract = vi.fn();
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: docsState.documents, settled: docsState.settled, loading: false,
    uploading: false, upload: vi.fn(), extractions: {}, error: null,
    refresh: vi.fn(), refreshExtraction: vi.fn().mockResolvedValue(undefined),
    reextract,
  }),
}));
const run = vi.fn();
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run, complete: false }) }));
vi.mock('@/lib/hooks/useEngineOutputs', () => ({ useEngineOutputs: () => ({ outputs: null }) }));
const toast = vi.fn();
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({ WORKSHEET_ROWS: [] }));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));
vi.mock('@/components/help/CoachMark', () => ({ CoachMark: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

import DataRoomTab from '@/components/project/DataRoomTab';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  run.mockClear();
  reextract.mockReset();
  toast.mockClear();
});

const view = () => <DataRoomTab projectId="deal-uuid-1" />;

describe('Data Room — re-run extraction', () => {
  it('per document: confirm says what happens, then re-runs through /extract', async () => {
    docsState.documents = [doc('a')];
    reextract.mockResolvedValue({ accepted: ['a'], failed: [] });
    render(view());
    fireEvent.click(screen.getByRole('button', { name: 'Re-run extraction for a.pdf' }));
    expect(reextract).not.toHaveBeenCalled();
    const dialog = screen.getByTestId('reextract-confirm');
    expect(dialog.textContent).toMatch(/a\.pdf will be extracted again/);
    expect(dialog.textContent).toMatch(/current extraction pipeline/);
    expect(dialog.textContent).toMatch(/accepted, edited or rejected — are not carried over/);
    await act(async () => {
      fireEvent.click(screen.getByTestId('reextract-confirm-go'));
    });
    expect(reextract).toHaveBeenCalledWith([{ id: 'a', endpoint: 'extract' }]);
    expect(screen.queryByTestId('reextract-confirm')).toBeNull();
  });

  it('a failed document is re-run through /reprocess', async () => {
    docsState.documents = [doc('a'), doc('f', 'FAILED')];
    reextract.mockResolvedValue({ accepted: ['f'], failed: [] });
    render(view());
    fireEvent.click(screen.getByRole('button', { name: 'Re-run extraction for f.pdf' }));
    await act(async () => {
      fireEvent.click(screen.getByTestId('reextract-confirm-go'));
    });
    expect(reextract).toHaveBeenCalledWith([{ id: 'f', endpoint: 'reprocess' }]);
  });

  it('is disabled with the reason while a document is processing', () => {
    docsState.documents = [doc('a'), doc('p', 'EXTRACTING')];
    render(view());
    const busy = screen.getByRole('button', { name: 'Re-run extraction for p.pdf' });
    expect(busy).toBeDisabled();
    expect(busy.getAttribute('title')).toMatch(/still processing/i);
    // the done document can still be re-run on its own…
    expect(screen.getByRole('button', { name: 'Re-run extraction for a.pdf' })).not.toBeDisabled();
    // …but not the whole set while one is moving
    expect(screen.getByTestId('reextract-all')).toBeDisabled();
    expect(screen.getByTestId('reextract-all-reason').textContent).toMatch(/1 document is still processing/);
  });

  it('a refused request toasts the reason in the analyst’s words', async () => {
    docsState.documents = [doc('a')];
    reextract.mockResolvedValue({ accepted: [], failed: [{ id: 'a', error: { status: 409 } }] });
    render(view());
    fireEvent.click(screen.getByRole('button', { name: 'Re-run extraction for a.pdf' }));
    await act(async () => {
      fireEvent.click(screen.getByTestId('reextract-confirm-go'));
    });
    expect(toast).toHaveBeenCalledWith(
      expect.stringMatching(/Couldn't re-run extraction for a\.pdf: it is still processing/),
      { type: 'error' },
    );
  });

  it('re-run all: the model runs ONCE, after the last document finishes', async () => {
    vi.useFakeTimers();
    docsState.documents = [doc('a'), doc('b')];
    reextract.mockResolvedValue({ accepted: ['a', 'b'], failed: [] });
    const v = render(view());
    act(() => { vi.advanceTimersByTime(3_000); });
    expect(run).not.toHaveBeenCalled(); // page load never auto-runs (FON-105)

    fireEvent.click(screen.getByTestId('reextract-all'));
    expect(screen.getByTestId('reextract-confirm').textContent).toMatch(/2 documents will be extracted again/);
    await act(async () => {
      fireEvent.click(screen.getByTestId('reextract-confirm-go'));
    });
    expect(reextract).toHaveBeenCalledWith([
      { id: 'a', endpoint: 'extract' },
      { id: 'b', endpoint: 'extract' },
    ]);

    // both back in the pipeline (the existing status polling)
    docsState.documents = [doc('a', 'CLASSIFYING'), doc('b', 'CLASSIFYING')];
    v.rerender(view());
    act(() => { vi.advanceTimersByTime(3_000); });
    // the first one lands — the EXTRACTED count ticks up, but the batch is open
    docsState.documents = [doc('a'), doc('b', 'EXTRACTING')];
    v.rerender(view());
    act(() => { vi.advanceTimersByTime(3_000); });
    expect(run).not.toHaveBeenCalled();
    // the last one lands → one run
    docsState.documents = [doc('a'), doc('b')];
    v.rerender(view());
    act(() => { vi.advanceTimersByTime(3_000); });
    expect(run).toHaveBeenCalledTimes(1);
    v.rerender(view());
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('a re-run where every document fails does not run the model', async () => {
    vi.useFakeTimers();
    docsState.documents = [doc('a')];
    reextract.mockResolvedValue({ accepted: ['a'], failed: [] });
    const v = render(view());
    fireEvent.click(screen.getByRole('button', { name: 'Re-run extraction for a.pdf' }));
    await act(async () => {
      fireEvent.click(screen.getByTestId('reextract-confirm-go'));
    });
    docsState.documents = [doc('a', 'EXTRACTING')];
    v.rerender(view());
    docsState.documents = [doc('a', 'FAILED')];
    v.rerender(view());
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(run).not.toHaveBeenCalled();
  });
});
