/**
 * reextract — #18 "Re-run extraction" in the Data Room (pure helpers).
 *
 * Sam asked testers to re-run extraction on documents that were already
 * extracted (after a pipeline change). The worker side:
 *   • ``POST .../documents/{doc}/extract`` on an EXTRACTED / FAILED document
 *     is a re-extraction — always a fresh run under the current pipeline
 *     (force: no content-hash cache clone, no sibling-template reuse),
 *     audited as ``document.re_extract_requested`` with the caller;
 *   • ``POST .../documents/{doc}/reprocess`` re-parses a PARSE_FAILED /
 *     FAILED document from the stored file, then extracts (same audit,
 *     same force);
 *   • both refuse (409) a document that is still processing and younger
 *     than EXTRACTION_STUCK_MINUTES.
 *
 * What happens to the analyst's work (verified in the worker, 2026-10-15):
 *   • a re-run writes a NEW extraction_results row and every reader takes
 *     the newest one, so field reviews made on the old result — accepted,
 *     edited or rejected values (PATCH .../fields) — are NOT carried over;
 *     the old row stays in the database as history;
 *   • the document type / year the analyst set (user_provided_doc_type,
 *     fiscal_year) are kept as their input; a disagreement with the new
 *     read is flagged for confirmation, as on first extraction;
 *   • deal-level model overrides (with their justification notes) live on
 *     the deal, not on the extraction, and are untouched.
 *
 * Kept free of React so the rules are unit-testable.
 */

import { isProcessingStatus } from '@/lib/progress';

export type ReextractEndpoint = 'extract' | 'reprocess';

export interface ReextractEligibility {
  /** True when the action can be offered right now. */
  ok: boolean;
  /** Which worker route the re-run goes through (when ``ok``). */
  endpoint: ReextractEndpoint | null;
  /** Why the action is disabled (when ``!ok``) — shown as the tooltip. */
  reason: string | null;
}

/** Whether a document with this worker status can be re-run, and how. */
export function reextractEligibility(status: string | null | undefined): ReextractEligibility {
  const s = (status ?? '').toUpperCase();
  if (isProcessingStatus(s)) {
    return {
      ok: false,
      endpoint: null,
      reason: 'Still processing — wait for this document to finish before re-running it.',
    };
  }
  if (s === 'EXTRACTED') return { ok: true, endpoint: 'extract', reason: null };
  // A failed document re-parses from the stored file first: its parse
  // output may be missing or be the reason it failed.
  if (s === 'FAILED' || s === 'PARSE_FAILED') {
    return { ok: true, endpoint: 'reprocess', reason: null };
  }
  return {
    ok: false,
    endpoint: null,
    reason: s ? `Not available for a document in status ${s}.` : 'Document status unknown.',
  };
}

/** The bulk action's plan: which documents it re-runs, and why it is
 *  disabled when it is. Any document still processing disables the whole
 *  action (one batch at a time, and never a second pipeline on a file). */
export function planReextractAll(
  files: ReadonlyArray<{ id: string; status?: string | null }>,
): { ids: string[]; disabledReason: string | null } {
  const processing = files.filter((f) => isProcessingStatus(f.status));
  if (processing.length > 0) {
    return {
      ids: [],
      disabledReason: `${processing.length} document${processing.length === 1 ? ' is' : 's are'} still processing — wait for ${processing.length === 1 ? 'it' : 'them'} to finish.`,
    };
  }
  const ids = files.filter((f) => reextractEligibility(f.status).ok).map((f) => f.id);
  if (ids.length === 0) {
    return { ids, disabledReason: 'No extracted or failed documents to re-run.' };
  }
  return { ids, disabledReason: null };
}

/** The confirm step's copy — what a re-run actually does (see header). */
export const REEXTRACT_CONSEQUENCES: readonly string[] = [
  'Fondok reads the file again from the start under the current extraction pipeline. It does not reuse the saved result for these bytes, so this is a full extraction run, not a cached copy. A failed document is re-parsed from the stored file first.',
  'Field reviews made on the current result — values you accepted, edited or rejected — are not carried over. The new result replaces them, and anything it flags as low-confidence needs reviewing again. The previous result is kept as history, not deleted.',
  'The document type and year you set are kept. If the new read disagrees, Fondok flags it for you to confirm, as on first extraction.',
  'Model overrides you entered on the deal (with their justification notes) are not affected.',
  'Progress shows on each document. When the re-run finishes, the model re-runs once automatically.',
];

/** True once every document in a re-run batch has left the pipeline —
 *  i.e. it was seen in a processing status after the request, and is now
 *  terminal (EXTRACTED / FAILED / PARSE_FAILED), or it disappeared from the
 *  list (deleted). Docs never yet seen processing hold the batch open. */
export function reextractBatchDone(
  pending: ReadonlyMap<string, { seenProcessing: boolean }>,
  docs: ReadonlyArray<{ id: string; status?: string | null }>,
): boolean {
  if (pending.size === 0) return false;
  const byId = new Map(docs.map((d) => [d.id, d]));
  let done = true;
  pending.forEach((p, id) => {
    const d = byId.get(id);
    if (!d) return; // deleted mid-run — nothing to wait for
    if (isProcessingStatus(d.status)) {
      done = false;
      return;
    }
    if (!p.seenProcessing) done = false;
  });
  return done;
}

/** Why a re-run request was refused, in the analyst's words. The worker's
 *  409 here means "still processing" (FON-91), not the workspace 409 that
 *  ``shortReason`` describes; 410 means the stored file is gone. */
export function reextractFailureReason(err: unknown): string {
  const e = (err ?? {}) as { name?: string; status?: unknown };
  const status = typeof e.status === 'number' ? e.status : null;
  if (status === 409) return 'it is still processing — try again once it finishes';
  if (status === 410) return 'the stored file is gone — re-upload it';
  if (status === 404) return 'the document is no longer on this deal';
  if (status === 401) return 'your session has expired — sign in again';
  if (status === 0) return 'worker not connected';
  if (status != null) {
    return status >= 500 ? `the worker returned an error (${status})` : `the request failed (${status})`;
  }
  if (e.name === 'TimeoutError') return 'the worker took too long to respond';
  return 'unexpected error';
}
