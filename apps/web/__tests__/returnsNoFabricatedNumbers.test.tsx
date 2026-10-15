/**
 * Returns section — standing rule "no inventing or fake data".
 *
 * Every figure the Returns section shows must trace to the worker engine
 * outputs. When a run is missing a field the UI must say so — "—" plus the
 * missing engine field named — and never print a 0, a default, or a number
 * from a fixture. These tests mutate one engine-shaped fixture per case and
 * assert the placeholder state, so a regression back to `?? 0` or to a
 * hard-coded slider default is caught by name.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => '/projects/deal-1',
}));

type EngineRow = {
  deal_id: string;
  engine: string;
  status: string;
  summary: string;
  outputs: Record<string, unknown>;
  inputs: Record<string, unknown>;
  error: null;
  runtime_ms: number;
  started_at: null;
  completed_at: null;
  run_id: string;
};
const row = (engine: string, outputs: Record<string, unknown>, inputs: Record<string, unknown> = {}): EngineRow => ({
  deal_id: 'deal-1', engine, status: 'complete', summary: '', outputs, inputs,
  error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'run-1',
});

const FULL_ASSUMPTIONS = { exit_cap_rate: 0.07, revpar_growth: 0.045, hold_years: 5, ltv: 0.65, interest_rate: 0.068 };

/** A complete, engine-shaped canonical run. Each test derives from this. */
function fullRun(): EngineOutputsResponse {
  return {
    deal_id: 'deal-1',
    engines: {
      returns: row('returns', {
        levered_irr: 0.2301,
        equity_multiple: 2.37,
        year_one_coc: 0.081,
        avg_coc: 0.0812,
        gross_sale_price: 52_000_000,
        net_proceeds: 21_000_000,
        selling_costs: 1_040_000,
        hold_years: 5,
        exit_cap_rate: 0.07,
        noi_by_year: [3_000_000, 3_100_000, 3_200_000, 3_300_000, 3_400_000],
        cash_flows: [-19_000_000, 1_400_000, 1_500_000, 1_600_000, 1_700_000, 22_700_000],
      }, { assumptions: { ...FULL_ASSUMPTIONS } }),
      capital: row('capital', { total_capital: 44_000_000 }),
      debt: row('debt', { year_one_dscr: 1.45 }),
      sensitivity: row('sensitivity', {
        matrices: [
          {
            key: 'irr_exit_revpar',
            row_variable: 'exit_cap_rate',
            col_variable: 'revpar_growth',
            metric: 'levered_irr',
            rows: [0.065, 0.07],
            cols: [0.035, 0.045],
            // Only three of the four cells came back from the engine.
            cells: [
              { row_value: 0.065, col_value: 0.035, value: 0.25, is_base: false },
              { row_value: 0.065, col_value: 0.045, value: 0.26, is_base: false },
              { row_value: 0.07, col_value: 0.045, value: 0.2301, is_base: true },
            ],
          },
        ],
      }),
    },
  } as unknown as EngineOutputsResponse;
}

// Mutable fixture the mocked hook hands back — each test sets it.
const outputsRef = vi.hoisted(() => ({ value: null as unknown }));
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: outputsRef.value, previous: null, loading: false, settled: true, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const previewSpy = vi.fn();
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, engines: { ...actual.api.engines, returnsPreview: (...a: unknown[]) => previewSpy(...a) } },
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: null, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn() }),
}));
vi.mock('@/components/project/PricingSensitivityPanel', () => ({ default: () => null }));
vi.mock('@/components/project/MaxPricePanel', () => ({ default: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));

import ReturnsTab from '@/components/project/ReturnsTab';

/** Drop keys from the returns engine's outputs (or inputs.assumptions). */
function withReturns(
  patchOutputs: Record<string, unknown | undefined>,
  patchAssumptions?: Record<string, unknown | undefined>,
): EngineOutputsResponse {
  const run = fullRun() as unknown as { engines: Record<string, EngineRow> };
  const outputs = { ...run.engines.returns.outputs };
  for (const [k, v] of Object.entries(patchOutputs)) {
    if (v === undefined) delete outputs[k];
    else outputs[k] = v;
  }
  run.engines.returns.outputs = outputs;
  if (patchAssumptions) {
    const a = { ...(run.engines.returns.inputs.assumptions as Record<string, unknown>) };
    for (const [k, v] of Object.entries(patchAssumptions)) {
      if (v === undefined) delete a[k];
      else a[k] = v;
    }
    run.engines.returns.inputs = { assumptions: a };
  }
  return run as unknown as EngineOutputsResponse;
}

const openSensitivities = () => fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));

beforeEach(() => {
  cleanup();
  previewSpy.mockClear();
  window.sessionStorage.clear();
  nav.params = new URLSearchParams('');
  outputsRef.value = fullRun();
});

describe('Returns Summary — a complete run shows only engine figures', () => {
  it('renders the engine values, no "—" and no zero placeholders', () => {
    render(<ReturnsTab />);
    expect(screen.getByText('23.01%')).toBeInTheDocument();   // returns.levered_irr
    expect(screen.getByText('2.37x')).toBeInTheDocument();    // returns.equity_multiple
    expect(screen.getByText('8.12%')).toBeInTheDocument();    // returns.avg_coc
    expect(screen.getByText('7.73%')).toBeInTheDocument();    // YoC = 3.4M ÷ 44M
    expect(screen.getByText('$52.00M')).toBeInTheDocument();  // returns.gross_sale_price
    expect(screen.getByText('$19.00M')).toBeInTheDocument();  // initial equity = −cash_flows[0]
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
    expect(screen.queryByText('0.00x')).not.toBeInTheDocument();
    expect(screen.queryByText(/Awaiting model run/)).not.toBeInTheDocument();
  });
});

describe('Returns Summary — a run missing a headline field names it instead of printing 0', () => {
  it('avg_coc absent → "—" + "returns.avg_coc", never 0.00%', () => {
    outputsRef.value = withReturns({ avg_coc: undefined });
    render(<ReturnsTab />);
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
    expect(screen.getByText('Awaiting model run — returns.avg_coc is not on this run')).toBeInTheDocument();
    // The other tiles are untouched.
    expect(screen.getByText('23.01%')).toBeInTheDocument();
    expect(screen.getByText('2.37x')).toBeInTheDocument();
  });

  it('equity_multiple absent → "—" + "returns.equity_multiple", never 0.00x', () => {
    outputsRef.value = withReturns({ equity_multiple: undefined });
    render(<ReturnsTab />);
    expect(screen.queryByText('0.00x')).not.toBeInTheDocument();
    expect(screen.getByText('Awaiting model run — returns.equity_multiple is not on this run')).toBeInTheDocument();
  });

  it('noi_by_year absent → Yield on Cost "—" and names returns.noi_by_year', () => {
    outputsRef.value = withReturns({ noi_by_year: undefined });
    render(<ReturnsTab />);
    expect(screen.queryByText('7.73%')).not.toBeInTheDocument();
    expect(screen.getByText('Awaiting model run — returns.noi_by_year is not on this run')).toBeInTheDocument();
  });

  it('cash_flows absent → Equity Profit and Initial Equity are "—" and name returns.cash_flows', () => {
    outputsRef.value = withReturns({ cash_flows: undefined });
    render(<ReturnsTab />);
    expect(screen.queryByText('$19.00M')).not.toBeInTheDocument();
    expect(screen.getAllByText('Awaiting model run — returns.cash_flows is not on this run').length).toBe(2);
    // The placeholder glyph carries the reason as its tooltip.
    const dashes = document.querySelectorAll('[data-unavailable="true"]');
    expect(dashes.length).toBeGreaterThanOrEqual(2);
    for (const d of Array.from(dashes)) expect(d.getAttribute('title')).toMatch(/is not on this run$/);
  });
});

describe('Return Bridge — net_proceeds absent leaves the exit leg "—", not $0.00M', () => {
  it('complete run: the bridge reconciles operating + exit to the total', () => {
    render(<ReturnsTab />);
    // cash_flows sum(1..) = 28.9M; net_proceeds 21.0M → operating 7.9M
    expect(screen.getByText(/\$7\.90M operating \+ \$21\.00M exit = \$28\.90M returned on \$19\.00M invested/)).toBeInTheDocument();
    expect(screen.queryByText(/returns\.net_proceeds is not on this run/)).not.toBeInTheDocument();
  });

  it('net_proceeds absent: Operating / Debt service / Net exit rows read "—" with the reason; total still shown', () => {
    outputsRef.value = withReturns({ net_proceeds: undefined });
    render(<ReturnsTab />);
    const awaiting = document.querySelectorAll('[data-awaiting="true"]');
    const labels = Array.from(awaiting).map((el) => el.textContent ?? '');
    expect(labels.some((t) => t.startsWith('Operating cash flow'))).toBe(true);
    expect(labels.some((t) => t.startsWith('Debt service'))).toBe(true);
    expect(labels.some((t) => t.startsWith('Net exit proceeds'))).toBe(true);
    for (const el of Array.from(awaiting)) {
      if (/Operating cash flow|Debt service|Net exit proceeds/.test(el.textContent ?? '')) {
        expect(el.getAttribute('title')).toBe('Awaiting model run — returns.net_proceeds is not on this run');
        expect(el.textContent).toMatch(/—$/);
      }
    }
    // Never a fabricated $0 exit folded into operating cash flow.
    expect(screen.queryByText('$0.00M')).not.toBeInTheDocument();
    expect(screen.getByText(/\$28\.90M returned on \$19\.00M invested/)).toBeInTheDocument();
    expect(screen.getByText(/operating \/ exit split is awaiting a model run — returns\.net_proceeds is not on this run/)).toBeInTheDocument();
  });

  it('no refinance modelled: the row is listed as "—" and says why', () => {
    render(<ReturnsTab />);
    const refi = Array.from(document.querySelectorAll('[data-awaiting="true"]')).find((el) =>
      (el.textContent ?? '').startsWith('Refinance proceeds'),
    );
    expect(refi).toBeDefined();
    expect(refi!.getAttribute('title')).toBe('No refinance modelled on this deal (debt.refi_year is not set)');
  });
});

describe('Live Assumptions sandbox — no client-side slider defaults', () => {
  it('complete assumptions: five sliders, each on the engine-recorded base', () => {
    render(<ReturnsTab />);
    openSensitivities();
    const sliders = screen.getAllByRole('slider') as HTMLInputElement[];
    expect(sliders.map((s) => s.value)).toEqual(['0.07', '0.045', '5', '0.65', '0.068']);
    expect(screen.queryByTestId('returns-sandbox-unavailable')).toBeNull();
  });

  it('revpar_growth not recorded: no sliders, no default (0.03 / 3.00%), the missing key is named', () => {
    outputsRef.value = withReturns({}, { revpar_growth: undefined });
    render(<ReturnsTab />);
    openSensitivities();
    expect(screen.queryAllByRole('slider').length).toBe(0);
    const notice = screen.getByTestId('returns-sandbox-unavailable');
    expect(notice).toHaveTextContent(/Awaiting model run/);
    expect(notice).toHaveTextContent('returns.inputs.assumptions.revpar_growth');
    expect(notice.getAttribute('title')).toBe('Not recorded on this run: returns.inputs.assumptions.revpar_growth');
    // The old hard-coded RevPAR default must not surface anywhere.
    expect(screen.queryByText('3.00%')).not.toBeInTheDocument();
    // No override banner, no preview call, Reset disabled — there is no sandbox.
    expect(screen.queryByText(/Sensitivity override active/i)).not.toBeInTheDocument();
    expect(previewSpy).not.toHaveBeenCalled();
    expect((screen.getByText('Reset to base case') as HTMLButtonElement).disabled).toBe(true);
    // The canonical result line still reads the engine.
    expect(screen.getByText(/Base case:/)).toBeInTheDocument();
    expect(screen.getByText('IRR 23.01%')).toBeInTheDocument();
  });

  it('no assumptions blob at all: every missing key is listed', () => {
    const run = fullRun() as unknown as { engines: Record<string, EngineRow> };
    run.engines.returns.inputs = {};
    outputsRef.value = run;
    render(<ReturnsTab />);
    openSensitivities();
    const notice = screen.getByTestId('returns-sandbox-unavailable');
    for (const k of ['exit_cap_rate', 'revpar_growth', 'hold_years', 'ltv', 'interest_rate']) {
      expect(notice).toHaveTextContent(`returns.inputs.assumptions.${k}`);
    }
    // A sandbox persisted by an older run is discarded, not replayed.
    window.sessionStorage.setItem('fondok:returns-sandbox:deal-1', JSON.stringify({ ...FULL_ASSUMPTIONS }));
    cleanup();
    render(<ReturnsTab />);
    expect(screen.queryByText(/Sensitivity override active/i)).not.toBeInTheDocument();
  });

  it('a headline figure missing from the run reads "—" on the sandbox result line too', () => {
    outputsRef.value = withReturns({ levered_irr: 0.2301, gross_sale_price: undefined });
    render(<ReturnsTab />);
    openSensitivities();
    expect(screen.getByText('Exit —')).toBeInTheDocument();
    expect(screen.queryByText(/IRR 0\.00%/)).not.toBeInTheDocument();
  });
});

describe('Sensitivity grid — a cell the engine did not return is "—", not 0.0%', () => {
  it('three returned cells render; the fourth reads "—" and says why', () => {
    render(<ReturnsTab />);
    openSensitivities();
    expect(screen.getByText('25.0%')).toBeInTheDocument();
    expect(screen.getByText('26.0%')).toBeInTheDocument();
    expect(screen.getByText('23.0%')).toBeInTheDocument();
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument();
    const missing = Array.from(document.querySelectorAll('[title]')).filter((el) =>
      /no cell returned by the sensitivity engine/.test(el.getAttribute('title') ?? ''),
    );
    expect(missing.length).toBe(1);
    expect(missing[0].textContent).toBe('—');
    expect(missing[0].getAttribute('title')).toMatch(/^7\.0% · 3\.5%/);
  });
});

describe('Returns section — no engine run at all', () => {
  it('shows the Returns-engine-unavailable notice, never a number', () => {
    outputsRef.value = null;
    render(<ReturnsTab />);
    expect(screen.getByTestId('overview-returns-unavailable')).toHaveTextContent(/Returns engine unavailable/);
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument();
  });
});
