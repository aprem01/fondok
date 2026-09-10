'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
  WorkerDocument,
  ExtractionResult,
} from '@/lib/api';

// ─────────────────────────────────────────────────────────────────────────────
// useDocuments — the deal's document list + per-document extraction results.
//
// Production finding (2026-09-10): on a 17-doc deal the browser issued 69
// extraction fetches in 40 s although every doc was already EXTRACTED. Eight
// components mount this hook (deal header, SourceDocPane, Data Room, PLTab,
// PLReviewSection, GroundedWorksheet, MemoStream, the doc-detail page — five
// at once on the Financials tab) and each instance held its OWN extraction
// map, so every instance re-fetched all 17 as its own list call landed.
//
// Now every instance reads ONE module-level per-deal store:
//   • the document list is fetched with in-flight de-duplication and
//     broadcast to every instance;
//   • an extraction is fetched ONCE per document when its list status reaches
//     EXTRACTED (never re-fetched because the list re-polled or another
//     instance mounted; bounded retries only when the record is not final or
//     the request errors);
//   • ONE list poller per deal runs only while some doc is still in flight,
//     backs off after a long stall, and stops when every doc is terminal;
//   • ``refreshExtraction`` (FON-23) force-fetches one doc and the store
//     broadcast converges every instance on the fresh result (FON-41a);
//   • FON-41a: the store carries a per-deal ``settled`` flag — latched true
//     once the first list attempt has completed AND every EXTRACTED doc's
//     extraction is loaded-or-failed — and ``extractionFailures`` (docs whose
//     bounded retry gave up with no record). The Financials worksheet holds a
//     skeleton on ``!settled`` instead of a false empty state, and never waits
//     on a doc that will never load.
// ─────────────────────────────────────────────────────────────────────────────

const LIST_POLL_MS = 2000;
/** No status change for this long → the poll backs off (a doc stuck in an
 *  in-flight status must not hold a 2 s poll forever). */
const LIST_POLL_STALL_MS = 120_000;
const LIST_POLL_SLOW_MS = 15_000;
/** The list says EXTRACTED but the record isn't final yet (or errored) →
 *  retry with linear backoff, at most this many attempts per list status. */
const EXTRACTION_RETRY_MS = 3000;
const EXTRACTION_MAX_ATTEMPTS = 3;

const ACTIVE_DOC_STATUSES = new Set([
  // PARSING is the initial state — upload returns immediately and a worker
  // background task drives the row through the rest of the pipeline. We keep
  // polling the list while we see any in-flight status.
  'PARSING',
  'UPLOADED',
  'CLASSIFYING',
  'EXTRACTING',
  'PROCESSING',
]);
/** List statuses for which an extraction record exists to fetch. */
const FETCHABLE_DOC_STATUSES = new Set(['EXTRACTED']);
/** Extraction-record statuses that are final — never re-fetched. */
const TERMINAL_EXTRACTION_STATUSES = new Set(['EXTRACTED', 'FAILED', 'PARSE_FAILED']);

interface Subscriber {
  onDocuments: (rows: WorkerDocument[]) => void;
  onExtraction: (docId: string, result: ExtractionResult) => void;
  /** FON-41a: settled flag + per-doc give-ups, after every store transition. */
  onMeta: (settled: boolean, failures: Record<string, boolean>) => void;
}

interface DealStore {
  documents: WorkerDocument[] | null;
  results: Map<string, ExtractionResult>;
  /** docId → in-flight extraction fetch. */
  inflight: Map<string, Promise<void>>;
  /** docId → the list status the last fetch was made for + attempt count. */
  attempts: Map<string, { status: string; count: number }>;
  retryTimers: Map<string, ReturnType<typeof setTimeout>>;
  listInflight: Promise<WorkerDocument[]> | null;
  listSeq: number;
  pollTimer: ReturnType<typeof setTimeout> | null;
  lastSignature: string;
  lastChangeAt: number;
  subscribers: Set<Subscriber>;
  /** FON-41a: the first list fetch has completed (success or failure). */
  listAttempted: boolean;
  /** FON-41a: docs whose extraction fetch gave up (bounded retries, no record). */
  failures: Set<string>;
  /** FON-41a: latched — list attempted + every EXTRACTED doc loaded-or-failed. */
  settled: boolean;
}

const stores = new Map<string, DealStore>();

function getStore(dealId: string): DealStore {
  let s = stores.get(dealId);
  if (!s) {
    s = {
      documents: null,
      results: new Map(),
      inflight: new Map(),
      attempts: new Map(),
      retryTimers: new Map(),
      listInflight: null,
      listSeq: 0,
      pollTimer: null,
      lastSignature: '',
      lastChangeAt: Date.now(),
      subscribers: new Set(),
      listAttempted: false,
      failures: new Set(),
      settled: false,
    };
    stores.set(dealId, s);
  }
  return s;
}

function signatureOf(rows: WorkerDocument[]): string {
  return rows.map((d) => `${d.id}:${d.status}`).join('|');
}

function broadcastExtraction(store: DealStore, docId: string, result: ExtractionResult): void {
  for (const s of store.subscribers) s.onExtraction(docId, result);
}

function snapshotFailures(store: DealStore): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  store.failures.forEach((id) => {
    out[id] = true;
  });
  return out;
}

/** A doc no longer holds up `settled`: nothing to fetch for its status, or an
 *  extraction record landed, or the bounded retry gave up. */
function isResolved(store: DealStore, doc: WorkerDocument): boolean {
  if (!FETCHABLE_DOC_STATUSES.has(doc.status)) return true;
  return store.results.has(doc.id) || store.failures.has(doc.id);
}

/** FON-41a: latch `settled` the first time every EXTRACTED doc is resolved,
 *  then tell every instance. Latched on purpose — a doc that finishes
 *  extracting later just appears when its record lands; it must not flip a
 *  rendered worksheet back to a skeleton. */
function recomputeMeta(store: DealStore): void {
  if (!store.settled && store.listAttempted && (store.documents ?? []).every((d) => isResolved(store, d))) {
    store.settled = true;
  }
  const failures = snapshotFailures(store);
  for (const s of store.subscribers) s.onMeta(store.settled, failures);
}

function runExtractionFetch(
  store: DealStore,
  dealId: string,
  docId: string,
  listStatus: string,
  attempt: number,
): Promise<void> {
  store.attempts.set(docId, { status: listStatus, count: attempt });
  const p = api.documents
    .extraction(dealId, docId)
    .then((r) => {
      store.results.set(docId, r);
      store.failures.delete(docId);
      broadcastExtraction(store, docId, r);
      const status = (r?.status as string | undefined) ?? '';
      if (!TERMINAL_EXTRACTION_STATUSES.has(status) && attempt < EXTRACTION_MAX_ATTEMPTS) {
        scheduleExtractionRetry(store, dealId, docId, listStatus, attempt + 1);
      }
      recomputeMeta(store);
    })
    .catch(() => {
      // Sam QA 2026-07-02 — never let one broken extraction row burn worker
      // cycles: bounded retries, then give up for this list status.
      if (attempt < EXTRACTION_MAX_ATTEMPTS) {
        scheduleExtractionRetry(store, dealId, docId, listStatus, attempt + 1);
        return;
      }
      // FON-41a: gave up with no record — record it so nothing waits on it.
      if (!store.results.has(docId)) store.failures.add(docId);
      recomputeMeta(store);
    })
    .finally(() => {
      if (store.inflight.get(docId) === p) store.inflight.delete(docId);
    });
  store.inflight.set(docId, p);
  return p;
}

function scheduleExtractionRetry(
  store: DealStore,
  dealId: string,
  docId: string,
  listStatus: string,
  attempt: number,
): void {
  const t = setTimeout(() => {
    store.retryTimers.delete(docId);
    void runExtractionFetch(store, dealId, docId, listStatus, attempt);
  }, EXTRACTION_RETRY_MS * (attempt - 1));
  store.retryTimers.set(docId, t);
}

/** Fetch a doc's extraction at most once per (doc, list status). */
function ensureExtraction(store: DealStore, dealId: string, doc: WorkerDocument): void {
  if (!FETCHABLE_DOC_STATUSES.has(doc.status)) return;
  const existing = store.results.get(doc.id);
  if (existing && TERMINAL_EXTRACTION_STATUSES.has(existing.status)) return;
  if (store.inflight.has(doc.id) || store.retryTimers.has(doc.id)) return;
  const att = store.attempts.get(doc.id);
  if (att && att.status === doc.status) return; // already fetched for this status
  void runExtractionFetch(store, dealId, doc.id, doc.status, 1);
}

function applyDocuments(store: DealStore, dealId: string, rows: WorkerDocument[]): void {
  const sig = signatureOf(rows);
  if (sig !== store.lastSignature) {
    store.lastSignature = sig;
    store.lastChangeAt = Date.now();
  }
  store.documents = rows;
  store.listAttempted = true;
  for (const s of store.subscribers) s.onDocuments(rows);
  for (const d of rows) ensureExtraction(store, dealId, d);
  recomputeMeta(store);
  schedulePoll(store, dealId);
}

function fetchList(store: DealStore, dealId: string): Promise<WorkerDocument[]> {
  if (store.listInflight) return store.listInflight;
  const seq = ++store.listSeq;
  const p = api.documents
    .list(dealId)
    .then(
      (rows) => {
        // Only the latest-started fetch may apply — a slow earlier response
        // must not overwrite fresher rows.
        if (seq === store.listSeq) applyDocuments(store, dealId, rows);
        return rows;
      },
      (err: unknown) => {
        // FON-41a: a failed first list still "settles" the deal (with no
        // documents) so a consumer holding a skeleton can show its honest
        // empty / error state instead of waiting forever.
        if (!store.listAttempted) {
          store.listAttempted = true;
          recomputeMeta(store);
        }
        throw err;
      },
    )
    .finally(() => {
      if (store.listInflight === p) store.listInflight = null;
    });
  store.listInflight = p;
  return p;
}

/** One list poller per deal: runs only while a doc is in flight AND someone
 *  is mounted; backs off after a stall; stops once every doc is terminal. */
function schedulePoll(store: DealStore, dealId: string): void {
  if (store.pollTimer) {
    clearTimeout(store.pollTimer);
    store.pollTimer = null;
  }
  if (store.subscribers.size === 0) return;
  const anyActive = (store.documents ?? []).some((d) => ACTIVE_DOC_STATUSES.has(d.status));
  if (!anyActive) return;
  const stalled = Date.now() - store.lastChangeAt > LIST_POLL_STALL_MS;
  store.pollTimer = setTimeout(() => {
    store.pollTimer = null;
    fetchList(store, dealId).catch(() => {
      // Transient list error — keep polling (applyDocuments re-arms on success).
      schedulePoll(store, dealId);
    });
  }, stalled ? LIST_POLL_SLOW_MS : LIST_POLL_MS);
}

function subscribe(store: DealStore, dealId: string, sub: Subscriber): () => void {
  store.subscribers.add(sub);
  schedulePoll(store, dealId);
  return () => {
    store.subscribers.delete(sub);
    if (store.subscribers.size === 0 && store.pollTimer) {
      clearTimeout(store.pollTimer);
      store.pollTimer = null;
    }
  };
}

function snapshotResults(store: DealStore): Record<string, ExtractionResult | undefined> {
  const out: Record<string, ExtractionResult | undefined> = {};
  store.results.forEach((r, id) => {
    out[id] = r;
  });
  return out;
}

const sameKeys = (a: Record<string, boolean>, b: Record<string, boolean>): boolean => {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => b[k]);
};

export interface DocumentsState {
  documents: WorkerDocument[];
  loading: boolean;
  /** FON-41a: latched true once the first list fetch has completed (success or
   *  failure) AND every EXTRACTED doc's extraction is loaded-or-failed — lets
   *  consumers hold a skeleton instead of an empty state until then. Shared
   *  per deal: a late-mounting tab on a warm store is settled at once. */
  settled: boolean;
  error: string | null;
  uploading: boolean;
  /** Per-doc extraction results, keyed by document id. */
  extractions: Record<string, ExtractionResult | undefined>;
  /** FON-41a: docs whose extraction fetch gave up (bounded retries, no record),
   *  so a consumer waiting on "every extraction loaded" doesn't wait forever. */
  extractionFailures: Record<string, boolean>;
  refresh: () => void;
  /** FON-23: force-refetch one doc's extraction after an analyst review. */
  refreshExtraction: (docId: string) => Promise<void>;
  upload: (files: File[]) => Promise<WorkerDocument[]>;
}

export function useDocuments(dealId: string | null | undefined): DocumentsState {
  const [documents, setDocuments] = useState<WorkerDocument[]>([]);
  const [extractions, setExtractions] = useState<
    Record<string, ExtractionResult | undefined>
  >({});
  const [loading, setLoading] = useState<boolean>(false);
  const [settled, setSettled] = useState<boolean>(false);
  const [extractionFailures, setExtractionFailures] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const tick = useRef(0);

  const idStr = dealId == null ? '' : String(dealId);
  // Numeric ids belong to mockData; the worker only knows UUIDs.
  const live = isWorkerConnected() && !!idStr && !/^\d+$/.test(idStr);

  // Subscribe to the shared per-deal store FIRST (before the initial refresh
  // below) and seed from whatever other instances already fetched, so a
  // late-mounting tab shows the extractions with zero extra requests.
  useEffect(() => {
    if (!live) {
      // Nothing will ever load for a mock / offline deal — settled at once.
      setSettled(true);
      return;
    }
    const store = getStore(idStr);
    const unsub = subscribe(store, idStr, {
      onDocuments: (rows) => setDocuments(rows),
      onExtraction: (docId, r) => setExtractions((prev) => ({ ...prev, [docId]: r })),
      onMeta: (s, failures) => {
        setSettled(s);
        setExtractionFailures((prev) => (sameKeys(prev, failures) ? prev : failures));
      },
    });
    if (store.documents) setDocuments(store.documents);
    if (store.results.size > 0) setExtractions(snapshotResults(store));
    setSettled(store.settled);
    setExtractionFailures(snapshotFailures(store));
    return unsub;
  }, [idStr, live]);

  const refresh = useCallback(() => {
    if (!live) {
      setLoading(false);
      return;
    }
    const store = getStore(idStr);
    const localTick = ++tick.current;
    setLoading(true);
    fetchList(store, idStr)
      .then(() => {
        if (localTick !== tick.current) return;
        setError(null);
      })
      .catch((err: unknown) => {
        if (localTick !== tick.current) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (localTick !== tick.current) return;
        setLoading(false);
      });
  }, [idStr, live]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // FON-23: force a refetch of ONE doc's extraction after an analyst review.
  // The store broadcast (FON-41a) converges every mounted instance on the
  // fresh result without a second round-trip.
  const refreshExtraction = useCallback(
    async (docId: string) => {
      if (!live) return;
      const store = getStore(idStr);
      const t = store.retryTimers.get(docId);
      if (t) {
        clearTimeout(t);
        store.retryTimers.delete(docId);
      }
      const listStatus = store.documents?.find((d) => d.id === docId)?.status ?? 'EXTRACTED';
      try {
        await runExtractionFetch(store, idStr, docId, listStatus, EXTRACTION_MAX_ATTEMPTS);
      } catch {
        // Best-effort — the row keeps its prior state on failure.
      }
    },
    [idStr, live],
  );

  const upload = useCallback(
    async (files: File[]): Promise<WorkerDocument[]> => {
      if (!live) {
        throw new Error('worker not connected');
      }
      setUploading(true);
      try {
        const created = await api.documents.upload(idStr, files);
        // Optimistically merge new docs into the shared list (every instance
        // sees them). The worker auto-chains parse → extract on its own
        // background task, so no separate /extract call — it would just race
        // the worker's pipeline. The poller picks up the PARSING → … →
        // EXTRACTED transitions and fetches each extraction once.
        const store = getStore(idStr);
        applyDocuments(store, idStr, [...created, ...(store.documents ?? [])]);
        refresh();
        return created;
      } finally {
        setUploading(false);
      }
    },
    [idStr, live, refresh],
  );

  return {
    documents,
    loading,
    settled,
    error,
    uploading,
    extractions,
    extractionFailures,
    refresh,
    refreshExtraction,
    upload,
  };
}
