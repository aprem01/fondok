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
      // THE stabilized year (expense.stabilization) — the Yield on Cost tile
      // reads this block's before-reserve NOI, the same figure the Overview's
      // Stabilized NOI row prints; never `returns.noi_by_year[-1]`.
      expense: row('expense', {
        stabilization: {
          stabilized_year_index: 2,
          stabilized_year: 3,
          source: 'default_year_3',
          anchor: 'acquisition_close',
          stabilized_noi_before_reserve: 3_400_000,
          stabilized_cash_noi: 3_200_000,
        },
      }),
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

  it('stabilization block absent → Yield on Cost "—" and names expense.stabilization (never the last hold year)', () => {
    // The last hold year's cash NOI (noi_by_year[-1] = 3.4M) is still on the
    // run; it must NOT stand in for the stabilized year.
    const run = fullRun() as unknown as { engines: Record<string, unknown> };
    delete run.engines.expense;
    outputsRef.value = run;
    render(<ReturnsTab />);
    expect(screen.queryByText('7.73%')).not.toBeInTheDocument();
    expect(
      screen.getByText('Awaiting model run — expense.stabilization.stabilized_noi_before_reserve is not on this run'),
    ).toBeInTheDocument();
  });

  it('Yield on Cost reads the stabilized year (Year 3), not the last hold year', () => {
    // Stabilized Year-3 NOI before reserve 3.0M vs last-year cash NOI 3.4M →
    // 3.0M ÷ 44M = 6.82%, and the caption names the year and basis.
    const run = fullRun() as unknown as { engines: Record<string, EngineRow> };
    run.engines.expense.outputs = {
      stabilization: { ...(run.engines.expense.outputs.stabilization as Record<string, unknown>), stabilized_noi_before_reserve: 3_000_000 },
    };
    outputsRef.value = run;
    render(<ReturnsTab />);
    expect(screen.getByText('6.82%')).toBeInTheDocument();
    expect(screen.queryByText('7.73%')).not.toBeInTheDocument();
    expect(screen.getByText('Stabilized NOI (Year 3, before FF&E reserve) ÷ total capital')).toBeInTheDocument();
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

// Sam #7 (2026-10-15) — "Stabilized NOI should remain separate from exit NOI."
describe('Returns — stabilized NOI and exit NOI stay separate figures with separate labels', () => {
  it('Yield on Cost reads the stabilized year; Exit Value names the exit NOI it capitalizes', () => {
    // Stabilized Year-3 NOI 3.4M (before reserve) vs exit NOI 3.64M (forward 12 months).
    outputsRef.value = withReturns({
      terminal_noi: 3_640_000,
      exit_noi_basis: 'forward_12',
      exit_noi_period_label: 'Forward 12-month NOI (Year 6)',
    });
    render(<ReturnsTab />);
    // 3.4M ÷ 44M = 7.73% — the stabilized figure, not 3.64M ÷ 44M = 8.27%.
    expect(screen.getByText('7.73%')).toBeInTheDocument();
    expect(screen.queryByText('8.27%')).not.toBeInTheDocument();
    expect(screen.getByText('Stabilized NOI (Year 3, before FF&E reserve) ÷ total capital')).toBeInTheDocument();
    expect(screen.getByText('7.00% exit cap · Exit NOI (Forward 12-month NOI (Year 6))')).toBeInTheDocument();
    // Neither caption borrows the other's name.
    expect(screen.queryByText(/Stabilized NOI.*exit cap/)).not.toBeInTheDocument();
  });

  // (c) 2026-10-17 — a run that predates `exit_noi_period_label` ran on the
  // forward-12 basis; Returns now says so in the Overview's exact spelling
  // instead of dropping the basis from the tile.
  it('a run without the period label names the forward-12 basis exactly as the Overview Exit row does', () => {
    render(<ReturnsTab />);
    expect(
      screen.getByText('7.00% exit cap · Exit NOI (forward 12-month, after FF&E reserve)'),
    ).toBeInTheDocument();
  });

  it('the stabilized exit basis is named on the Exit Value tile and the Exit Assumptions card alike', () => {
    outputsRef.value = withReturns({
      exit_noi_basis: 'stabilized',
      exit_noi_period_label: 'Stabilized NOI (Year 3, grown to Year 5)',
    });
    render(<ReturnsTab />);
    expect(
      screen.getByText('7.00% exit cap · Exit NOI (Stabilized NOI (Year 3, grown to Year 5))'),
    ).toBeInTheDocument();
    // The Exit Assumptions card (same Summary view) spells it identically.
    expect(
      screen.getByTitle('Exit NOI (Stabilized NOI (Year 3, grown to Year 5)) ÷ exit cap rate'),
    ).toBeInTheDocument();
  });
});

// FON-98 (2026-10-17) — Yield on Cost names the stabilized year AND the anchor
// it counts from, exactly as the Overview's Stabilization Year reads, with the
// Sam #7 exception note when the reopening could not anchor the default.
describe('Returns — Yield on Cost carries the stabilization anchor caption', () => {
  const withStabilization = (patch: Record<string, unknown>) => {
    const run = fullRun() as unknown as { engines: Record<string, EngineRow> };
    run.engines.expense.outputs = {
      stabilization: { ...(run.engines.expense.outputs.stabilization as Record<string, unknown>), ...patch },
    };
    outputsRef.value = run;
  };

  it('close anchor → "Year 3 after close (default)", no exception note', () => {
    withStabilization({ default_year: 3, clamped: false, reopening_date: null, anchor_fallback_reason: null });
    render(<ReturnsTab />);
    expect(screen.getByText('Stabilized NOI (Year 3, before FF&E reserve) ÷ total capital')).toBeInTheDocument();
    expect(screen.getByTestId('yoc-anchor')).toHaveTextContent('Stabilized year: Year 3 after close (default)');
    expect(screen.queryByTestId('yoc-anchor-note')).toBeNull();
  });

  it('reopening anchor → names the year, the rule and the date', () => {
    withStabilization({
      stabilized_year_index: 5, stabilized_year: 6, source: 'default_year_3_reopening',
      anchor: 'reopening', default_year: 3, reopening_date: '2027-03-15', anchor_fallback_reason: null,
    });
    render(<ReturnsTab />);
    expect(screen.getByText('Stabilized NOI (Year 6, before FF&E reserve) ÷ total capital')).toBeInTheDocument();
    expect(screen.getByTestId('yoc-anchor')).toHaveTextContent(
      'Stabilized year: Year 6 — Year 3 after reopening (Mar 2027) (default)',
    );
    expect(screen.queryByTestId('yoc-anchor-note')).toBeNull();
  });

  it('Sam #7 fallback → close caption plus the exception note', () => {
    withStabilization({ default_year: 3, reopening_date: '2019-06-01', anchor_fallback_reason: 'reopening_before_projection' });
    render(<ReturnsTab />);
    expect(screen.getByTestId('yoc-anchor')).toHaveTextContent('Stabilized year: Year 3 after close (default)');
    expect(screen.getByTestId('yoc-anchor-note')).toHaveTextContent(
      'Exception: Year 3 after the reopening date (Jun 2019) falls before the first projected year, so stabilization is measured from the acquisition close instead.',
    );
  });

  it('no stabilization block → no caption alongside the honest dash', () => {
    const run = fullRun() as unknown as { engines: Record<string, unknown> };
    delete run.engines.expense;
    outputsRef.value = run;
    render(<ReturnsTab />);
    expect(screen.queryByTestId('yoc-anchor')).toBeNull();
    expect(screen.queryByTestId('yoc-anchor-note')).toBeNull();
  });
});
