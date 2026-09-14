/**
 * FON-41 — the Projections control bar has no THIRD STATE.
 *
 * Sam, MVP QA 2026-09-14: *"Projection controls (FON-41): Base Year / Period
 * still appear non-editable. We should confirm whether this is intentional for
 * MVP. If analysts are expected to control the projection period/base year,
 * this needs to be resolved; otherwise we should remove/disable the appearance
 * of editability."*
 *
 * This product has been bitten twice by the same defect class — the Historicals
 * Period dropdown and the Annual/Monthly granularity toggle both looked live
 * and did nothing, and the founder hit the first one himself. The rule that came
 * out of it: **a control that silently does nothing is worse than one that is
 * plainly unavailable.** So every control on this bar must be in exactly one of
 * two states, and this file asserts there is no third:
 *
 *   1. GENUINELY EDITABLE — activating it changes something, and a Save PATCHes
 *      the exact `field_overrides` key with the FON-74 justification.
 *   2. NO EDITABLE AFFORDANCE AT ALL — derived values carry no button, no
 *      input, no pointer cursor; they name the field that actually drives them.
 *
 * The three controls and their verdicts:
 *
 *   • Base year        → DERIVED from `acquisition_close_date` (there is no
 *                        `base_year` assumption in the worker). Affordance
 *                        removed; the Investment tab is named as owner.
 *   • Projection period → REAL: `field_overrides.hold_years`, which the worker's
 *                        override loop lands on `base['hold_years']`. Now
 *                        genuinely editable, note-gated.
 *   • Annual / Monthly  → REMOVED. The state it set was read by nothing, and the
 *                        revenue/expense engines have no monthly series to show.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { NOTE_REQUIRED_MESSAGE } from '@/lib/overrideNote';

const HOLD_YEARS = 5;
const CALENDAR = [2025, 2026, 2027, 2028, 2029];
const TOTAL_REVENUE = 13_600_000;

const revYear = (i: number) => ({
  year: i + 1,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const fbYear = (i: number) => ({
  year: i + 1,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  resort_fees: 0,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const expYear = (i: number) => ({
  year: i + 1,
  total_revenue: TOTAL_REVENUE,
  dept_expenses: { rooms: 3_000_000, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: {
    administrative_general: 900_000,
    information_telecom: 180_000,
    sales_marketing: 800_000,
    property_operations: 760_000,
    utilities: 560_000,
    total: 3_200_000,
  },
  mgmt_fee: 408_000,
  ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: 5_400_000,
  noi: 1_448_443,
  noi_institutional: 2_001_056,
});

function buildOutputs(calendar: number[] | undefined): EngineOutputsResponse {
  const stub = {
    deal_id: 'deal-uuid-1', status: 'complete', summary: '',
    inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
  };
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      revenue: {
        ...stub, engine: 'revenue',
        outputs: {
          years: Array.from({ length: HOLD_YEARS }, (_, i) => revYear(i)),
          projection_start_year: calendar ? calendar[0] : null,
          projection_calendar_years: calendar ?? [],
        },
      },
      fb: { ...stub, engine: 'fb', outputs: { years: Array.from({ length: HOLD_YEARS }, (_, i) => fbYear(i)) } },
      expense: {
        ...stub, engine: 'expense',
        outputs: {
          years: Array.from({ length: HOLD_YEARS }, (_, i) => expYear(i)),
          // The published stabilized block — the Assumptions panel's one other
          // editable non-AssumptionField control reads it.
          stabilization: { stabilized_year: 3, stabilized_year_index: 2, source: 'fondok_signal' },
        },
      },
      returns: {
        ...stub, engine: 'returns',
        outputs: {
          hold_years: HOLD_YEARS, terminal_noi: 2_759_000, exit_cap_rate: 0.07, revpar_growth: 0.045,
        },
      },
      debt: { ...stub, engine: 'debt', outputs: { year_one_dscr: 1.59 } },
      capital: { ...stub, engine: 'capital', outputs: { purchase_price: 34_000_000, uses: [], sources: [] } },
    },
  } as unknown as EngineOutputsResponse;
}

// ── Mocks ────────────────────────────────────────────────────────────
const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  update: vi.fn(async () => ({ id: 'deal-uuid-1' })),
  run: vi.fn(async () => {}),
}));

let CALENDAR_YEARS: number[] | undefined = CALENDAR;
let FIELD_OVERRIDES: Record<string, unknown> = {};

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: buildOutputs(CALENDAR_YEARS),
      previous: null, loading: false, settled: true, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132, field_overrides: FIELD_OVERRIDES },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: fx.run, running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: fx.toast }) }));
vi.mock('@/lib/exportXlsx', () => ({ downloadXlsx: vi.fn(async () => {}) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, deals: { ...actual.api.deals, update: fx.update } },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  fx.toast.mockClear();
  fx.update.mockClear();
  fx.run.mockClear();
  CALENDAR_YEARS = CALENDAR;
  FIELD_OVERRIDES = { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } };
});

/** The control bar above the Assumptions panel. */
const bar = () => screen.getByTestId('projections-controls');

/** Everything in a subtree that CLAIMS to be operable. */
function interactiveIn(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>('button, input, select, textarea, [role="button"]'),
  );
}

// ── 1. Base year — derived, and the affordance is gone ───────────────
describe('FON-41 · Base year is DERIVED — no editable affordance survives', () => {
  it('renders the calendar year with no input, no button and no pointer cursor', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const value = screen.getByTestId('projection-base-year');

    expect(value.textContent).toContain('2025');
    // Not an editor, and nothing inside it is.
    expect(value.tagName).toBe('SPAN');
    expect(interactiveIn(value)).toEqual([]);
    expect(value.style.cursor).not.toBe('pointer');
    // The old chip was a white 1px-bordered 6px-radius box — this file's own
    // text-input treatment. It must not come back.
    expect(value.style.border).toBe('');
    expect(value.style.background).toBe('');
  });

  it('names the field that actually drives it, and deep-links to its owner', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const title = screen.getByTestId('projection-base-year').getAttribute('title') ?? '';
    expect(title).toMatch(/acquisition close date/i);
    expect(title).toMatch(/Investment tab/i);
    // …and the link goes there, so "edit it where it lives" is one click.
    const owner = screen.getByTestId('projection-base-year-owner');
    expect(owner.getAttribute('href')).toContain('tab=investment');
  });

  it('with no close date shows an em dash and says where the date is set — never a guess', () => {
    CALENDAR_YEARS = undefined;
    FIELD_OVERRIDES = {};
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const value = screen.getByTestId('projection-base-year');
    expect(value.textContent?.trim()).toBe('—');
    expect(value.getAttribute('title') ?? '').toMatch(/Acquisition Date on the Investment tab/i);
    // No wall-clock fallback.
    expect(value.textContent).not.toContain(String(new Date().getFullYear()));
  });
});

// ── 2. Projection period — genuinely editable, note-gated ────────────
describe('FON-41 · Projection period IS editable — it writes field_overrides.hold_years', () => {
  it('shows the deal hold and opens an editor with a justification field', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const value = screen.getByTestId('projection-period-value');
    expect(value.textContent).toBe('5 years');

    fireEvent.click(value);
    expect(screen.getByTestId('projection-period-input')).toBeInTheDocument();
    // hold_years moves every engine number, so FON-74 demands a reason.
    expect(screen.getByTestId('projection-period-note')).toBeInTheDocument();
  });

  it('Save with no justification is REFUSED — nothing is PATCHed', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('projection-period-value'));
    fireEvent.change(screen.getByTestId('projection-period-input'), { target: { value: '7' } });
    fireEvent.click(screen.getByTestId('projection-period-save'));

    expect(fx.update).not.toHaveBeenCalled();
    expect(fx.run).not.toHaveBeenCalled();
    expect(fx.toast).toHaveBeenCalledWith(NOTE_REQUIRED_MESSAGE, { type: 'error' });
  });

  it('Save WITH a justification PATCHes hold_years as {value, note} and re-runs the model', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('projection-period-value'));
    fireEvent.change(screen.getByTestId('projection-period-input'), { target: { value: '7' } });
    fireEvent.change(screen.getByTestId('projection-period-note'), {
      target: { value: 'IC approved a 7-year hold at the 9/12 committee.' },
    });
    fireEvent.click(screen.getByTestId('projection-period-save'));

    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [dealId, body] = fx.update.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(dealId).toBe('deal-uuid-1');
    // The EXACT worker key the override loop lands on base['hold_years'].
    expect(body.field_overrides.hold_years).toEqual({
      value: 7,
      note: 'IC approved a 7-year hold at the 9/12 committee.',
    });
    // Every other override survives the write.
    expect(body.field_overrides.acquisition_close_date).toEqual({ value: '2025-09-30', note: 'PSA' });
    await waitFor(() => expect(fx.run).toHaveBeenCalled());
  });

  it('re-saving the SAME hold writes nothing — an unchanged value is not an override', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('projection-period-value'));
    fireEvent.change(screen.getByTestId('projection-period-input'), { target: { value: '5' } });
    fireEvent.click(screen.getByTestId('projection-period-save'));

    expect(fx.update).not.toHaveBeenCalled();
    expect(fx.run).not.toHaveBeenCalled();
  });

  it('Cancel and Escape both discard without a request', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('projection-period-value'));
    fireEvent.change(screen.getByTestId('projection-period-input'), { target: { value: '9' } });
    fireEvent.click(screen.getByTestId('projection-period-cancel'));
    expect(fx.update).not.toHaveBeenCalled();
    expect(screen.getByTestId('projection-period-value').textContent).toBe('5 years');

    fireEvent.click(screen.getByTestId('projection-period-value'));
    fireEvent.keyDown(screen.getByTestId('projection-period-input'), { key: 'Escape' });
    expect(fx.update).not.toHaveBeenCalled();
    expect(screen.getByTestId('projection-period-value').textContent).toBe('5 years');
  });

  it('shows the analyst override the moment it is saved, before the re-run lands', () => {
    FIELD_OVERRIDES = {
      acquisition_close_date: { value: '2025-09-30', note: 'PSA' },
      hold_years: { value: 7, note: 'IC approved a 7-year hold.' },
    };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    // The override wins over the (still stale) returns.hold_years of 5.
    expect(screen.getByTestId('projection-period-value').textContent).toBe('7 years');
  });
});

// ── 3. The dead Annual / Monthly toggle is gone ──────────────────────
describe('FON-41 · the Annual / Monthly toggle is removed, not re-dressed', () => {
  it('offers no granularity pill — the statement states its basis instead', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(within(bar()).queryByText('Monthly')).toBeNull();
    expect(within(bar()).queryByRole('button', { name: /monthly/i })).toBeNull();

    const basis = screen.getByTestId('projection-basis-note');
    expect(basis.textContent).toMatch(/annual/i);
    // It explains WHY there is no monthly view, rather than offering a dead one.
    expect(basis.getAttribute('title') ?? '').toMatch(/annual periods/i);
    expect(interactiveIn(basis)).toEqual([]);
  });

  it('the surviving stepper is labelled as a view trim and actually trims', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const shown = screen.getByTestId('projection-columns-shown');
    expect(shown.textContent).toBe('5 of 5 years');

    const columnCount = () =>
      (document.querySelector('table') as HTMLTableElement).tHead!.rows[0].cells.length;
    const before = columnCount();
    fireEvent.click(screen.getByRole('button', { name: 'Show one fewer year' }));
    await waitFor(() => expect(screen.getByTestId('projection-columns-shown').textContent).toBe('4 of 5 years'));
    expect(columnCount()).toBeLessThan(before);
  });
});

// ── 4. the sweep: no third state anywhere on the bar ─────────────────
describe('FON-41 · every control on the bar is live or is not a control', () => {
  it('nothing outside a real control claims to be clickable', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const controls = new Set(interactiveIn(bar()));
    const fakes = Array.from(bar().querySelectorAll<HTMLElement>('*')).filter(
      (el) => el.style.cursor === 'pointer' && !controls.has(el) && el.tagName !== 'A',
    );
    expect(fakes.map((el) => el.outerHTML.slice(0, 120))).toEqual([]);
  });

  it('EVERY enabled control on the bar changes something when activated', async () => {
    // Re-render per control: activating one mutates the tree the next would be
    // read from. The assertion is deliberately blunt — a control that leaves the
    // DOM byte-identical is the exact defect Sam reported.
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const total = interactiveIn(bar()).length;
    expect(total).toBeGreaterThan(0);
    cleanup();

    for (let i = 0; i < total; i++) {
      render(<ProjectionsSection dealId="deal-uuid-1" />);
      const el = interactiveIn(bar())[i];
      const label = el.getAttribute('aria-label') ?? el.getAttribute('data-testid') ?? el.textContent ?? '?';
      if ((el as HTMLButtonElement).disabled) {
        // "Plainly unavailable" is the OTHER allowed state — but only for a
        // control that is genuinely at a bound. The ONLY one on a fresh render
        // is "show one more year" with every modelled year already on screen,
        // and it comes back the moment the view is trimmed.
        expect(label).toBe('Show one more year');
        fireEvent.click(screen.getByRole('button', { name: 'Show one fewer year' }));
        await waitFor(() =>
          expect((screen.getByRole('button', { name: 'Show one more year' }) as HTMLButtonElement).disabled).toBe(false),
        );
        cleanup();
        continue;
      }
      const before = document.body.innerHTML;
      fireEvent.click(el);
      await waitFor(() =>
        expect(document.body.innerHTML, `"${label}" is inert — it changed nothing`).not.toBe(before),
      );
      cleanup();
    }
  });
});

// ── 5. the Assumptions panel holds the same line ─────────────────────
describe('FON-41 · Assumptions panel — editable fields are wired, the linked one is inert by design', () => {
  it('every assumption input opens the justification gate on a real change', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const inputs = Array.from(
      document.querySelectorAll<HTMLInputElement>('[data-testid^="assumption-panel-input-"]'),
    );
    expect(inputs.length).toBeGreaterThan(0);

    for (const input of inputs) {
      const key = input.getAttribute('data-testid')!.replace('assumption-panel-input-', '');
      const moved = String(Number(input.value) + 1);
      fireEvent.change(input, { target: { value: moved } });
      fireEvent.blur(input);
      // A live field parks the change and asks why. A dead one would do nothing.
      expect(
        screen.queryByTestId(`assumption-panel-note-${key}`),
        `${key} did not react to a real change`,
      ).toBeInTheDocument();
    }
    // Parking a change never writes anything on its own.
    expect(fx.update).not.toHaveBeenCalled();
  });

  it('the Stabilization Year is a real editor', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const value = screen.getByTestId('stabilization-year-value');
    expect((value as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(value);
    expect(screen.getByLabelText('Stabilization Year')).toBeInTheDocument();
  });

  it('Exit cap rate carries NO editable affordance and names Investment as its owner', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const row = screen.getByText('Exit cap rate').parentElement as HTMLElement;
    expect(interactiveIn(row)).toEqual([]);
    expect(within(row).getByText('Investment →').getAttribute('href')).toContain('tab=investment');
  });
});
