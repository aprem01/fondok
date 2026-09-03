/**
 * Returns tab — split-headline fix + ephemeral sandbox (FON-68 step 3).
 *
 * Two contracts locked here:
 *
 *  1. SPLIT-HEADLINE REGRESSION — the headline IRR / Equity Multiple / CoC all
 *     resolve from the SAME mocked worker ``returns`` output. CoC in particular
 *     must read ``year_one_coc`` (not the old, always-missing
 *     ``cash_on_cash_year_one``, which silently fell back to the client TS
 *     model and split the headline). ReturnsTab no longer consumes the
 *     assumptions provider, so there is no TS fallback to leak in.
 *
 *  2. EPHEMERAL SANDBOX — the Live Assumptions sliders are LOCAL state. Moving
 *     one raises the "Sensitivity override active" guardrail banner and calls
 *     the NON-persisting preview endpoint; "Reset to base case" restores the
 *     base and clears the banner. No persisting engine run is ever invoked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
}));

// The worker outputs under test. The headline + sandbox base both read from
// these; ``getEngineField`` (the real one) is exercised end-to-end.
const OUTPUTS = {
  deal_id: 'deal-1',
  engines: {
    returns: {
      deal_id: 'deal-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: {
        levered_irr: 0.2301,
        equity_multiple: 2.37,
        year_one_coc: 0.081,
        gross_sale_price: 52_000_000,
        hold_years: 5,
        exit_cap_rate: 0.07,
      },
      inputs: {
        assumptions: {
          exit_cap_rate: 0.07,
          revpar_growth: 0.045,
          hold_years: 5,
          ltv: 0.65,
          interest_rate: 0.068,
        },
      },
      error: null,
      runtime_ms: 12,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    debt: {
      deal_id: 'deal-1',
      engine: 'debt',
      status: 'complete',
      summary: '',
      outputs: { year_one_dscr: 1.45, interest_rate: 0.068 },
      inputs: {},
      error: null,
      runtime_ms: 8,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

// Keep the REAL getEngineField (the field-name fix is what's under test); only
// swap the hook to hand back our fixture instead of hitting the worker.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<
    typeof import('@/lib/hooks/useEngineOutputs')
  >('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

// Spy on the api surface. ``returnsPreview`` is the non-persisting sandbox
// call; ``runOne`` / ``runAll`` are the persisting ones we assert are NEVER hit.
const previewSpy = vi.fn(async () => ({
  deal_id: 'deal-1',
  levered_irr: 0.1902,
  unlevered_irr: 0.14,
  equity_multiple: 2.05,
  year_one_coc: 0.079,
  exit_value: 46_000_000,
  net_proceeds: 20_000_000,
  dscr_y1: 1.38,
  hold_years: 5,
  exit_cap_rate: 0.09,
  sensitivity: null,
}));
const runOneSpy = vi.fn();
const runAllSpy = vi.fn();

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: {
        ...actual.api.engines,
        returnsPreview: (...args: unknown[]) => previewSpy(...(args as [])),
        runOne: (...args: unknown[]) => runOneSpy(...(args as [])),
        runAll: (...args: unknown[]) => runAllSpy(...(args as [])),
      },
    },
  };
});

// Trim the heavy tab chrome to nothing — the test only cares about the
// headline KPIs + the Live Assumptions card.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/PricingSensitivityPanel', () => ({ default: () => null }));
vi.mock('@/components/project/MaxPricePanel', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/MetricLabel', () => ({
  MetricLabel: ({ label }: { label: string }) =>
    React.createElement('span', null, label),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ReturnsTab from '@/components/project/ReturnsTab';

beforeEach(() => {
  cleanup();
  previewSpy.mockClear();
  runOneSpy.mockClear();
  runAllSpy.mockClear();
});

describe('ReturnsTab headline (split-headline regression)', () => {
  it('resolves IRR, Equity Multiple AND CoC from the same worker output', () => {
    render(<ReturnsTab />);
    // IRR + EM from the worker.
    expect(screen.getByText('23.01%')).toBeInTheDocument();
    expect(screen.getByText('2.37x')).toBeInTheDocument();
    // CoC reads year_one_coc (0.081 → 8.10%). If the old field name regressed,
    // this would render 0.00% from a null fallback.
    expect(screen.getByText('8.10%')).toBeInTheDocument();
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
    // Exit value + DSCR also come off the worker (returns / debt).
    expect(screen.getByText('$52.00M')).toBeInTheDocument();
    expect(screen.getByText('1.45x')).toBeInTheDocument();
  });
});

describe('ReturnsTab ephemeral sandbox', () => {
  it('starts on the base case with no override banner', () => {
    render(<ReturnsTab />);
    expect(
      screen.queryByText(/Sensitivity override active/i),
    ).not.toBeInTheDocument();
  });

  it('raises the guardrail banner + calls the non-persisting preview on slider change', async () => {
    render(<ReturnsTab />);
    const sliders = screen.getAllByRole('slider');
    expect(sliders.length).toBe(5);
    // Drag Exit Cap Rate off its 0.07 base.
    fireEvent.change(sliders[0], { target: { value: '0.09' } });

    // Guardrail banner appears while the sandbox differs from base.
    expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument();
    expect(
      screen.getByText(/canonical assumptions in Investment and Debt are unchanged/i),
    ).toBeInTheDocument();

    // The debounced sandbox call hits the NON-persisting preview endpoint only.
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    expect(runOneSpy).not.toHaveBeenCalled();
    expect(runAllSpy).not.toHaveBeenCalled();
  });

  it('"Reset to base case" restores base and clears the banner without persisting', async () => {
    render(<ReturnsTab />);
    const sliders = screen.getAllByRole('slider');
    fireEvent.change(sliders[0], { target: { value: '0.09' } });
    expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument();

    // Reset — the banner's own button (there are two reset buttons when dirty).
    fireEvent.click(screen.getAllByText('Reset to base case')[0]);

    await waitFor(() =>
      expect(
        screen.queryByText(/Sensitivity override active/i),
      ).not.toBeInTheDocument(),
    );
    // Slider is back on the 0.07 base value.
    expect((screen.getAllByRole('slider')[0] as HTMLInputElement).value).toBe(
      '0.07',
    );
    // Never persisted anything.
    expect(runOneSpy).not.toHaveBeenCalled();
    expect(runAllSpy).not.toHaveBeenCalled();
  });
});
