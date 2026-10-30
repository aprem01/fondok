/**
 * Go-live Wave 0 (C5 / D1) — the Variance tab renders ONLY the worker's
 * report, and names its state when there is none.
 *
 *  • no worker URL → "Worker not configured" (never the former Kimpton flags);
 *  • fetch failed → an error card with Retry, never "awaiting inputs";
 *  • report with no flags → the worker's own note;
 *  • a report with flags → those flags, their count, and no fixture copy.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import React from 'react';
import type { VarianceFlag } from '@/lib/variance';

const hoisted = vi.hoisted(() => ({
  state: {
    flags: null as unknown[] | null,
    critical: 0,
    warn: 0,
    info: 0,
    note: null as string | null,
    loading: false,
    error: null as string | null,
    workerConfigured: true,
    refresh: () => {},
  },
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/hooks/useVariance', () => ({ useVariance: () => hoisted.state }));
vi.mock('@/components/citations/Citation', () => ({ Citation: () => null }));

import VarianceTab from '@/components/project/VarianceTab';

const FLAG: VarianceFlag = {
  flag_id: 'BROKER_VS_T12_NOI_VARIANCE-0',
  rule_id: 'BROKER_VS_T12_NOI_VARIANCE',
  severity: 'CRITICAL',
  metric: 'noi',
  field_label: 'NOI',
  broker_value: 5_200_000,
  t12_value: 4_281_000,
  variance_abs: 919_000,
  variance_pct: 0.2147,
  format: 'currency',
  broker_overstates: true,
  noi_impact_usd: 919_000,
  explanation: 'Broker NOI exceeds the T-12 by 21.5%.',
  recommended_action: 'Re-underwrite.',
  source_documents: [],
};

beforeEach(() => {
  hoisted.state = {
    flags: null, critical: 0, warn: 0, info: 0, note: null, loading: false, error: null,
    workerConfigured: true, refresh: vi.fn(),
  };
});
afterEach(cleanup);

describe('VarianceTab — states', () => {
  it('no worker URL → "Worker not configured", and no former fixture copy', () => {
    hoisted.state.workerConfigured = false;
    const { container } = render(<VarianceTab />);
    expect(screen.getByTestId('worker-not-configured')).toHaveTextContent(/variance report/);
    expect(container.textContent).not.toMatch(/coastal insurance|optimistic occupancy uplift|Offering_Memorandum_Final/);
    expect(container.textContent).not.toMatch(/\d+ variance flags?/);
  });

  it('fetch failed → an error card with Retry, not "awaiting inputs"', () => {
    const refresh = vi.fn();
    hoisted.state.error = "Fondok's service didn't respond. Try again.";
    hoisted.state.refresh = refresh;
    render(<VarianceTab />);
    const card = screen.getByTestId('variance-load-error');
    expect(card).toHaveTextContent('The variance report did not load');
    expect(card).toHaveTextContent("Fondok's service didn't respond");
    expect(screen.queryByText(/awaiting inputs/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('still loading → a loading state, not an empty report', () => {
    hoisted.state.loading = true;
    render(<VarianceTab />);
    expect(screen.getByTestId('variance-loading')).toBeInTheDocument();
    expect(screen.queryByText(/No flags yet/)).toBeNull();
  });

  it('a report with no flags → the worker’s own note', () => {
    hoisted.state.flags = [];
    hoisted.state.note = 'Only the T-12 has been extracted; upload the Offering Memorandum.';
    render(<VarianceTab />);
    expect(screen.getByText('Broker Variance — awaiting inputs')).toBeInTheDocument();
    expect(screen.getByText(/Only the T-12 has been extracted/)).toBeInTheDocument();
  });

  it('a report with flags → the flags, counted from the report, with the NOI variance from the NOI flag', () => {
    hoisted.state.flags = [FLAG];
    hoisted.state.critical = 1;
    const { container } = render(<VarianceTab />);
    expect(screen.getByText('1 Critical')).toBeInTheDocument();
    expect(container.textContent).toContain('1 variance flag');
    expect(container.textContent).toContain('Broker NOI exceeds the T-12 by 21.5%.');
    expect(container.textContent).toContain('$919K');
    expect(container.textContent).toContain('21.5% overstate');
    expect(container.textContent).not.toMatch(/coastal insurance|optimistic occupancy uplift/);
  });

  it('flags without a broker-vs-T-12 pair → NOI Variance "—", never "$0 · 0.0% overstate"', () => {
    hoisted.state.flags = [{
      ...FLAG,
      flag_id: 'INSURANCE_PER_KEY-0',
      rule_id: 'INSURANCE_PER_KEY',
      metric: 'insurance',
      field_label: 'Insurance',
      broker_value: undefined,
      t12_value: undefined,
      value: 4_200,
      variance_abs: undefined,
      variance_pct: undefined,
      noi_impact_usd: 0,
      explanation: 'Insurance per key is above the coastal band.',
    }];
    hoisted.state.critical = 1;
    const { container } = render(<VarianceTab />);
    expect(container.textContent).not.toMatch(/\$0\b/);
    expect(container.textContent).not.toMatch(/0\.0% overstate/);
    expect(container.textContent).toContain('No broker-vs-T-12 pair on this report');
    expect(container.textContent).not.toMatch(/Most material/);
  });
});
