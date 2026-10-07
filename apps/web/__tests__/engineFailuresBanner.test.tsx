/**
 * E-023 (FON-63) — the engine-failure banner must show the worker's
 * plain-language sentence VERBATIM and tuck the raw pydantic text under the
 * "Technical detail" disclosure.
 *
 * Two external testers hit a Debt failure on a negative year-1 NOI and saw
 * only "The model computed a value outside its expected range…" — no input,
 * no year, no range, and nothing saying their saved assumptions survived.
 * The worker now writes the sentence; the banner's job is to not get in its
 * way. The keyword heuristics stay only for errors the worker did not phrase.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';
import type {
  EngineName,
  EngineOutputResponse,
  EngineOutputsResponse,
  EngineStatus,
} from '@/lib/api';

const run = vi.hoisted(() => vi.fn(async () => undefined));

vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({
    run,
    status: 'idle',
    output: null,
    summary: '',
    error: null,
    complete: false,
    activeRunId: null,
  }),
}));

import {
  EngineFailuresBanner,
  humanizeEngineError,
} from '@/components/project/EngineFailuresBanner';

/** Exactly what apps/worker/app/services/engine_runner.py writes for E-023. */
const DEBT_SENTENCE =
  'Debt: NOI for year 1 is −$4,879,453, below the $0 minimum the Debt model accepts. ' +
  'The model stops here and Returns, Sensitivity, Partnership and Cash Flow were not run. ' +
  'Your saved assumptions were kept. ' +
  'Check key count, revenue base, expense base, or a starting-occupancy override, then re-run.';

const RAW_PYDANTIC =
  '1 validation error for DebtEngineInputExt\n' +
  'noi_by_year.0\n' +
  '  Input should be greater than or equal to 0 [type=greater_than_equal, input_value=-4879452.5, input_type=float]\n' +
  '    For further information visit https://errors.pydantic.dev/2.13/v/greater_than_equal';

const RUNNER_ERROR = `${DEBT_SENTENCE}\n\nTechnical detail: ${RAW_PYDANTIC}`;

const LEGACY_ZERO_ERROR = 'ZeroDivisionError: float division by zero';

function row(
  engine: EngineName,
  status: EngineStatus,
  error: string | null = null,
): EngineOutputResponse {
  return {
    deal_id: 'deal-uuid-1',
    engine,
    status,
    summary: '',
    outputs: null,
    inputs: null,
    error,
    runtime_ms: 0,
    started_at: null,
    completed_at: null,
    run_id: 'run-1',
  };
}

function outputs(rows: EngineOutputResponse[]): EngineOutputsResponse {
  const engines = Object.fromEntries(
    rows.map((r) => [r.engine, r]),
  ) as EngineOutputsResponse['engines'];
  return { deal_id: 'deal-uuid-1', engines, stale_run: null };
}

beforeEach(() => { run.mockClear(); });
afterEach(cleanup);

describe('humanizeEngineError', () => {
  it('splits a runner-formatted error into the verbatim sentence and the technical remainder', () => {
    const out = humanizeEngineError(RUNNER_ERROR);
    expect(out.runnerFormatted).toBe(true);
    expect(out.message).toBe(DEBT_SENTENCE);
    expect(out.technical).toBe(RAW_PYDANTIC);
  });

  it('treats a known engine label prefix as runner-formatted even without technical detail', () => {
    const out = humanizeEngineError('Returns: exit_cap_rate = 0 is outside the allowed range (> 0).');
    expect(out.runnerFormatted).toBe(true);
    expect(out.message).toBe('Returns: exit_cap_rate = 0 is outside the allowed range (> 0).');
    expect(out.technical).toBeNull();
  });

  it('keeps the legacy heuristics for errors the worker did not phrase', () => {
    const zero = humanizeEngineError(LEGACY_ZERO_ERROR);
    expect(zero.runnerFormatted).toBe(false);
    expect(zero.message).toMatch(/A required input was zero/);
    expect(zero.technical).toBe(LEGACY_ZERO_ERROR);

    // An un-humanized pydantic text (older worker) still gets the old sentence.
    const oldWorker = humanizeEngineError(RAW_PYDANTIC);
    expect(oldWorker.runnerFormatted).toBe(false);
    expect(oldWorker.message).toMatch(/outside its expected range/);
    expect(oldWorker.technical).toBe(RAW_PYDANTIC);
  });

  it('falls back for an empty error', () => {
    expect(humanizeEngineError(null)).toEqual({
      message: 'The model hit an unexpected error.',
      technical: null,
      runnerFormatted: false,
    });
  });
});

describe('EngineFailuresBanner', () => {
  it('renders nothing when every engine completed', () => {
    const { container } = render(
      <EngineFailuresBanner
        outputs={outputs([row('debt', 'complete'), row('returns', 'complete')])}
        dealId="deal-uuid-1"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the worker sentence verbatim and puts the pydantic text under Technical detail', () => {
    render(
      <EngineFailuresBanner
        outputs={outputs([row('revenue', 'complete'), row('debt', 'failed', RUNNER_ERROR)])}
        dealId="deal-uuid-1"
      />,
    );

    expect(screen.getByText('The Debt model didn’t finish')).toBeInTheDocument();

    const item = screen.getByTestId('engine-failure-debt');
    // The full sentence is on screen, character for character …
    expect(item).toHaveTextContent(DEBT_SENTENCE);
    const lead = within(item).getByTestId('engine-failure-lead');
    const message = within(item).getByTestId('engine-failure-message');
    expect(`${lead.textContent} ${message.textContent}`).toBe(DEBT_SENTENCE);
    // … the engine label is not doubled ("Debt: Debt: …") …
    expect(item.textContent).not.toContain('Debt: Debt:');
    // … and the old generic sentence is gone.
    expect(screen.queryByText(/outside its expected range/)).toBeNull();
    expect(message.textContent).not.toContain('Technical detail');
    expect(message.textContent).not.toContain('greater_than_equal');

    // The raw pydantic text is in the <details> block, and only there.
    const technical = within(item).getByTestId('engine-failure-technical');
    expect(technical.closest('details')).not.toBeNull();
    expect(technical.textContent).toBe(RAW_PYDANTIC);
    expect(technical.textContent).not.toContain('Your saved assumptions were kept.');
    expect(within(item).getByText('Technical detail')).toBeInTheDocument();
  });

  it('still uses the legacy phrasing, with the full raw text as detail, for a non-runner error', () => {
    render(
      <EngineFailuresBanner
        outputs={outputs([row('capital', 'failed', LEGACY_ZERO_ERROR)])}
        dealId="deal-uuid-1"
      />,
    );
    const item = screen.getByTestId('engine-failure-capital');
    expect(within(item).getByTestId('engine-failure-lead')).toHaveTextContent('Sources & Uses:');
    expect(within(item).getByTestId('engine-failure-message')).toHaveTextContent(
      /A required input was zero/,
    );
    expect(within(item).getByTestId('engine-failure-technical').textContent).toBe(
      LEGACY_ZERO_ERROR,
    );
  });

  it('counts every failed row — the skipped dependants land alongside the cause', () => {
    render(
      <EngineFailuresBanner
        outputs={outputs([
          row('debt', 'failed', RUNNER_ERROR),
          row('returns', 'failed', 'skipped: upstream debt did not complete'),
        ])}
        dealId="deal-uuid-1"
      />,
    );
    expect(screen.getByText('2 models didn’t finish')).toBeInTheDocument();
    expect(screen.getByTestId('engine-failure-debt')).toHaveTextContent(DEBT_SENTENCE);
    expect(screen.getByTestId('engine-failure-returns')).toHaveTextContent(
      'Returns: skipped: upstream debt did not complete',
    );
  });

  it('Re-run posts a run-all', () => {
    render(
      <EngineFailuresBanner
        outputs={outputs([row('debt', 'failed', RUNNER_ERROR)])}
        dealId="deal-uuid-1"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Re-run models/ }));
    expect(run).toHaveBeenCalledTimes(1);
  });
});
