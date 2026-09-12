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

// Mutable routing state (FON-59 #4) - `params` is a REAL URLSearchParams, what
// Next's ReadonlyURLSearchParams behaves like, so `useSubTab`'s toString()
// round-trip is exercised rather than stubbed.
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-1',
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
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
        avg_coc: 0.0812,
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
    sensitivity: {
      deal_id: 'deal-1',
      engine: 'sensitivity',
      status: 'complete',
      summary: '',
      outputs: {
        matrices: [
          {
            key: 'irr_exit_revpar',
            label: 'Levered IRR — Exit Cap × RevPAR Growth',
            row_variable: 'exit_cap_rate',
            col_variable: 'revpar_growth',
            metric: 'levered_irr',
            rows: [0.06, 0.065, 0.07, 0.075, 0.08],
            // The grid runs to 7.50% — the sandbox slider must reach it.
            cols: [0.025, 0.035, 0.045, 0.055, 0.075],
            cells: [
              { row_value: 0.07, col_value: 0.045, value: 0.2301, is_base: true },
            ],
          },
        ],
      },
      inputs: {},
      error: null,
      runtime_ms: 20,
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
  avg_coc: 0.0755,
  exit_value: 46_000_000,
  net_proceeds: 20_000_000,
  dscr_y1: 1.38,
  hold_years: 5,
  exit_cap_rate: 0.09,
  loan_amount: 25_480_000,
  total_debt: 25_480_000,
  total_capital: 44_000_000,
  noi_by_year: [3_000_000, 3_100_000, 3_200_000, 3_300_000, 3_400_000],
  cash_flows: [-19_000_000, 1_400_000, 1_500_000, 1_600_000, 1_700_000, 21_000_000],
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
vi.mock('@/components/project/MaxPricePanel', () => ({
  // Surfaces the prop so the ReturnsWorkspace → PricingSubTab → panel wiring is
  // asserted here; the note's own copy is covered in pricingMaxPrice.test.tsx.
  default: ({ sandboxActive }: { sandboxActive?: boolean }) =>
    React.createElement(
      'div',
      null,
      sandboxActive
        ? 'Solved on the canonical case; the active sensitivity is not applied.'
        : 'canonical',
    ),
}));
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
  window.sessionStorage.clear();
});

describe('ReturnsTab headline (split-headline regression)', () => {
  it('resolves IRR, Equity Multiple AND CoC from the same worker output', () => {
    render(<ReturnsTab />);
    // IRR + EM from the worker.
    expect(screen.getByText('23.01%')).toBeInTheDocument();
    expect(screen.getByText('2.37x')).toBeInTheDocument();
    // Canonical headline (Design 100% Returns rebuild) is the HOLD-AVERAGE
    // cash-on-cash: avg_coc (0.0812 → 8.12%). year_one_coc stays in the
    // fixture at 0.081 so a regression back to the Year-1 field (8.10%)
    // — or to a dead field name (0.00%) — is caught.
    expect(screen.getByText('8.12%')).toBeInTheDocument();
    expect(screen.queryByText('8.10%')).not.toBeInTheDocument();
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
    // Exit value comes off the worker (returns). DSCR left the Returns
    // headline in the canonical rebuild — it lives in the Debt tab's credit
    // metrics (covered by debtTab.test.tsx).
    expect(screen.getByText('$52.00M')).toBeInTheDocument();
    expect(screen.queryByText('1.45x')).not.toBeInTheDocument();
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
    // Rebuilt ReturnsTab: the Live Assumptions sandbox moved to the
    // Sensitivities sub-tab, so navigate there before the sliders exist.
    fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
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
    // Sandbox lives on the Sensitivities sub-tab (rebuilt ReturnsTab).
    fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
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


// ── FON-68 — the sandbox is visible where it changes an answer, survives
//    navigation within the deal, and is honest about what it does NOT reach ──

const SANDBOX_KEY = 'fondok:returns-sandbox:deal-1';

/** Open Sensitivities and drag Exit Cap Rate off its 0.07 base. */
function dirtyTheSandbox(value = '0.09') {
  fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
  fireEvent.change(screen.getAllByRole('slider')[0], { target: { value } });
}

describe('ReturnsTab — Returns Summary shows the sandbox case', () => {
  it('hero KPIs move to the preview and are chipped Sandbox while dirty', async () => {
    render(<ReturnsTab />);
    // Canonical first.
    expect(screen.getByText('23.01%')).toBeInTheDocument();
    expect(screen.queryByText('Sandbox')).not.toBeInTheDocument();

    dirtyTheSandbox();
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('tab', { name: 'Returns Summary' }));

    // The headline now reads the preview, not the canonical run.
    await waitFor(() => expect(screen.getByText('19.02%')).toBeInTheDocument());
    expect(screen.getByText('2.05x')).toBeInTheDocument();       // preview MOIC
    expect(screen.getByText('7.55%')).toBeInTheDocument();       // preview avg CoC
    expect(screen.getByText('$46.00M')).toBeInTheDocument();     // preview exit value
    expect(screen.queryByText('23.01%')).not.toBeInTheDocument();
    expect(screen.queryByText('2.37x')).not.toBeInTheDocument();
    // …and every moved tile says so.
    expect(screen.getAllByText('Sandbox').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/sandbox case — Investment and Debt are unchanged/i),
    ).toBeInTheDocument();
  });

  it('goes back to the canonical numbers on Reset to base case', async () => {
    render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    fireEvent.click(screen.getAllByText('Reset to base case')[0]);

    fireEvent.click(screen.getByRole('tab', { name: 'Returns Summary' }));
    await waitFor(() => expect(screen.getByText('23.01%')).toBeInTheDocument());
    expect(screen.queryByText('Sandbox')).not.toBeInTheDocument();
  });
});

describe('ReturnsTab — the sandbox persists within the deal (FON-68 §1)', () => {
  it('survives an unmount/remount of the Returns workspace', async () => {
    const { unmount } = render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() =>
      expect(window.sessionStorage.getItem(SANDBOX_KEY)).not.toBeNull(),
    );
    unmount();

    // Sam: "after navigating Returns → Investment → Returns, the sensitivity
    // had automatically reset to the base case."
    render(<ReturnsTab />);
    await waitFor(() =>
      expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
    expect((screen.getAllByRole('slider')[0] as HTMLInputElement).value).toBe('0.09');
  });

  it('Reset to base case clears the persisted sandbox', async () => {
    render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() =>
      expect(window.sessionStorage.getItem(SANDBOX_KEY)).not.toBeNull(),
    );
    fireEvent.click(screen.getAllByText('Reset to base case')[0]);
    await waitFor(() =>
      expect(window.sessionStorage.getItem(SANDBOX_KEY)).toBeNull(),
    );
  });

  it('is never written to the deal record or a URL param', async () => {
    render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    expect(runOneSpy).not.toHaveBeenCalled();
    expect(runAllSpy).not.toHaveBeenCalled();
    expect(window.location.search).toBe('');
  });
});

describe('ReturnsTab — slider ranges and Pricing honesty', () => {
  it('the RevPAR slider max equals the sensitivity matrix top axis value', () => {
    render(<ReturnsTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
    // SANDBOX_FIELDS order: exit cap, RevPAR growth, hold, LTV, rate.
    const revpar = screen.getAllByRole('slider')[1] as HTMLInputElement;
    // The matrix's top revpar_growth axis value is 0.075 — not the declared 0.06.
    expect(revpar.max).toBe('0.075');
  });

  it('Pricing states that the active sensitivity is not applied', async () => {
    render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('tab', { name: 'Pricing' }));
    expect(
      screen.getByText(
        /Solved on the canonical case; the active sensitivity is not applied/i,
      ),
    ).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Sub-tab routing convention (FON-59 #4 / FON-61 §3)
//
// Every sub-tab is now a URL slug on the shared `useSubTab` hook, so a deep
// link lands where it says, the back button works, and `setSub` keeps every
// other query param (`doc`, `focus`, `reviewField`) intact.
// ─────────────────────────────────────────────────────────────────────────

describe('ReturnsTab — `?tab=returns&sub=<slug>` routing', () => {
  const tabEl = (name: string) => screen.getByRole('tab', { name });

  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
    nav.replace.mockClear();
  });

  it('opens Sensitivities on ?sub=sensitivities', () => {
    nav.params = new URLSearchParams('tab=returns&sub=sensitivities');
    render(<ReturnsTab />);
    expect(tabEl('Sensitivities')).toHaveAttribute('aria-selected', 'true');
    expect(tabEl('Returns Summary')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getAllByRole('slider').length).toBe(5);
  });

  it('opens Pricing on ?sub=pricing', () => {
    nav.params = new URLSearchParams('tab=returns&sub=pricing');
    render(<ReturnsTab />);
    expect(tabEl('Pricing')).toHaveAttribute('aria-selected', 'true');
  });

  it('falls back to Returns Summary on an unknown sub value', () => {
    nav.params = new URLSearchParams('tab=returns&sub=not-a-sub-tab');
    render(<ReturnsTab />);
    expect(tabEl('Returns Summary')).toHaveAttribute('aria-selected', 'true');
  });

  it('follows a param change while already mounted', () => {
    nav.params = new URLSearchParams('tab=returns&sub=sensitivities');
    const { rerender } = render(<ReturnsTab />);
    expect(tabEl('Sensitivities')).toHaveAttribute('aria-selected', 'true');

    nav.params = new URLSearchParams('tab=returns&sub=pricing');
    rerender(<ReturnsTab />);
    expect(tabEl('Pricing')).toHaveAttribute('aria-selected', 'true');
  });

  it('setSub writes sub= and preserves doc / focus / reviewField', () => {
    nav.params = new URLSearchParams('tab=returns&doc=doc-9&focus=irr&reviewField=noi_usd');
    render(<ReturnsTab />);
    fireEvent.click(tabEl('Sensitivities'));

    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const written = new URLSearchParams(url.split('?')[1]);
    expect(written.get('sub')).toBe('sensitivities');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('irr');
    expect(written.get('reviewField')).toBe('noi_usd');
  });

  // Wave 2b shipped the session-persisted sandbox; routing must not disturb it.
  it('the sandbox survives a sub-tab change within the deal', async () => {
    render(<ReturnsTab />);
    dirtyTheSandbox();
    await waitFor(() => expect(previewSpy).toHaveBeenCalled());
    expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument();

    // A sub-tab change is a router.replace, not a remount — the override rides
    // along and the slider is still off its base when we come back.
    fireEvent.click(tabEl('Pricing'));
    expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument();
    fireEvent.click(tabEl('Returns Summary'));
    expect(screen.getByText(/Sensitivity override active/i)).toBeInTheDocument();
    fireEvent.click(tabEl('Sensitivities'));
    expect((screen.getAllByRole('slider')[0] as HTMLInputElement).value).toBe('0.09');
  });
});
