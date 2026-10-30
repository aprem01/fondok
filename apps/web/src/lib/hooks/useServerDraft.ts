'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
  userMessage,
  workerErrorCode,
  WorkerError,
  type DraftWizardPayload,
  type NewDealBody,
  type WorkerDeal,
} from '@/lib/api';
import {
  DRAFT_DEBOUNCE_MS,
  clearDraftPointer,
  saveDraftPointer,
} from '@/lib/wizardDraft';

/**
 * FON-79 — the New Deal wizard's server-side draft.
 *
 * Once the wizard has a Deal Name and the worker can hold a draft, the deal
 * row IS the autosave: ``create()`` makes the row once (``lifecycle='draft'``),
 * ``save()`` PATCHes the normalized deal columns plus the wizard's resume
 * payload on a debounce, ``activate()`` is Finish, ``discard()`` deletes the
 * row, and ``load()`` is the resume read (``?include=draft_wizard``).
 *
 * Invariants this hook owns:
 *   - never two PATCHes in flight — a serial queue; changes made while one
 *     is in flight collapse into the next (last write wins; one author);
 *   - ``create()`` issues ONE POST however often it is called — the promise
 *     is held, so a flaky first autosave cannot double-create;
 *   - once the row is lost (a PATCH answered 404, or 409 ``deal_not_draft``
 *     because another tab finished or discarded it) the hook is CLOSED: no
 *     further POST or PATCH leaves it for the rest of the page's life — the
 *     wizard must not resurrect a draft someone just discarded. ``reset()``
 *     (an explicit new start) reopens it;
 *   - a save that failed for a reason a retry can fix — the network, a
 *     timeout, a 5xx — is retried with a bounded exponential backoff
 *     ("Couldn't save — retrying"); a 4xx is deterministic, so it is shown
 *     once with the worker's reason and NOT retried (the next edit sends the
 *     latest content, which may well fix it);
 *   - ``flush()`` runs whatever is pending NOW and settles before Finish, so
 *     the last keystroke is on the row before ``activate`` clears the wizard
 *     payload (a PATCH after activate would be refused).
 *
 * Feature detection: ``probe()`` asks the pipeline summary for
 * ``draft_count`` — a worker that cannot hold drafts has no such field, and
 * the wizard then keeps today's browser-local autosave. The create response
 * is NOT the detector: an older worker would ignore ``lifecycle`` and turn
 * the first autosave into a real active deal.
 */

export type DraftSaveStatus = 'idle' | 'saving' | 'saved' | 'error';

/** Why a server draft stopped being this wizard's draft. */
export type DraftLostReason = 'not_found' | 'not_draft' | 'unsupported';

/** The deal columns a save carries beside the wizard payload. */
export type DraftDealPatch = Partial<Omit<NewDealBody, 'lifecycle' | 'draft_wizard'>>;

export interface DraftLoadResult {
  deal: WorkerDeal;
  wizard: DraftWizardPayload | null;
}

export interface ServerDraft {
  draftId: string | null;
  saveStatus: DraftSaveStatus;
  /** With ``saveStatus === 'error'``: the reason in plain words (``userMessage``). */
  saveError: string | null;
  /** With ``saveStatus === 'error'``: true while a retry is scheduled. */
  willRetry: boolean;
  /** When the row last accepted a save (ms since epoch); null before the first. */
  savedAt: number | null;
  /** Whether the worker can hold a draft — null until ``probe()`` or ``load()`` answered. */
  supported: boolean | null;
  probe: () => Promise<boolean>;
  /** Create the draft row once. Resolves to its id, or null when the row
   *  could not be made (the caller keeps its local draft and retries later). */
  create: (body: DraftDealPatch, wizard: DraftWizardPayload) => Promise<string | null>;
  /** Queue a debounced PATCH. No-op without a draft or when nothing changed. */
  save: (body: DraftDealPatch, wizard: DraftWizardPayload) => void;
  /** Tell the hook what the row already holds (after a resume), so the
   *  first render after opening a draft does not write it back unchanged. */
  prime: (body: DraftDealPatch, wizard: DraftWizardPayload) => void;
  /** Send whatever is pending now and wait for it. Rejects with the save
   *  error when the row did not take the latest change. */
  flush: () => Promise<void>;
  /** Finish: flush, then ``POST /deals/{id}/activate``. */
  activate: () => Promise<WorkerDeal>;
  /** ``DELETE /deals/{id}/draft``; rejects with the worker's refusal
   *  (409 ``documents_in_flight`` carries the count). */
  discard: () => Promise<void>;
  /** The resume read. Resolves null (after ``onLost``) when the id is not a
   *  draft on this tenant any more. */
  load: (id: string) => Promise<DraftLoadResult | null>;
  /** Forget the draft locally and reopen the hook (after Finish navigated,
   *  or an explicit new start). */
  reset: () => void;
}

export interface UseServerDraftOptions {
  onLost?: (reason: DraftLostReason, id: string) => void;
}

/** First retry delay for a retryable failure; doubles per attempt. */
export const DRAFT_RETRY_MS = 4000;
/** Longest wait between retries. */
export const DRAFT_RETRY_MAX_MS = 60_000;
/** Retries per failed save before the hook stops and shows the reason. */
export const DRAFT_RETRY_MAX_ATTEMPTS = 5;

interface Pending {
  body: DraftDealPatch;
  wizard: DraftWizardPayload;
  /** Serialized form, for the no-change short-circuit. */
  key: string;
}

function serialize(body: DraftDealPatch, wizard: DraftWizardPayload): string {
  return JSON.stringify({ body, wizard });
}

/** 404 → the row is gone; 409 → it is no longer a draft (or never was). */
function lostReasonFor(err: unknown): DraftLostReason | null {
  if (!(err instanceof WorkerError)) return null;
  if (err.status === 404) return 'not_found';
  if (err.status === 409 && workerErrorCode(err) === 'deal_not_draft') return 'not_draft';
  return null;
}

/** Only failures a retry can fix: no answer, a timeout, or the worker's 5xx.
 *  A 4xx is deterministic for the same content — retrying it is a storm. */
export function isRetryableSaveError(err: unknown): boolean {
  if (err instanceof WorkerError) return err.status === 0 || err.status >= 500;
  const name = (err as { name?: string } | null)?.name;
  if (name === 'WorkerError') {
    const status = (err as { status?: number }).status ?? 0;
    return status === 0 || status >= 500;
  }
  if (name === 'TimeoutError' || name === 'AbortError') return true;
  if (err instanceof TypeError) return true; // fetch's network failure
  return false;
}

/** 4 s, 8 s, 16 s, 32 s, 60 s (capped). */
export function retryDelayMs(attempt: number): number {
  return Math.min(DRAFT_RETRY_MS * 2 ** Math.max(0, attempt), DRAFT_RETRY_MAX_MS);
}

export function useServerDraft(options: UseServerDraftOptions = {}): ServerDraft {
  const [draftId, setDraftId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<DraftSaveStatus>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [willRetry, setWillRetry] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);

  const draftIdRef = useRef<string | null>(null);
  const supportedRef = useRef<boolean | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const lastSentKeyRef = useRef<string | null>(null);
  const lastErrorRef = useRef<unknown>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Consecutive failed PATCH attempts for the content now pending. */
  const retryAttemptsRef = useRef(0);
  const createPromiseRef = useRef<Promise<string | null> | null>(null);
  const createRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const createAttemptsRef = useRef(0);
  const lastCreateArgsRef = useRef<{ body: DraftDealPatch; wizard: DraftWizardPayload } | null>(null);
  /** Set once the row is gone / activated — no POST or PATCH may leave until ``reset()``. */
  const closedRef = useRef(false);
  const onLostRef = useRef(options.onLost);
  onLostRef.current = options.onLost;

  const clearTimers = useCallback(() => {
    if (debounceRef.current) { clearTimeout(debounceRef.current); debounceRef.current = null; }
    if (retryRef.current) { clearTimeout(retryRef.current); retryRef.current = null; }
    if (createRetryRef.current) { clearTimeout(createRetryRef.current); createRetryRef.current = null; }
  }, []);

  const ok = useCallback(() => {
    lastErrorRef.current = null;
    setSaveError(null);
    setWillRetry(false);
  }, []);

  const fail = useCallback((err: unknown, retrying: boolean) => {
    lastErrorRef.current = err;
    setSaveStatus('error');
    setSaveError(userMessage(err));
    setWillRetry(retrying);
  }, []);

  const adopt = useCallback((id: string, at: number) => {
    draftIdRef.current = id;
    closedRef.current = false;
    supportedRef.current = true;
    setSupported(true);
    setDraftId(id);
    setSavedAt(at);
    setSaveStatus('saved');
    saveDraftPointer(id, at);
  }, []);

  const lose = useCallback((reason: DraftLostReason, id: string) => {
    // Closed for the rest of the page's life: the local branch of the
    // wizard's autosave must not POST a replacement for a row another tab
    // just finished or discarded.
    closedRef.current = true;
    pendingRef.current = null;
    clearTimers();
    clearDraftPointer();
    draftIdRef.current = null;
    setDraftId(null);
    setSaveStatus('idle');
    setSaveError(null);
    setWillRetry(false);
    onLostRef.current?.(reason, id);
  }, [clearTimers]);

  // The serial queue: one PATCH at a time; whatever arrived meanwhile goes next.
  const pump = useCallback((): void => {
    const id = draftIdRef.current;
    const payload = pendingRef.current;
    if (!id || !payload || inFlightRef.current || closedRef.current) return;
    pendingRef.current = null;
    setSaveStatus('saving');
    inFlightRef.current = api.deals
      .update(id, { ...payload.body, draft_wizard: payload.wizard })
      .then(() => {
        lastSentKeyRef.current = payload.key;
        retryAttemptsRef.current = 0;
        ok();
        const now = Date.now();
        setSavedAt(now);
        saveDraftPointer(id, now);
        if (!pendingRef.current) setSaveStatus('saved');
      })
      .catch((err: unknown) => {
        const lost = lostReasonFor(err);
        if (lost) {
          lose(lost, id);
          return;
        }
        // Nothing newer arrived → the failed content stays pending, so the
        // next edit (or the retry) sends it.
        if (!pendingRef.current) pendingRef.current = payload;
        const retryable = isRetryableSaveError(err) && retryAttemptsRef.current < DRAFT_RETRY_MAX_ATTEMPTS;
        fail(err, retryable);
        if (retryable && !retryRef.current) {
          const delay = retryDelayMs(retryAttemptsRef.current);
          retryAttemptsRef.current += 1;
          retryRef.current = setTimeout(() => {
            retryRef.current = null;
            pump();
          }, delay);
        }
      })
      .finally(() => {
        inFlightRef.current = null;
        // Content that arrived during the flight goes next — unless this
        // flight failed and the failure is waiting on its backoff (or is not
        // retryable at all): a new edit re-enters through ``save``.
        if (pendingRef.current && !retryRef.current && !lastErrorRef.current) pump();
      });
  }, [lose, ok, fail]);

  const save = useCallback((body: DraftDealPatch, wizard: DraftWizardPayload) => {
    if (!draftIdRef.current || closedRef.current) return;
    const key = serialize(body, wizard);
    // Nothing changed since the row last took a save → no request.
    if (key === lastSentKeyRef.current && !pendingRef.current && !inFlightRef.current) return;
    // Same content as the one already pending (a re-render, not an edit).
    if (pendingRef.current?.key === key) return;
    pendingRef.current = { body, wizard, key };
    // A new edit is new content: forget the previous failure's backoff.
    retryAttemptsRef.current = 0;
    if (retryRef.current) { clearTimeout(retryRef.current); retryRef.current = null; }
    setSaveStatus('saving');
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      pump();
    }, DRAFT_DEBOUNCE_MS);
  }, [pump]);

  const prime = useCallback((body: DraftDealPatch, wizard: DraftWizardPayload) => {
    if (!pendingRef.current && !inFlightRef.current) lastSentKeyRef.current = serialize(body, wizard);
  }, []);

  const flush = useCallback(async () => {
    if (debounceRef.current) { clearTimeout(debounceRef.current); debounceRef.current = null; }
    if (retryRef.current) { clearTimeout(retryRef.current); retryRef.current = null; }
    // At most two rounds: the request in flight, then anything that arrived
    // during it. A change still pending after that is a failed save.
    for (let round = 0; round < 2 && (inFlightRef.current || pendingRef.current); round++) {
      if (!inFlightRef.current) pump();
      await inFlightRef.current;
    }
    if (pendingRef.current && lastErrorRef.current) throw lastErrorRef.current;
  }, [pump]);

  const probe = useCallback(async (): Promise<boolean> => {
    if (supportedRef.current != null) return supportedRef.current;
    if (!isWorkerConnected()) {
      supportedRef.current = false;
      setSupported(false);
      return false;
    }
    let can = false;
    try {
      const r = await api.deals.pipeline({ limit: 1 });
      can = typeof r?.summary?.draft_count === 'number';
    } catch {
      can = false;
    }
    // ``load()`` may have answered meanwhile.
    if (supportedRef.current == null) {
      supportedRef.current = can;
      setSupported(can);
    }
    return supportedRef.current ?? can;
  }, []);

  const create = useCallback((body: DraftDealPatch, wizard: DraftWizardPayload): Promise<string | null> => {
    if (closedRef.current) return Promise.resolve(null);
    if (draftIdRef.current) return Promise.resolve(draftIdRef.current);
    if (createPromiseRef.current) return createPromiseRef.current;
    if (createRetryRef.current) { clearTimeout(createRetryRef.current); createRetryRef.current = null; }
    lastCreateArgsRef.current = { body, wizard };
    setSaveStatus('saving');
    const p = api.deals
      .create({ ...body, name: body.name ?? '', lifecycle: 'draft', draft_wizard: wizard })
      .then((deal) => {
        if (closedRef.current) {
          // Lost while the POST was out (another tab's row vanished): do not
          // adopt a replacement. The row exists on the worker as a draft the
          // Projects list shows; nothing here points at it.
          return null;
        }
        if (deal.lifecycle !== 'draft') {
          // The worker did not store a draft — it cannot hold one. Stop
          // trying for this session; the wizard stays browser-local.
          supportedRef.current = false;
          setSupported(false);
          setSaveStatus('idle');
          return null;
        }
        const now = Date.now();
        lastSentKeyRef.current = serialize(body, wizard);
        createAttemptsRef.current = 0;
        ok();
        adopt(deal.id, now);
        return deal.id;
      })
      .catch((err: unknown) => {
        const retryable = isRetryableSaveError(err) && createAttemptsRef.current < DRAFT_RETRY_MAX_ATTEMPTS;
        fail(err, retryable);
        if (retryable) {
          // Try again with the latest content the page handed us.
          const delay = retryDelayMs(createAttemptsRef.current);
          createAttemptsRef.current += 1;
          createRetryRef.current = setTimeout(() => {
            createRetryRef.current = null;
            const args = lastCreateArgsRef.current;
            if (args && !draftIdRef.current && !closedRef.current) void create(args.body, args.wizard);
          }, delay);
        }
        // A 4xx is not retried here; the page's next autosave (new content)
        // may call create() again.
        return null;
      })
      .finally(() => {
        createPromiseRef.current = null;
      });
    createPromiseRef.current = p;
    return p;
  }, [adopt, ok, fail]);

  const activate = useCallback(async (): Promise<WorkerDeal> => {
    const id = draftIdRef.current;
    if (!id) throw new Error('no draft to finish');
    await flush();
    const deal = await api.deals.activate(id);
    closedRef.current = true;
    clearTimers();
    clearDraftPointer();
    return deal;
  }, [flush, clearTimers]);

  const reset = useCallback(() => {
    clearTimers();
    pendingRef.current = null;
    lastSentKeyRef.current = null;
    lastErrorRef.current = null;
    lastCreateArgsRef.current = null;
    createPromiseRef.current = null;
    retryAttemptsRef.current = 0;
    createAttemptsRef.current = 0;
    closedRef.current = false;
    draftIdRef.current = null;
    setDraftId(null);
    setSavedAt(null);
    setSaveStatus('idle');
    setSaveError(null);
    setWillRetry(false);
  }, [clearTimers]);

  const discard = useCallback(async () => {
    const id = draftIdRef.current;
    if (!id) return;
    clearTimers();
    pendingRef.current = null;
    closedRef.current = true;
    try {
      await inFlightRef.current;
    } catch {
      /* a failed last save does not block the discard */
    }
    try {
      await api.deals.discardDraft(id);
    } catch (err) {
      const lost = lostReasonFor(err);
      if (lost === 'not_found') {
        // Already gone — the outcome the analyst asked for.
      } else {
        closedRef.current = false;
        throw err;
      }
    }
    clearDraftPointer();
    reset();
  }, [clearTimers, reset]);

  const load = useCallback(async (id: string): Promise<DraftLoadResult | null> => {
    let deal: WorkerDeal;
    try {
      deal = await api.deals.getDraft(id);
    } catch (err) {
      clearDraftPointer();
      onLostRef.current?.(lostReasonFor(err) ?? 'not_found', id);
      return null;
    }
    if (deal.lifecycle === undefined) {
      // No lifecycle on the row: this worker cannot hold drafts.
      supportedRef.current = false;
      setSupported(false);
      clearDraftPointer();
      onLostRef.current?.('unsupported', id);
      return null;
    }
    if (deal.lifecycle !== 'draft') {
      clearDraftPointer();
      onLostRef.current?.('not_draft', id);
      return null;
    }
    const at = deal.updated_at ? new Date(deal.updated_at).getTime() : Date.now();
    adopt(deal.id, Number.isFinite(at) ? at : Date.now());
    return { deal, wizard: deal.draft_wizard ?? null };
  }, [adopt]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  return useMemo<ServerDraft>(() => ({
    draftId, saveStatus, saveError, willRetry, savedAt, supported,
    probe, create, save, prime, flush, activate, discard, load, reset,
  }), [draftId, saveStatus, saveError, willRetry, savedAt, supported, probe, create, save, prime, flush, activate, discard, load, reset]);
}
