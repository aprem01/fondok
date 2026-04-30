'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
  WorkerDocument,
  ExtractionResult,
} from '@/lib/api';

const LIST_POLL_MS = 2000;
const EXTRACTION_POLL_MS = 2000;

const ACTIVE_DOC_STATUSES = new Set([
  // PARSING is the new initial state — upload returns immediately and a
  // worker background task drives the row through the rest of the
  // pipeline. We keep polling while we see any in-flight status.
  'PARSING',
  'UPLOADED',
  'CLASSIFYING',
  'EXTRACTING',
  'PROCESSING',
]);

export interface DocumentsState {
  documents: WorkerDocument[];
  loading: boolean;
  error: string | null;
  uploading: boolean;
  /** Per-doc extraction results, keyed by document id. */
  extractions: Record<string, ExtractionResult | undefined>;
  refresh: () => void;
  upload: (files: File[]) => Promise<WorkerDocument[]>;
}

export function useDocuments(dealId: string | null | undefined): DocumentsState {
  const [documents, setDocuments] = useState<WorkerDocument[]>([]);
  const [extractions, setExtractions] = useState<
    Record<string, ExtractionResult | undefined>
  >({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const tick = useRef(0);

  const idStr = dealId == null ? '' : String(dealId);

  const refresh = useCallback(() => {
    if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) {
      setLoading(false);
      return;
    }
    const localTick = ++tick.current;
    const ctrl = new AbortController();
    setLoading(true);
    api.documents
      .list(idStr, ctrl.signal)
      .then((rows) => {
        if (localTick !== tick.current) return;
        setDocuments(rows);
        setError(null);
      })
      .catch((err: unknown) => {
        if (localTick !== tick.current) return;
        if ((err as { name?: string })?.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (localTick !== tick.current) return;
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [idStr]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll the document list while any doc is still progressing.
  useEffect(() => {
    if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) return;
    const anyActive = documents.some((d) => ACTIVE_DOC_STATUSES.has(d.status));
    if (!anyActive) return;
    const t = setInterval(() => {
      api.documents
        .list(idStr)
        .then((rows) => setDocuments(rows))
        .catch(() => {});
    }, LIST_POLL_MS);
    return () => clearInterval(t);
  }, [idStr, documents]);

  // Poll extraction results for each EXTRACTING / EXTRACTED document.
  useEffect(() => {
    if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) return;
    const cancels: Array<() => void> = [];

    documents.forEach((d) => {
      // Skip docs that aren't extracted yet — the extraction record
      // doesn't exist while the doc is still parsing or failed parse.
      if (d.status === 'PARSING' || d.status === 'PARSE_FAILED') return;
      if (d.status === 'UPLOADED' || d.status === 'FAILED') return;
      // Poll until we have an EXTRACTED record.
      const existing = extractions[d.id];
      if (existing && existing.status === 'EXTRACTED') return;

      const ctrl = new AbortController();
      const fetchOnce = () => {
        api.documents
          .extraction(idStr, d.id, ctrl.signal)
          .then((r) => {
            setExtractions((prev) => ({ ...prev, [d.id]: r }));
          })
          .catch(() => {});
      };
      fetchOnce();
      const t = setInterval(fetchOnce, EXTRACTION_POLL_MS);
      cancels.push(() => {
        clearInterval(t);
        ctrl.abort();
      });
    });

    return () => cancels.forEach((c) => c());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idStr, documents.map((d) => `${d.id}:${d.status}`).join('|')]);

  const upload = useCallback(
    async (files: File[]): Promise<WorkerDocument[]> => {
      if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) {
        throw new Error('worker not connected');
      }
      setUploading(true);
      try {
        const created = await api.documents.upload(idStr, files);
        // Optimistically merge new docs into the list. The worker now
        // auto-chains parse → extract on its own background task, so
        // the frontend no longer needs to fire a separate /extract
        // call — it would just race with the worker's pipeline.
        setDocuments((prev) => [...created, ...prev]);
        // Re-fetch the canonical list so the polling loop picks up
        // the PARSING → UPLOADED → CLASSIFYING transitions.
        refresh();
        return created;
      } finally {
        setUploading(false);
      }
    },
    [idStr, refresh],
  );

  return { documents, loading, error, uploading, extractions, refresh, upload };
}
