/**
 * "Duplicate deal" — copy text shared by the project-header dialog and its
 * tests. The worker route is POST /deals/{id}/duplicate
 * (apps/worker/app/api/deal_duplicate.py); keep these lists in step with what
 * that route copies and skips. Cell comments are the one per-copy choice
 * (``include_cell_comments``, default off — Sam picks on each copy).
 */

import { WorkerError } from './api';

export interface DuplicateDealChoices {
  /** Also copy the P&L cell comments (worker ``include_cell_comments``). */
  includeCellComments: boolean;
}

export const DEFAULT_DUPLICATE_CHOICES: DuplicateDealChoices = {
  includeCellComments: false,
};

/** What the copy gets, in the order the dialog lists them. */
export function duplicateDealCopiedItems(choices: DuplicateDealChoices): string[] {
  return [
    'the deal details',
    'your assumptions and their notes',
    'scenarios',
    ...(choices.includeCellComments
      ? ['P&L cell comments (each marked as copied from this deal)']
      : []),
    'every document with its latest extraction (including the fields you reviewed)',
  ];
}

/** What the copy does NOT get, in the order the dialog lists them. */
export function duplicateDealSkippedItems(choices: DuplicateDealChoices): string[] {
  return [
    'model runs',
    'the activity history',
    ...(choices.includeCellComments ? [] : ['P&L cell comments']),
  ];
}

function joinList(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}

/** The confirm summary shown before duplicating (also the dialog body). */
export function duplicateDealConfirmText(
  dealName: string,
  choices: DuplicateDealChoices = DEFAULT_DUPLICATE_CHOICES,
): string {
  return (
    `Duplicate "${dealName}"?\n\n` +
    `The copy gets: ${joinList(duplicateDealCopiedItems(choices))}. ` +
    'The files themselves are shared, not uploaded again.\n\n' +
    `Not copied: ${joinList(duplicateDealSkippedItems(choices))}. ` +
    'The new deal shows no results until you run the model on it.\n\n' +
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
