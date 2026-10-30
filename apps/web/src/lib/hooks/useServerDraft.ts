'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
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
 *   - a failed PATCH is retried (``'error'`` → "Couldn't save — retrying"),
 *     except when the row is gone or no longer a draft (404 / 409): the
 *     pointer is cleared and ``onLost`` tells the page;
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
  /** Forget the draft locally (after Finish navigated, or a local Discard). */
  reset: () => void;
}

export interface UseServerDraftOptions {
  onLost?: (reason: DraftLostReason, id: string) => void;
}

/** A failed PATCH is retried after this long. */
export const DRAFT_RETRY_MS = 4000;

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

export function useServerDraft(options: UseServerDraftOptions = {}): ServerDraft {
  const [draftId, setDraftId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<DraftSaveStatus>('idle');
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
  const createPromiseRef = useRef<Promise<string | null> | null>(null);
  const createRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastCreateArgsRef = useRef<{ body: DraftDealPatch; wizard: DraftWizardPayload } | null>(null);
  /** Set once the row is gone / activated — no further PATCH may leave. */
  const closedRef = useRef(false);
  const onLostRef = useRef(options.onLost);
  onLostRef.current = options.onLost;

  const clearTimers = useCallback(() => {
    if (debounceRef.current) { clearTimeout(debounceRef.current); debounceRef.current = null; }
    if (retryRef.current) { clearTimeout(retryRef.current); retryRef.current = null; }
    if (createRetryRef.current) { clearTimeout(createRetryRef.current); createRetryRef.current = null; }
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
    closedRef.current = true;
    pendingRef.current = null;
    clearTimers();
    clearDraftPointer();
    draftIdRef.current = null;
    setDraftId(null);
    setSaveStatus('idle');
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
        lastErrorRef.current = null;
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
        lastErrorRef.current = err;
        setSaveStatus('error');
        // Nothing newer arrived → put this change back and retry it.
        if (!pendingRef.current) pendingRef.current = payload;
        if (!retryRef.current) {
          retryRef.current = setTimeout(() => {
            retryRef.current = null;
            pump();
          }, DRAFT_RETRY_MS);
        }
      })
      .finally(() => {
        inFlightRef.current = null;
        if (pendingRef.current && !retryRef.current) pump();
      });
  }, [lose]);

  const save = useCallback((body: DraftDealPatch, wizard: DraftWizardPayload) => {
    if (!draftIdRef.current || closedRef.current) return;
    const key = serialize(body, wizard);
    // Nothing changed since the row last took a save → no request.
    if (key === lastSentKeyRef.current && !pendingRef.current && !inFlightRef.current) return;
    pendingRef.current = { body, wizard, key };
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
    let ok = false;
    try {
      const r = await api.deals.pipeline({ limit: 1 });
      ok = typeof r?.summary?.draft_count === 'number';
    } catch {
      ok = false;
    }
    // ``load()`` may have answered meanwhile.
    if (supportedRef.current == null) {
      supportedRef.current = ok;
      setSupported(ok);
    }
    return supportedRef.current ?? ok;
  }, []);

  const create = useCallback((body: DraftDealPatch, wizard: DraftWizardPayload): Promise<string | null> => {
    if (draftIdRef.current) return Promise.resolve(draftIdRef.current);
    if (createPromiseRef.current) return createPromiseRef.current;
    if (createRetryRef.current) { clearTimeout(createRetryRef.current); createRetryRef.current = null; }
    lastCreateArgsRef.current = { body, wizard };
    setSaveStatus('saving');
    const p = api.deals
      .create({ ...body, name: body.name ?? '', lifecycle: 'draft', draft_wizard: wizard })
      .then((deal) => {
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
        lastErrorRef.current = null;
        adopt(deal.id, now);
        return deal.id;
      })
      .catch((err: unknown) => {
        lastErrorRef.current = err;
        setSaveStatus('error');
        // Try again with the latest content the page handed us.
        createRetryRef.current = setTimeout(() => {
          createRetryRef.current = null;
          const args = lastCreateArgsRef.current;
          if (args && !draftIdRef.current && !closedRef.current) void create(args.body, args.wizard);
        }, DRAFT_RETRY_MS);
        return null;
      })
      .finally(() => {
        createPromiseRef.current = null;
      });
    createPromiseRef.current = p;
    return p;
  }, [adopt]);

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
    closedRef.current = false;
    draftIdRef.current = null;
    setDraftId(null);
    setSavedAt(null);
    setSaveStatus('idle');
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
    draftId, saveStatus, savedAt, supported,
    probe, create, save, prime, flush, activate, discard, load, reset,
  }), [draftId, saveStatus, savedAt, supported, probe, create, save, prime, flush, activate, discard, load, reset]);
}
