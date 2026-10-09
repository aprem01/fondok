/**
 * R-073 — Investment Bridge tab.
 *
 * The fixture is the worker's bridge for the Kimpton seed run (the same
 * figures `apps/worker/tests/test_investment_bridge.py` foots): $19.60M
 * invested → acquisition / renovation / operations / financing / exit →
 * $62.20M returned. The waterfall must foot to the engine's own total, and a
 * leg the run cannot support must render "—" with its reason, never $0.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';
import type { InvestmentBridgeResponse } from '@/lib/api';
import { layoutBridge } from '@/lib/investmentBridge';

const KIMPTON: InvestmentBridgeResponse = {
  deal_id: 'deal-1',
  available: true,
  reason: null,
  equity_invested: 19_602_900,
  equity_invested_source: '−returns.cash_flows[0] (= capital.equity_amount)',
  equity_returned: 62_201_444,
  equity_returned_source: 'Σ returns.cash_flows[1:]',
  equity_profit: 42_598_544,
  hold_years: 5,
  legs: [
    { key: 'acquisition', label: 'Acquisition', value: -37_628_000, status: 'ok', formula: 'f', reason: null, components: [
      { label: 'Purchase Price', value: -36_400_000, source: 'capital.uses[Purchase Price]' },
      { label: 'Closing Costs', value: -728_000, source: 'capital.uses[Closing Costs]' },
      { label: 'Working Capital', value: -500_000, source: 'capital.uses[Working Capital]' },
    ] },
    { key: 'renovation', label: 'Renovation / PIP', value: -5_280_000, status: 'ok', formula: 'f', reason: null, components: [
      { label: 'Renovation', value: -5_280_000, source: 'capital.uses[Renovation]' },
    ] },
    { key: 'operations', label: 'Operations', value: 21_811_087, status: 'ok', formula: 'f', reason: null, components: [
      { label: 'Year 1 NOI', value: 21_811_087, source: 'returns.noi_by_year[0]' },
    ] },
    { key: 'financing', label: 'Financing', value: -8_172_896, status: 'ok', formula: 'f', reason: null, components: [
      { label: 'Loan proceeds', value: 23_660_000, source: 'returns.inputs.loan_amount' },
      { label: 'Loan payoff at exit', value: -31_832_896, source: 'returns.inputs.loan_balance_at_exit' },
    ] },
    { key: 'exit', label: 'Exit', value: 71_868_353, status: 'ok', formula: 'f', reason: null, components: [
      { label: 'Gross sale price', value: 73_335_054, source: 'returns.gross_sale_price' },
      { label: 'Selling costs', value: -1_466_701, source: 'returns.selling_costs' },
    ] },
  ],
  unavailable: [],
  computed_equity_returned: 62_201_444,
  residual: 0,
  reconciles: true,
};

const bridgeRef: { value: InvestmentBridgeResponse } = { value: KIMPTON };
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: { ...actual.api.engines, investmentBridge: async () => bridgeRef.value },
    },
  };
});

import InvestmentBridgeTab from '@/components/project/InvestmentBridgeTab';

beforeEach(() => {
  cleanup();
  bridgeRef.value = KIMPTON;
});

describe('layoutBridge (pure)', () => {
  it('equity invested + the five legs foot to equity returned (Kimpton)', () => {
    const l = layoutBridge(KIMPTON)!;
    expect(l.steps.map((s) => s.key)).toEqual(['start', 'acquisition', 'renovation', 'operations', 'financing', 'exit', 'end']);
    const sum = KIMPTON.equity_invested! + KIMPTON.legs.reduce((a, b) => a + (b.value ?? 0), 0);
    expect(sum).toBeCloseTo(KIMPTON.equity_returned!, 0);
    expect(l.computedTotal).toBeCloseTo(KIMPTON.equity_returned!, 0);
    expect(l.foots).toBe(true);
    // Each floating bar starts where the previous one ended.
    for (let i = 1; i < l.steps.length - 1; i += 1) expect(l.steps[i].from).toBe(l.steps[i - 1].to);
  });

  it('an unavailable leg does not move the running total and the bridge stops footing', () => {
    const b: InvestmentBridgeResponse = {
      ...KIMPTON,
      legs: KIMPTON.legs.map((l) => (l.key === 'financing' ? { ...l, value: null, status: 'unavailable', reason: 'no debt inputs', components: [] } : l)),
      unavailable: ['financing'],
    };
    const l = layoutBridge(b)!;
    const fin = l.steps.find((s) => s.key === 'financing')!;
    expect(fin.value).toBeNull();
    expect(fin.from).toBe(fin.to);
    expect(l.computedTotal).toBeNull();
    expect(l.foots).toBe(false);
  });

  it('no run → no layout', () => {
    expect(layoutBridge({ ...KIMPTON, available: false, equity_invested: null })).toBeNull();
  });
});

describe('InvestmentBridgeTab', () => {
  it('renders the waterfall in leg order and states that it foots', async () => {
    render(<InvestmentBridgeTab dealId="11111111-1111-1111-1111-111111111111" />);
    expect(await screen.findByTestId('bridge-row-acquisition')).toBeInTheDocument();
    expect(screen.getByTestId('bridge-value-start')).toHaveTextContent('$19.60M');
    expect(screen.getByTestId('bridge-value-acquisition')).toHaveTextContent('−$37.63M');
    expect(screen.getByTestId('bridge-value-exit')).toHaveTextContent('$71.87M');
    expect(screen.getByTestId('bridge-value-end')).toHaveTextContent('$62.20M');
    expect(screen.getByTestId('bridge-foot')).toHaveTextContent(/^Foots:/);
  });

  it('expands a leg to its engine fields', async () => {
    render(<InvestmentBridgeTab dealId="11111111-1111-1111-1111-111111111111" />);
    fireEvent.click(await screen.findByTestId('bridge-row-acquisition'));
    const comps = screen.getByTestId('bridge-components-acquisition');
    expect(within(comps).getByText(/capital\.uses\[Purchase Price\]/)).toBeInTheDocument();
  });

  it('an unavailable leg renders "—" with its reason, never $0', async () => {
    bridgeRef.value = {
      ...KIMPTON,
      legs: KIMPTON.legs.map((l) => (l.key === 'financing' ? { ...l, value: null, status: 'unavailable', reason: 'The returns run did not record its debt inputs', components: [] } : l)),
      unavailable: ['financing'],
      computed_equity_returned: null,
      residual: null,
      reconciles: false,
    };
    render(<InvestmentBridgeTab dealId="11111111-1111-1111-1111-111111111111" />);
    const v = await screen.findByTestId('bridge-value-financing');
    expect(v).toHaveTextContent('—');
    expect(v.textContent).not.toMatch(/\$0/);
    expect(screen.getByTestId('bridge-reason-financing')).toHaveTextContent(/did not record its debt inputs/);
    expect(screen.getByTestId('bridge-foot')).toHaveTextContent(/Does not foot yet/);
  });
});
