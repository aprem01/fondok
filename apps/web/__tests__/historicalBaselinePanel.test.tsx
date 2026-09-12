/**
 * Historical Baseline panel — what an incomparable period RENDERS (FON-44 §4).
 *
 * Sam, August 2026: "Fixed Expenses +11,170%, F&B Dept Expense +4,476% …
 * driven by incomplete/partial or inconsistently classified historical
 * periods." The worker now refuses those comparisons
 * (`engines/historical_baseline.walk_yoy`, pinned in
 * `apps/worker/tests/test_historical_baseline.py`). This suite pins the half
 * Sam actually looks at: the panel must show the refusal, not a number — and
 * never a zero standing in for a percentage that does not exist.
 *
 * The panel does not compute year-over-year at all; every historical
 * percentage is the engine's own walk entry for that (line, year). So a
 * comparison the engine withholds cannot reappear in the table.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import HistoricalBaselinePanel from '@/components/project/HistoricalBaselinePanel';
import type { HistoricalBaselineResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

const DEAL = '11111111-1111-1111-1111-111111111111';

function year(fiscal_year: number, overrides: Record<string, unknown> = {}) {
  return {
    fiscal_year,
    period_basis: 'FY',
    is_partial: false,
    occupancy: 0.74,
    adr: 280,
    revpar: 207.2,
    rooms_revenue: 12_000_000,
    fnb_revenue: 1_800_000,
    other_revenue: 600_000,
    total_revenue: 14_400_000,
    rooms_dept_expense: 3_600_000,
    fnb_dept_expense: 1_500_000,
    other_dept_expense: 250_000,
    undistributed: 2_500_000,
    gop: 6_550_000,
    fixed_expenses: 930_000,
    noi: 5_620_000,
    source_document_ids: [`doc-${fiscal_year}`],
    ...overrides,
  };
}

/** Sam's deal shape: 2019 and 2023, with 2020-2022 missing. The worker
 *  refuses every 2023 comparison rather than dividing 2023 by 2019. */
function gappedBaseline(): HistoricalBaselineResponse {
  return {
    deal_id: DEAL,
    years: [year(2019), year(2023, { fixed_expenses: 104_000_000 })],
    gaps: [2020, 2021, 2022],
    look_back_years: 5,
    coverage_pct: 0.4,
    walk: [
      { line: 'fixed_expenses', year: 2019, value: 930_000,
        yoy_abs: null, yoy_pct: null, reason: null },
      { line: 'fixed_expenses', year: 2023, value: 104_000_000,
        yoy_abs: null, yoy_pct: null, reason: 'period_mismatch' },
    ],
  } as unknown as HistoricalBaselineResponse;
}

describe('an incomparable pair renders its refusal, not a percentage', () => {
  it('shows no growth figure for a year whose prior year is missing', () => {
    const { container } = render(
      <HistoricalBaselinePanel baseline={gappedBaseline()} dealId={DEAL} />,
    );

    // The +11,170% Sam saw came from dividing 2023 by 2019. Nothing in the
    // table carries a percentage now — not the swing, and not a 0.0%.
    expect(container.textContent).not.toMatch(/%/);
    // The refusal is on the screen, carrying the worker's own code.
    const refused = container.querySelectorAll('[data-refused="period_mismatch"]');
    expect(refused.length).toBeGreaterThan(0);
    expect(refused[0]).toHaveAttribute('aria-label', 'Period mismatch');
    // The values themselves still render — this withholds the comparison,
    // not the data.
    expect(screen.getByText('$104.0M')).toBeInTheDocument();
  });

  it('keeps a refused swing out of the "biggest swings" chip row', () => {
    render(<HistoricalBaselinePanel baseline={gappedBaseline()} dealId={DEAL} />);
    // Chips are the broker-question candidates; a swing nobody can compute
    // is not a question worth sending to a broker.
    expect(screen.queryByText('Biggest YoY swings')).not.toBeInTheDocument();
  });

  it('states what the coverage denominator counts', () => {
    render(<HistoricalBaselinePanel baseline={gappedBaseline()} dealId={DEAL} />);
    const chip = screen.getByText('Coverage 2/5 yrs');
    expect(chip).toHaveAttribute(
      'title',
      '2 of the 5 fiscal years in the lookback window ending 2023 carry an extracted P&L',
    );
  });
});

describe('a comparable pair still renders the engine’s percentage', () => {
  it('renders the walk entry for that line and year, unchanged', () => {
    const baseline = {
      deal_id: DEAL,
      years: [year(2022), year(2023, { rooms_revenue: 11_040_000 })],
      gaps: [],
      look_back_years: 5,
      coverage_pct: 0.4,
      walk: [
        { line: 'rooms_revenue', year: 2023, value: 11_040_000,
          yoy_abs: -960_000, yoy_pct: -0.08, reason: null },
      ],
    } as unknown as HistoricalBaselineResponse;

    const { container } = render(
      <HistoricalBaselinePanel baseline={baseline} dealId={DEAL} />,
    );
    expect(screen.getAllByText('-8.0%').length).toBeGreaterThan(0);
    // And nothing was refused, so no refusal glyph is rendered.
    expect(container.querySelectorAll('[data-refused]')).toHaveLength(0);
  });
});
