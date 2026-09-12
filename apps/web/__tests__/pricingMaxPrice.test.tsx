/**
 * Returns → Pricing (FON-68) — the Max Price Solver block + the max-price grid.
 *
 * Contracts locked here:
 *
 *  1. TARGETS COME FROM THE DEAL — both panels call the worker with an EMPTY
 *     body (no panel-local hurdle, no 0.15 / 1.8 anywhere) and render the
 *     endpoint's numbers: current price, max price, headroom, $/key, the
 *     IRR-solved and MOIC-solved prices, the binding constraint and the
 *     exit cap / LTV / rate / hold context row.
 *
 *  2. NO SILENT HURDLE — with no target on the deal the block shows the
 *     worker's "No return target set — …" copy with a "→ Investment Profile"
 *     link, never calls the endpoint and renders no numbers. A worker 422
 *     with that copy renders the same way.
 *
 *  3. GRID — every cell is the max purchase price clearing both hurdles with
 *     the binding constraint tagged; the base cell is outlined; a cell no
 *     price clears renders "—".
 *
 *  4. WIRING — ReturnsTab's Pricing sub-tab mounts the solver block first,
 *     then the grid, feeding both the deal from useDeal.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type {
  EngineOutputsResponse,
  PricingMaxPriceGridResponse,
  PricingMaxPriceResponse,
  WorkerDeal,
} from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const NO_TARGET =
  'No return target set — set Target Levered IRR / Target MOIC on the Investment Profile or pass them explicitly';

const MAX_PRICE: PricingMaxPriceResponse = {
  deal_id: 'deal-1',
  target_irr: 0.12,
  target_em: 1.5,
  target_source: 'deal',
  max_price_for_irr: 44_500_000,
  max_price_for_em: 41_200_000,
  max_price: 41_200_000,
  binding_constraint: 'em',
  irr_status: 'converged',
  em_status: 'converged',
  final_price_per_key: 206_000,
  base_purchase_price: 40_000_000,
  rooms: 200,
  exit_cap_rate: 0.075,
  ltv: 0.6,
  interest_rate: 0.06,
  hold_years: 5,
  iters: 30,
};

const GRID: PricingMaxPriceGridResponse = {
  deal_id: 'deal-1',
  target_irr: 0.12,
  target_em: 1.5,
  target_source: 'deal',
  base_exit_cap_pct: 0.075,
  base_noi_growth_pct: 0.03,
  base_purchase_price: 40_000_000,
  rooms: 200,
  cap_axis: [0.07, 0.075],
  noi_growth_axis: [0.02, 0.03],
  cells: [
    { exit_cap_pct: 0.07, noi_growth_pct: 0.02, max_price_for_irr: 43_000_000, max_price_for_em: 45_000_000, max_price: 43_000_000, binding_constraint: 'irr', irr_status: 'converged', em_status: 'converged', price_per_key: 215_000, is_base: false },
    { exit_cap_pct: 0.07, noi_growth_pct: 0.03, max_price_for_irr: 46_100_000, max_price_for_em: 47_000_000, max_price: 46_100_000, binding_constraint: 'irr', irr_status: 'converged', em_status: 'converged', price_per_key: 230_500, is_base: false },
    { exit_cap_pct: 0.075, noi_growth_pct: 0.02, max_price_for_irr: null, max_price_for_em: 39_000_000, max_price: null, binding_constraint: 'irr', irr_status: 'unreachable', em_status: 'converged', price_per_key: null, is_base: false },
    { exit_cap_pct: 0.075, noi_growth_pct: 0.03, max_price_for_irr: 44_500_000, max_price_for_em: 41_200_000, max_price: 41_200_000, binding_constraint: 'em', irr_status: 'converged', em_status: 'converged', price_per_key: 206_000, is_base: true },
  ],
};

const DEAL_WITH_TARGETS = {
  id: 'deal-1', tenant_id: 't', name: 'Anglers', city: 'Miami', keys: 200, service: null, brand: null,
  status: 'Active', deal_stage: null, risk: null, ai_confidence: null, target_irr: 0.12, target_moic: 1.5,
  created_at: '', updated_at: '',
} as unknown as WorkerDeal;
const DEAL_NO_TARGETS = { ...DEAL_WITH_TARGETS, target_irr: null, target_moic: null } as WorkerDeal;

type PricingCall = [dealId: string, body: unknown, signal?: AbortSignal];
const maxPriceSpy = vi.fn(async (..._a: PricingCall): Promise<PricingMaxPriceResponse> => MAX_PRICE);
const gridSpy = vi.fn(async (..._a: PricingCall): Promise<PricingMaxPriceGridResponse> => GRID);

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      analysis: {
        ...actual.api.analysis,
        pricing: {
          ...actual.api.analysis.pricing,
          maxPrice: (...a: PricingCall) => maxPriceSpy(...a),
          maxPriceGrid: (...a: PricingCall) => gridSpy(...a),
        },
      },
    },
  };
});

import { WorkerError } from '@/lib/api';
import MaxPricePanel from '@/components/project/MaxPricePanel';
import PricingSensitivityPanel from '@/components/project/PricingSensitivityPanel';

beforeEach(() => {
  cleanup();
  maxPriceSpy.mockClear();
  gridSpy.mockClear();
  maxPriceSpy.mockImplementation(async () => MAX_PRICE);
  gridSpy.mockImplementation(async () => GRID);
});

/** Value cell of a constraint row (row = label's grandparent). */
function rowValue(label: string): string {
  const el = screen.getByText(label);
  return (el.parentElement!.parentElement!.lastElementChild as HTMLElement).textContent ?? '';
}

describe('Max Price Solver block — renders from the endpoint with the deal targets', () => {
  it('calls /pricing/max-price with an EMPTY body and renders tiles, constraint rows and context', async () => {
    render(<MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    await waitFor(() => expect(maxPriceSpy).toHaveBeenCalledTimes(1));
    // No panel-local hurdle — the worker reads the Investment Profile.
    expect(maxPriceSpy.mock.calls[0][1]).toEqual({});

    expect(await screen.findByText('$41.20M', { selector: 'div' })).toBeInTheDocument(); // max price tile
    expect(screen.getByText('Max Price Solver')).toBeInTheDocument();
    expect(screen.getByText('Solved against the hurdles on the Investment Profile')).toBeInTheDocument();
    expect(screen.getByText('Current purchase price')).toBeInTheDocument();
    expect(screen.getByText('$40.00M')).toBeInTheDocument();
    expect(screen.getByText('$200,000 / key')).toBeInTheDocument();
    expect(screen.getByText('Headroom')).toBeInTheDocument();
    expect(screen.getByText('+$1.20M')).toBeInTheDocument();
    expect(screen.getByText('Max price / key')).toBeInTheDocument();
    expect(screen.getByText('$206,000')).toBeInTheDocument();

    // Constraint rows — targets are LINKED from the profile, prices calculated.
    expect(rowValue('Target levered IRR')).toBe('12.0%');
    expect(rowValue('Target MOIC')).toBe('1.50x');
    expect(rowValue('Max price @ 12.0% IRR')).toBe('$44.50M');
    expect(rowValue('Max price @ 1.50x MOIC')).toBe('$41.20M');
    expect(rowValue('Binding constraint')).toBe('MOIC');
    expect(rowValue('Hold period')).toBe('5 years');
    expect(rowValue('Exit cap rate')).toBe('7.50%');
    expect(rowValue('LTV / interest rate')).toBe('60.0% · 6.00%');

    // Lower-of note + the link back to the source of truth.
    expect(screen.getByText(/the lower price governs: \$44\.50M at the IRR hurdle, \$41\.20M at the MOIC hurdle — the MOIC hurdle binds\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '→ Investment Profile' })).toBeInTheDocument();
  });

  it('shows "Gap" when the current basis exceeds the max price', async () => {
    maxPriceSpy.mockImplementation(async () => ({ ...MAX_PRICE, max_price: 38_000_000, max_price_for_em: 38_000_000 }));
    render(<MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    expect(await screen.findByText('Gap')).toBeInTheDocument();
    expect(screen.getByText('−$2.00M')).toBeInTheDocument();
    expect(screen.getByText('Current basis exceeds the max price')).toBeInTheDocument();
  });

  it('renders "—" and "No price clears the hurdles" when the binding hurdle is unreachable', async () => {
    maxPriceSpy.mockImplementation(async () => ({
      ...MAX_PRICE, max_price: null, max_price_for_irr: null, irr_status: 'unreachable', binding_constraint: 'irr',
    }));
    render(<MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    expect(await screen.findByText('No price clears the hurdles')).toBeInTheDocument();
    expect(rowValue('Max price @ 12.0% IRR')).toBe('— unreachable');
    expect(rowValue('Binding constraint')).toBe('IRR');
    expect(screen.queryByText('$206,000')).not.toBeInTheDocument();
  });
});

describe('Max Price Solver block — no silent hurdle', () => {
  it('with no target on the deal: shows the worker copy + the profile link, never calls the endpoint, no numbers', () => {
    const go = vi.fn();
    const { container } = render(<MaxPricePanel dealId="deal-1" deal={DEAL_NO_TARGETS} onGoToProfile={go} />);
    expect(maxPriceSpy).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent(NO_TARGET);
    fireEvent.click(screen.getByRole('button', { name: '→ Investment Profile' }));
    expect(go).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toMatch(/\$|%/);
    expect(screen.queryByText('Binding constraint')).not.toBeInTheDocument();
  });

  it('renders a worker 422 "No return target set" the same way — message, link, no numbers', async () => {
    maxPriceSpy.mockImplementation(async () => {
      throw new WorkerError('POST /analysis/deal-1/pricing/max-price → 422', 422, JSON.stringify({ detail: NO_TARGET }));
    });
    const { container } = render(<MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    expect(await screen.findByRole('status')).toHaveTextContent(NO_TARGET);
    expect(screen.getByRole('button', { name: '→ Investment Profile' })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\$/);
  });
});

describe('Pricing sensitivity grid — max purchase price per cell', () => {
  it('renders every cell from /pricing/max-price-grid with the binding tag, outlines the base cell, dashes an unsolvable one', async () => {
    render(<PricingSensitivityPanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    await waitFor(() => expect(gridSpy).toHaveBeenCalledTimes(1));
    expect(gridSpy.mock.calls[0][1]).toEqual({});

    // FON-68 §4 — the axis is `revpar_growth` (max_price_grid.py sets
    // ``assumptions.revpar_growth`` per cell), so it carries that name here too.
    const table = await screen.findByRole('table', { name: /Max purchase price by exit cap rate and RevPAR growth/ });
    expect(screen.getByText('Pricing Sensitivity — Max Purchase Price')).toBeInTheDocument();
    expect(screen.getByText('EXIT CAP \\ REVPAR GROWTH')).toBeInTheDocument();
    // Axis headers from the endpoint (no hard-coded axes).
    expect(within(table).getByText('2.0%')).toBeInTheDocument();
    expect(within(table).getByText('3.0%')).toBeInTheDocument();
    expect(within(table).getByText('7.00%')).toBeInTheDocument();
    expect(within(table).getByText('7.50%')).toBeInTheDocument();

    const cells = within(table).getAllByRole('cell');
    expect(cells).toHaveLength(4);
    expect(cells[0]).toHaveTextContent('$43.00M');
    expect(cells[0]).toHaveTextContent('$215,000 / key · IRR');
    expect(cells[0].getAttribute('data-binding')).toBe('irr');
    // Unsolvable cell → "—", no per-key, no binding tag.
    expect(cells[2]).toHaveTextContent('—');
    expect(cells[2]).not.toHaveTextContent('$');
    expect(cells[2].getAttribute('data-binding')).toBeNull();
    // Base cell is outlined and equals the headline solve.
    expect(cells[3].getAttribute('data-base')).toBe('true');
    expect(cells[3]).toHaveTextContent('$41.20M');
    expect(cells[3]).toHaveTextContent('MOIC');
    // Footnote names both hurdles from the response.
    expect(screen.getByText(/meets both the 12\.0% IRR and 1\.50x MOIC hurdles/)).toBeInTheDocument();
  });

  it('with no target on the deal: message + link, no call, no numbers', () => {
    const { container } = render(<PricingSensitivityPanel dealId="deal-1" deal={DEAL_NO_TARGETS} onGoToProfile={vi.fn()} />);
    expect(gridSpy).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent(NO_TARGET);
    expect(screen.getByRole('button', { name: '→ Investment Profile' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\$/);
  });
});

// ─── ReturnsTab wiring ────────────────────────────────────────────────────

const OUTPUTS = {
  deal_id: 'deal-1',
  engines: {
    returns: {
      deal_id: 'deal-1', engine: 'returns', status: 'complete', summary: '',
      outputs: { levered_irr: 0.2301, equity_multiple: 2.37, year_one_coc: 0.081, avg_coc: 0.0812, gross_sale_price: 52_000_000, hold_years: 5, exit_cap_rate: 0.07 },
      inputs: { assumptions: { exit_cap_rate: 0.07, revpar_growth: 0.045, hold_years: 5, ltv: 0.65, interest_rate: 0.068 } },
      error: null, runtime_ms: 12, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: DEAL_WITH_TARGETS, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn() }),
}));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ReturnsTab from '@/components/project/ReturnsTab';

describe('ReturnsTab → Pricing sub-tab wiring', () => {
  it('mounts the Max Price Solver block, then the grid, both driven by the deal targets', async () => {
    render(<ReturnsTab />);
    // Nothing pricing-related is fetched until the analyst opens Pricing.
    expect(maxPriceSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'Pricing' }));
    await waitFor(() => expect(maxPriceSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(gridSpy).toHaveBeenCalledTimes(1));
    expect(maxPriceSpy.mock.calls[0][0]).toBe('deal-1');
    expect(maxPriceSpy.mock.calls[0][1]).toEqual({});

    const solver = await screen.findByText('Max Price Solver');
    const grid = screen.getByText('Pricing Sensitivity — Max Purchase Price');
    expect(solver.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(await screen.findByText('$40.00M')).toBeInTheDocument();
  });
});


// ── FON-68 §1 — Pricing is solved on the canonical case, and says so ─────
// The Max Price Solver calls the endpoint with an EMPTY override body, so an
// active Live-Assumptions sandbox is not applied. Silence is the one
// unacceptable option: both blocks carry the note while a sandbox is on.

describe('Pricing — an active sandbox is not applied, and the panel says so', () => {
  it('Max Price Solver states it is solved on the canonical case', async () => {
    render(
      <MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} sandboxActive onGoToProfile={vi.fn()} />,
    );
    expect(
      await screen.findByText(
        /Solved on the canonical case; the active sensitivity is not applied/i,
      ),
    ).toBeInTheDocument();
    // …and the solver still calls the endpoint with no overrides.
    await waitFor(() => expect(maxPriceSpy).toHaveBeenCalled());
    expect(maxPriceSpy.mock.calls[0][1]).toEqual({});
  });

  it('says nothing when no sandbox is active', async () => {
    render(<MaxPricePanel dealId="deal-1" deal={DEAL_WITH_TARGETS} onGoToProfile={vi.fn()} />);
    await waitFor(() => expect(maxPriceSpy).toHaveBeenCalled());
    expect(
      screen.queryByText(/the active sensitivity is not applied/i),
    ).not.toBeInTheDocument();
  });

  it('the max-price grid carries the same note', async () => {
    render(
      <PricingSensitivityPanel
        dealId="deal-1"
        deal={DEAL_WITH_TARGETS}
        sandboxActive
        onGoToProfile={vi.fn()}
      />,
    );
    expect(
      await screen.findByText(
        /Solved on the canonical case; the active sensitivity is not applied/i,
      ),
    ).toBeInTheDocument();
  });
});
