/**
 * FON-75 — the page-level stale-run banner.
 *
 * The defect it exists for: on 2026-09-12 a healthy deal opened with the whole
 * Stabilization section of Overview rendering five dashes, because the deal's
 * persisted engine output had been written before `expense.stabilization`
 * existed. Nothing on screen said so.
 *
 * Three things are locked here:
 *   1. a current run shows nothing — a false positive telling an analyst to
 *      re-run an up-to-date model is the only way this feature can do harm;
 *   2. a run missing the expense block names the section a reader recognises
 *      ("Stabilization"), not the dotted path `expense.stabilization`;
 *   3. the Re-run button actually posts a run.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { REASONS } from '@/lib/ontology/reasons.generated';

const runAll = vi.hoisted(() => vi.fn(async () => ({
  deal_id: 'deal-uuid-1',
  run_id: 'run-1',
  started_at: new Date().toISOString(),
  engines: [],
})));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: { ...actual.api.engines, runAll, runStatus: vi.fn(async () => ({
        deal_id: 'deal-uuid-1', run_id: 'run-1', engines: [],
      })) },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import { StaleRunBanner } from '@/components/project/StaleRunBanner';

/** The engines map is irrelevant to this banner — only `stale_run` drives it. */
function outputs(staleRun: EngineOutputsResponse['stale_run']): EngineOutputsResponse {
  return {
    deal_id: 'deal-uuid-1',
    engines: {} as EngineOutputsResponse['engines'],
    stale_run: staleRun,
  };
}

beforeEach(() => { runAll.mockClear(); });
afterEach(cleanup);

describe('StaleRunBanner', () => {
  it('renders nothing when the run is current', () => {
    const { container } = render(
      <StaleRunBanner outputs={outputs(null)} dealId="deal-uuid-1" />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('stale-run-banner')).toBeNull();
  });

  it('renders nothing when there are no outputs at all', () => {
    const { container } = render(<StaleRunBanner outputs={null} dealId="deal-uuid-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the Stabilization SECTION, not the dotted field path', () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { expense: ['stabilization'] },
        })}
        dealId="deal-uuid-1"
      />,
    );

    const banner = screen.getByTestId('stale-run-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('Stabilization');
    // The reader never sees the engine-internal path.
    expect(banner.textContent).not.toContain('expense.stabilization');
    // The copy is the shared `stale_run` vocabulary, not banner-local prose.
    expect(banner.textContent).toContain(REASONS.stale_run.explanation);
  });

  it('falls back to the raw dotted path for a block with no label', () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { revenue: ['some_future_block'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    // Honest and greppable beats an invented product name.
    expect(screen.getByTestId('stale-run-sections').textContent).toContain(
      'revenue.some_future_block',
    );
  });

  it('collapses several missing paths onto one section name', () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { capital: ['sources', 'uses'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    const items = screen.getByTestId('stale-run-sections').querySelectorAll('li');
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toBe('Sources & Uses');
  });

  it('posts a run when the Re-run button is clicked', async () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { expense: ['stabilization'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /re-run models/i }));
    await waitFor(() => expect(runAll).toHaveBeenCalledWith('deal-uuid-1'));
  });
});
