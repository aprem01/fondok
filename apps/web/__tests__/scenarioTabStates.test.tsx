/**
 * Go-live Wave 0 (D1 / D4) — Scenario Analysis tells three "no grids" states
 * apart instead of one "not computed" card:
 *
 *  • the engine-outputs read FAILED → an error card with Retry;
 *  • an engine FAILED on the run → the failure banner naming it and the reason;
 *  • the model genuinely has not run → the not-computed state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

const hoisted = vi.hoisted(() => ({
  outputs: null as unknown,
  error: null as string | null,
  refresh: vi.fn(async () => {}),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/api');
  return { ...actual, isWorkerConnected: () => true };
});
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: hoisted.outputs as EngineOutputsResponse | null,
      previous: null,
      loading: false,
      settled: true,
      error: hoisted.error,
      lastRunAt: null,
      refresh: hoisted.refresh,
    }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle' }),
}));

import ScenarioAnalysisTab from '@/components/project/ScenarioAnalysisTab';

beforeEach(() => {
  hoisted.outputs = null;
  hoisted.error = null;
  hoisted.refresh.mockClear();
});
afterEach(cleanup);

describe('ScenarioAnalysisTab — why there are no grids', () => {
  it('read failed → error card with Retry, not "not computed"', () => {
    hoisted.error = "Fondok's service didn't respond. Try again.";
    render(<ScenarioAnalysisTab dealId="deal-1" />);
    const card = screen.getByTestId('engine-outputs-error');
    expect(card).toHaveTextContent('Couldn’t load the sensitivity grids');
    expect(card).toHaveTextContent(/read failure, not a model that has yet to run/);
    expect(screen.queryByText('Scenario analysis not computed')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(hoisted.refresh).toHaveBeenCalledTimes(1);
  });

  it('an engine failed → the banner names the engine and the reason, not "not computed"', () => {
    hoisted.outputs = {
      deal_id: 'deal-1',
      engines: {},
      failures: [{ engine: 'sensitivity', reason: 'upstream_failed', message: 'Sensitivity: Returns did not finish, so nothing to flex.' }],
    } as unknown as EngineOutputsResponse;
    render(<ScenarioAnalysisTab dealId="deal-1" />);
    const banner = screen.getByTestId('engine-failures-banner');
    expect(banner).toHaveTextContent('The Sensitivity model didn’t finish');
    expect(banner).toHaveTextContent('reason: upstream failed');
    expect(screen.queryByText('Scenario analysis not computed')).toBeNull();
  });

  it('a healthy snapshot with no matrices → the not-computed state, no error, no banner', () => {
    hoisted.outputs = { deal_id: 'deal-1', engines: {} } as unknown as EngineOutputsResponse;
    render(<ScenarioAnalysisTab dealId="deal-1" />);
    expect(screen.getByText('Scenario analysis not computed')).toBeInTheDocument();
    expect(screen.queryByTestId('engine-outputs-error')).toBeNull();
    expect(screen.queryByTestId('engine-failures-banner')).toBeNull();
  });
});
