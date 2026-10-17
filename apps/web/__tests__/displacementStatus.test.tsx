/**
 * FON-89 — the Year-1 displacement verdict is printed, verbatim, where the
 * analyst looks: the CAPEX Renovation Budget row and the Future P&L Year-1
 * basis line. The worker computes it AFTER every override is applied
 * (`assumption_sources.displacement_status`); the browser adds a tone and
 * nothing else — no number on screen is composed here.
 *
 *   • `displacementStatusLine` — tone per state; worker sentence untouched;
 *     null when there is nothing honest to print.
 *   • `ProvenanceProvider` feeds `useDisplacementStatus` from the payload,
 *     through its own context, so harnesses that mock `useDealProvenance`
 *     down to `{ useSource }` render the consumers with nothing printed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { AssumptionSourcesResponse, DisplacementStatus } from '@/lib/api';

const fx = vi.hoisted(() => ({
  assumptionSources: vi.fn<() => Promise<AssumptionSourcesResponse>>(),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, assumptionSources: fx.assumptionSources },
    },
  };
});

import { displacementStatusLine, useDisplacementStatus } from '@/lib/hooks/useDisplacementStatus';
import { ProvenanceProvider } from '@/lib/hooks/useDealProvenance';

// Sam, 2026-10-15 — a renovation budget (here a PIP document's) never
// displaces Year 1 by itself; the worker says so and points at the inputs.
const OFF_NO_ASSUMPTION: DisplacementStatus = {
  active: false,
  state: 'off_no_assumption',
  source: 'pip_document',
  detail:
    'No Year-1 renovation impact — set rooms offline / rate displacement assumptions to model one. The renovation budget is a capital cost only; it does not change Year-1 occupancy or ADR.',
  renovation_budget: 7_100_000,
  renovation_budget_per_key: 71_000,
  y1_occupancy_displacement_pct: 0,
  y1_adr_displacement_pct: 0,
};

const ON_ANALYST: DisplacementStatus = {
  active: true,
  state: 'on_analyst',
  source: 'pip_document',
  detail:
    'Year-1 renovation impact on — analyst-set displacement assumptions (occupancy −10%, ADR −5%).',
  renovation_budget: 7_100_000,
  renovation_budget_per_key: 71_000,
  y1_occupancy_displacement_pct: 0.1,
  y1_adr_displacement_pct: 0.05,
};

const payload = (displacement_status: DisplacementStatus | null | undefined): AssumptionSourcesResponse => ({
  id: 'deal-uuid-1',
  sources: { renovation_budget: 'analyst_override' },
  values: { renovation_budget: 9_500_000 },
  ...(displacement_status === undefined ? {} : { displacement_status }),
});

function Probe() {
  const line = displacementStatusLine(useDisplacementStatus());
  return line ? (
    <p data-testid="probe" data-state={line.state} data-tone={line.tone}>
      {line.text}
    </p>
  ) : (
    <p data-testid="probe-empty">nothing</p>
  );
}

describe('displacementStatusLine', () => {
  it('keeps the worker sentence verbatim; an undisplaced Year 1 is muted, never a warning', () => {
    const line = displacementStatusLine(OFF_NO_ASSUMPTION);
    expect(line).toEqual({
      tone: 'muted',
      state: 'off_no_assumption',
      text: OFF_NO_ASSUMPTION.detail,
    });
  });

  it('tones every active state "on" and every off state "muted"', () => {
    expect(displacementStatusLine(ON_ANALYST)?.tone).toBe('on');
    expect(displacementStatusLine({ ...ON_ANALYST, state: 'on_pip_schedule' })?.tone).toBe('on');
    expect(displacementStatusLine({ ...OFF_NO_ASSUMPTION, state: 'off_analyst' })?.tone).toBe('muted');
  });

  it('prints nothing when the worker sent nothing (old worker, loading, blank detail)', () => {
    expect(displacementStatusLine(null)).toBeNull();
    expect(displacementStatusLine(undefined)).toBeNull();
    expect(displacementStatusLine({ ...ON_ANALYST, detail: '   ' })).toBeNull();
  });
});

describe('ProvenanceProvider → useDisplacementStatus', () => {
  beforeEach(() => {
    fx.assumptionSources.mockReset();
  });
  afterEach(cleanup);

  it('serves the payload verdict to consumers under the provider', async () => {
    fx.assumptionSources.mockResolvedValue(payload(OFF_NO_ASSUMPTION));
    render(
      <ProvenanceProvider dealId="deal-uuid-1">
        <Probe />
      </ProvenanceProvider>,
    );
    const el = await waitFor(() => screen.getByTestId('probe'));
    expect(el.getAttribute('data-state')).toBe('off_no_assumption');
    expect(el.getAttribute('data-tone')).toBe('muted');
    expect(el.textContent).toBe(OFF_NO_ASSUMPTION.detail);
    // Never implies the budget alone causes an impact.
    expect(el.textContent).not.toMatch(/displacement on|−15%|−8%/);
    expect(fx.assumptionSources).toHaveBeenCalledTimes(1);
  });

  it('says explicit analyst displacement assumptions are applied when the worker says so', async () => {
    fx.assumptionSources.mockResolvedValue(payload(ON_ANALYST));
    render(
      <ProvenanceProvider dealId="deal-uuid-1">
        <Probe />
      </ProvenanceProvider>,
    );
    const el = await waitFor(() => screen.getByTestId('probe'));
    expect(el.getAttribute('data-state')).toBe('on_analyst');
    expect(el.getAttribute('data-tone')).toBe('on');
    expect(el.textContent).toContain('occupancy −10%, ADR −5%');
  });

  it('stays silent on a worker that does not emit the field', async () => {
    fx.assumptionSources.mockResolvedValue(payload(undefined));
    render(
      <ProvenanceProvider dealId="deal-uuid-1">
        <Probe />
      </ProvenanceProvider>,
    );
    await waitFor(() => expect(fx.assumptionSources).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('probe-empty')).toBeTruthy();
    expect(screen.queryByTestId('probe')).toBeNull();
  });

  it('is null with no provider at all (the mocked-harness case)', () => {
    render(<Probe />);
    expect(screen.getByTestId('probe-empty')).toBeTruthy();
  });
});
