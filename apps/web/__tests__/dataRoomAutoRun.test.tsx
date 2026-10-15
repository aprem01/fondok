/**
 * Data Room — auto-run baseline waits for the loaded document list.
 *
 * Seen on both tester deals 2026-10-15: every visit to the Data Room of an
 * already-extracted live deal fired a full 8-engine run, because the
 * "EXTRACTED count ticked up" detector took its baseline from the EMPTY list
 * rendered before the first fetch settled. The baseline must come from the
 * settled list; a genuine new extraction after that still auto-runs once.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
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

const doc = (id: string) => ({
  id, filename: `${id}.pdf`, doc_type: 'T12', status: 'EXTRACTED',
  uploaded_at: '2026-01-01T00:00:00Z', size_bytes: 10,
});
const docsState: { documents: ReturnType<typeof doc>[]; settled: boolean } = { documents: [], settled: false };
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: docsState.documents, settled: docsState.settled, loading: !docsState.settled,
    uploading: false, upload: vi.fn(), extractions: {}, error: null,
    refresh: vi.fn(), refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));
const run = vi.fn();
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run, complete: false }) }));
vi.mock('@/lib/hooks/useEngineOutputs', () => ({ useEngineOutputs: () => ({ outputs: null }) }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({ WORKSHEET_ROWS: [] }));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));
vi.mock('@/components/help/CoachMark', () => ({ CoachMark: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

import DataRoomTab from '@/components/project/DataRoomTab';

afterEach(() => { cleanup(); vi.useRealTimers(); run.mockClear(); });

describe('Data Room auto-run baseline', () => {
  it('does not fire a run when an already-extracted deal simply loads', () => {
    vi.useFakeTimers();
    docsState.documents = []; docsState.settled = false;
    const view = render(<DataRoomTab projectId="deal-uuid-1" />);
    // The list settles with two extracted documents — a page load, not a new extraction.
    docsState.documents = [doc('a'), doc('b')]; docsState.settled = true;
    view.rerender(<DataRoomTab projectId="deal-uuid-1" />);
    act(() => { vi.advanceTimersByTime(5_000); });
    expect(run).not.toHaveBeenCalled();
  });

  it('still auto-runs once when a document finishes extracting after the list settled', () => {
    vi.useFakeTimers();
    docsState.documents = [doc('a')]; docsState.settled = true;
    const view = render(<DataRoomTab projectId="deal-uuid-1" />);
    act(() => { vi.advanceTimersByTime(3_000); });
    expect(run).not.toHaveBeenCalled();
    docsState.documents = [doc('a'), doc('b')];
    view.rerender(<DataRoomTab projectId="deal-uuid-1" />);
    act(() => { vi.advanceTimersByTime(3_000); });
    expect(run).toHaveBeenCalledTimes(1);
  });
});
