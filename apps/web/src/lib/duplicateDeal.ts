/**
 * "Duplicate deal" — copy text shared by the project-header action and its
 * test. The worker route is POST /deals/{id}/duplicate
 * (apps/worker/app/api/deal_duplicate.py); keep this list in step with what
 * that route copies and skips.
 */

import { WorkerError } from './api';

/** The confirm prompt shown before duplicating. */
export function duplicateDealConfirmText(dealName: string): string {
  return (
    `Duplicate "${dealName}"?\n\n` +
    'The copy gets: the deal details, your assumptions and their notes, ' +
    'scenarios, cell comments, and every document with its latest ' +
    'extraction (including the fields you reviewed). The files themselves ' +
    'are shared, not uploaded again.\n\n' +
    'Not copied: model runs and the activity history. The new deal shows ' +
    'no results until you run the model on it.\n\n' +
    'The original deal is not changed.'
  );
}

/** Toast after a successful duplicate. */
export function duplicateDealDoneText(newName: string): string {
  return `Created "${newName}". Run the model on the copy to see results.`;
}

/**
 * The toast line for a failed duplicate. ``request()`` throws a
 * ``WorkerError`` whose ``message`` is only "POST /deals/{id}/duplicate → 409";
 * the worker's reason ("Some documents are still being processed (…). Wait
 * for extraction to finish, then duplicate the deal.") travels in ``body`` as
 * ``{"detail": "..."}``. Prefer that detail; fall back to the raw body, then
 * the message. Never invents a reason.
 */
export function duplicateDealErrorText(err: unknown): string {
  const prefix = 'Duplicate failed';
  if (err instanceof WorkerError) {
    const body = (err.body ?? '').trim();
    let detail = '';
    if (body) {
      try {
        const parsed = JSON.parse(body) as { detail?: unknown };
        if (typeof parsed?.detail === 'string') detail = parsed.detail.trim();
        else if (parsed?.detail != null) detail = JSON.stringify(parsed.detail);
      } catch {
        detail = body;
      }
    }
    return detail ? `${prefix}: ${detail} (${err.message})` : `${prefix}: ${err.message}`;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return `${prefix}: ${msg}`;
}
