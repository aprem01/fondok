/**
 * "Duplicate deal" — the confirm copy must say what is copied and that the
 * model has to be re-run, and the project header must wire the action to
 * POST /deals/{id}/duplicate (with the per-copy cell-comment choice) and
 * navigate to the new deal. The copied / skipped lists are read from the
 * worker route so the prompt cannot drift from what the API actually does.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  DEFAULT_DUPLICATE_CHOICES,
  duplicateDealConfirmText,
  duplicateDealCopiedItems,
  duplicateDealDoneText,
  duplicateDealErrorText,
  duplicateDealSkippedItems,
} from '@/lib/duplicateDeal';
import { WorkerError } from '@/lib/api';

const root = join(__dirname, '..');
const worker = readFileSync(
  join(root, '..', 'worker', 'app', 'api', 'deal_duplicate.py'),
  'utf8',
);
const page = readFileSync(join(root, 'src', 'app', 'projects', '[id]', 'page.tsx'), 'utf8');
const api = readFileSync(join(root, 'src', 'lib', 'api.ts'), 'utf8');
const dialog = readFileSync(
  join(root, 'src', 'components', 'project', 'DuplicateDealDialog.tsx'),
  'utf8',
);
const methodology = readFileSync(join(root, 'src', 'app', 'methodology', 'page.tsx'), 'utf8');

describe('duplicate deal confirm copy', () => {
  it('defaults to NOT copying cell comments', () => {
    expect(DEFAULT_DUPLICATE_CHOICES).toEqual({ includeCellComments: false });
  });

  it('names the deal and what is copied (default: comments under "Not copied")', () => {
    const text = duplicateDealConfirmText('Kimpton Miami Beach');
    expect(text).toContain('"Kimpton Miami Beach"');
    for (const part of [
      'assumptions and their notes',
      'scenarios',
      'latest extraction',
      'fields you reviewed',
      'shared, not uploaded again',
    ]) {
      expect(text).toContain(part);
    }
    const [, copiedPara, skippedPara] = text.split('\n\n');
    expect(copiedPara).not.toContain('cell comments');
    expect(skippedPara).toContain('Not copied: model runs, the activity history, and P&L cell comments');
  });

  it('moves cell comments into the copied list when asked', () => {
    const choices = { includeCellComments: true };
    const text = duplicateDealConfirmText('Kimpton Miami Beach', choices);
    const [, copiedPara, skippedPara] = text.split('\n\n');
    expect(copiedPara).toContain('P&L cell comments (each marked as copied from this deal)');
    expect(skippedPara).toBe(
      'Not copied: model runs, and the activity history. The new deal shows no results until you run the model on it.',
    );
    expect(duplicateDealCopiedItems(choices)).toContain(
      'P&L cell comments (each marked as copied from this deal)',
    );
    expect(duplicateDealSkippedItems(choices)).toEqual(['model runs', 'the activity history']);
    expect(duplicateDealSkippedItems(DEFAULT_DUPLICATE_CHOICES)).toEqual([
      'model runs',
      'the activity history',
      'P&L cell comments',
    ]);
  });

  it('says model runs are not copied and the model must be run', () => {
    const text = duplicateDealConfirmText('X');
    expect(text).toContain('Not copied: model runs');
    expect(text).toContain('until you run the model');
    expect(duplicateDealDoneText('X (copy 2026-10-16)')).toContain('Run the model');
  });

  it('matches what the worker route copies, skips and lets the caller choose', () => {
    expect(worker).toMatch(/"engine_outputs",/);
    expect(worker).toMatch(/"audit_log",/);
    for (const table of ['documents', 'extraction_results', 'scenarios', 'cell_comments']) {
      expect(worker).toContain(`copied["${table}"]`);
    }
    // The per-copy switch, its default, the opt-out listing and the marker.
    expect(worker).toContain('include_cell_comments: bool = False');
    expect(worker).toContain('skipped.append("cell_comments")');
    expect(worker).toContain('"copied_from_deal_id": src');
    expect(worker).toContain('body: DuplicateDealRequest | None = None');
  });

  it('the methodology page describes the per-copy choice', () => {
    expect(methodology).toContain('Also copy P&amp;L cell comments');
    expect(methodology).toContain('off by default');
    expect(methodology).toContain('carrying the source deal&apos;s id');
  });
});

describe('duplicate deal wiring', () => {
  it('api client posts the per-copy choice to the duplicate route (default off)', () => {
    expect(api).toContain(
      "duplicate: (id: string, body: DuplicateDealRequest = { include_cell_comments: false }) =>",
    );
    expect(api).toContain("request<DuplicateDealResponse>('POST', `/deals/${id}/duplicate`, body)");
    expect(api).toContain('include_cell_comments: boolean;');
    expect(api).toContain('copied_from_deal_id?: string | null;');
  });

  it('project header offers the action, opens the dialog, and navigates to the copy', () => {
    expect(page).toContain("'Duplicate deal'");
    expect(page).not.toContain('window.confirm(duplicateDealConfirmText');
    expect(page).toContain('<DuplicateDealDialog');
    expect(page).toContain('setDuplicateOpen(true)');
    expect(page).toContain('include_cell_comments: choices.includeCellComments');
    expect(page).toContain('workerApi.deals.duplicate(rawId, {');
    expect(page).toContain('router.push(`/projects/${res.deal.id}`)');
  });

  it('the dialog has the checkbox, default off, and lists copied / not copied', () => {
    expect(dialog).toContain('Also copy P&amp;L cell comments');
    expect(dialog).toContain('DEFAULT_DUPLICATE_CHOICES.includeCellComments');
    expect(dialog).toContain('duplicateDealCopiedItems(choices)');
    expect(dialog).toContain('duplicateDealSkippedItems(choices)');
  });
});

// The worker's refusal reason rides in the WorkerError BODY ({"detail": …});
// the message is only "POST /deals/{id}/duplicate → 409". The toast must say
// why and what to do, not just the status. (Fix of 2026-10-17 — pinned.)
describe('duplicate deal failure text', () => {
  const REASON =
    'Some documents are still being processed (1 of 3). Wait for extraction to finish, then duplicate the deal.';

  it('surfaces the worker detail from a WorkerError body', () => {
    const err = new WorkerError('POST /deals/abc/duplicate → 409', 409, JSON.stringify({ detail: REASON }));
    const text = duplicateDealErrorText(err);
    expect(text).toContain('Duplicate failed');
    expect(text).toContain(REASON);
    expect(text).toContain('409');
  });

  it('falls back to the raw body, then the message, and never invents a reason', () => {
    expect(duplicateDealErrorText(new WorkerError('POST /deals/abc/duplicate → 502', 502, 'Bad gateway')))
      .toBe('Duplicate failed: Bad gateway (POST /deals/abc/duplicate → 502)');
    expect(duplicateDealErrorText(new WorkerError('POST /deals/abc/duplicate → 500', 500, '')))
      .toBe('Duplicate failed: POST /deals/abc/duplicate → 500');
    expect(duplicateDealErrorText(new Error('Failed to fetch'))).toBe('Duplicate failed: Failed to fetch');
    expect(duplicateDealErrorText('boom')).toBe('Duplicate failed: boom');
  });

  it('the project header uses it in the catch', () => {
    expect(page).toContain('toast(duplicateDealErrorText(err)');
  });
});
