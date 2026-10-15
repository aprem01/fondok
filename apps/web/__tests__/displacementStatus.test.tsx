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

const OFF_ANALYST_ENTRY: DisplacementStatus = {
  active: false,
  state: 'off_analyst_entry',
  source: 'analyst_override',
  detail:
    'Year-1 displacement off — the renovation budget is an analyst entry without an OM or PIP document; set the Y1 displacement % or upload the PIP.',
  renovation_budget: 9_500_000,
  renovation_budget_per_key: 71_969.7,
  y1_occupancy_displacement_pct: 0,
  y1_adr_displacement_pct: 0,
};

const ON_PIP: DisplacementStatus = {
  active: true,
  state: 'on_pip_document',
  source: 'pip_document',
  detail:
    'Year-1 displacement on — the renovation budget is read off a PIP / CapEx document (occupancy −15%, ADR −8%).',
  renovation_budget: 7_100_000,
  renovation_budget_per_key: 71_000,
  y1_occupancy_displacement_pct: 0.15,
  y1_adr_displacement_pct: 0.08,
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
  it('keeps the worker sentence verbatim and tones the analyst-entry state as attention', () => {
    const line = displacementStatusLine(OFF_ANALYST_ENTRY);
    expect(line).toEqual({
      tone: 'attention',
      state: 'off_analyst_entry',
      text: OFF_ANALYST_ENTRY.detail,
    });
  });

  it('tones every active state "on" and every other off state "muted"', () => {
    expect(displacementStatusLine(ON_PIP)?.tone).toBe('on');
    expect(displacementStatusLine({ ...ON_PIP, state: 'on_om_broker' })?.tone).toBe('on');
    expect(displacementStatusLine({ ...ON_PIP, state: 'on_analyst' })?.tone).toBe('on');
    expect(displacementStatusLine({ ...OFF_ANALYST_ENTRY, state: 'off_seed' })?.tone).toBe('muted');
    expect(displacementStatusLine({ ...OFF_ANALYST_ENTRY, state: 'off_immaterial' })?.tone).toBe('muted');
    expect(displacementStatusLine({ ...OFF_ANALYST_ENTRY, state: 'off_analyst' })?.tone).toBe('muted');
  });

  it('prints nothing when the worker sent nothing (old worker, loading, blank detail)', () => {
    expect(displacementStatusLine(null)).toBeNull();
    expect(displacementStatusLine(undefined)).toBeNull();
    expect(displacementStatusLine({ ...ON_PIP, detail: '   ' })).toBeNull();
  });
});

describe('ProvenanceProvider → useDisplacementStatus', () => {
  beforeEach(() => {
    fx.assumptionSources.mockReset();
  });
  afterEach(cleanup);

  it('serves the payload verdict to consumers under the provider', async () => {
    fx.assumptionSources.mockResolvedValue(payload(OFF_ANALYST_ENTRY));
    render(
      <ProvenanceProvider dealId="deal-uuid-1">
        <Probe />
      </ProvenanceProvider>,
    );
    const el = await waitFor(() => screen.getByTestId('probe'));
    expect(el.getAttribute('data-state')).toBe('off_analyst_entry');
    expect(el.getAttribute('data-tone')).toBe('attention');
    expect(el.textContent).toBe(OFF_ANALYST_ENTRY.detail);
    expect(fx.assumptionSources).toHaveBeenCalledTimes(1);
  });

  it('says a PIP document displaces (FON-101) when the worker says so', async () => {
    fx.assumptionSources.mockResolvedValue(payload(ON_PIP));
    render(
      <ProvenanceProvider dealId="deal-uuid-1">
        <Probe />
      </ProvenanceProvider>,
    );
    const el = await waitFor(() => screen.getByTestId('probe'));
    expect(el.getAttribute('data-state')).toBe('on_pip_document');
    expect(el.textContent).toContain('PIP / CapEx document');
    expect(el.textContent).toContain('occupancy −15%, ADR −8%');
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
