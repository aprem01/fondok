/**
 * "Duplicate deal" — the confirm copy must say what is copied and that the
 * model has to be re-run, and the project header must wire the action to
 * POST /deals/{id}/duplicate and navigate to the new deal. The copied /
 * skipped lists are read from the worker route so the prompt cannot drift
 * from what the API actually does.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { duplicateDealConfirmText, duplicateDealDoneText } from '@/lib/duplicateDeal';

const root = join(__dirname, '..');
const worker = readFileSync(
  join(root, '..', 'worker', 'app', 'api', 'deal_duplicate.py'),
  'utf8',
);
const page = readFileSync(join(root, 'src', 'app', 'projects', '[id]', 'page.tsx'), 'utf8');
const api = readFileSync(join(root, 'src', 'lib', 'api.ts'), 'utf8');

describe('duplicate deal confirm copy', () => {
  const text = duplicateDealConfirmText('Kimpton Miami Beach');

  it('names the deal and what is copied', () => {
    expect(text).toContain('"Kimpton Miami Beach"');
    for (const part of [
      'assumptions and their notes',
      'scenarios',
      'cell comments',
      'latest extraction',
      'fields you reviewed',
      'shared, not uploaded again',
    ]) {
      expect(text).toContain(part);
    }
  });

  it('says model runs are not copied and the model must be run', () => {
    expect(text).toContain('Not copied: model runs');
    expect(text).toContain('until you run the model');
    expect(duplicateDealDoneText('X (copy 2026-10-16)')).toContain('Run the model');
  });

  it('matches what the worker route copies and skips', () => {
    expect(worker).toMatch(/"engine_outputs",/);
    expect(worker).toMatch(/"audit_log",/);
    for (const table of ['documents', 'extraction_results', 'scenarios', 'cell_comments']) {
      expect(worker).toContain(`copied["${table}"]`);
    }
  });
});

describe('duplicate deal wiring', () => {
  it('api client posts to the duplicate route', () => {
    expect(api).toContain("request<DuplicateDealResponse>('POST', `/deals/${id}/duplicate`)");
  });

  it('project header offers the action, confirms, and navigates to the copy', () => {
    expect(page).toContain("'Duplicate deal'");
    expect(page).toContain('window.confirm(duplicateDealConfirmText(name))');
    expect(page).toContain('workerApi.deals.duplicate(rawId)');
    expect(page).toContain('router.push(`/projects/${res.deal.id}`)');
  });
});
