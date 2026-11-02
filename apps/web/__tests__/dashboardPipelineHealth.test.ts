/**
 * Wave 5 — pipeline health counts the team-set statuses (Ready for Review,
 * IC Ready, IC Approved, IC Not Approved) by their own names; every other
 * deal is bucketed by what it has done (documents, model run).
 */
import { describe, it, expect } from 'vitest';
import { pipelineHealth } from '@/lib/dashboard';
import type { PipelineDealRow } from '@/lib/api';

const row = (over: Partial<PipelineDealRow>): PipelineDealRow =>
  ({ deal_id: 'd', name: 'n', state: 'READY', status: 'In Progress', document_count: 3,
    levered_irr: 0.2, last_engine_run_at: '2026-10-30T00:00:00Z', ...over }) as PipelineDealRow;

describe('pipelineHealth', () => {
  it('counts each team-set status by name and the rest by progress', () => {
    const health = pipelineHealth([
      row({ status: 'In Progress' }),
      row({ status: 'In Progress', document_count: 0, levered_irr: null, last_engine_run_at: null }),
      row({ status: 'Ready for Review' }),
      row({ status: 'IC Ready' }),
      row({ status: 'IC Ready' }),
      row({ status: 'IC Approved' }),
      row({ status: 'IC Not Approved' }),
    ]);
    expect(health).toEqual([
      { label: 'Modelled', n: 1 },
      { label: 'No documents yet', n: 1 },
      { label: 'Ready for Review', n: 1 },
      { label: 'IC Ready', n: 2 },
      { label: 'IC Approved', n: 1 },
      { label: 'IC Not Approved', n: 1 },
    ]);
  });

  it('a legacy label is not counted as IC Ready', () => {
    expect(pipelineHealth([row({ status: 'ic ready' })])).toEqual([{ label: 'Modelled', n: 1 }]);
  });
});
