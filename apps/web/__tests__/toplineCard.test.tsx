/**
 * FON-116 / FON-117 — the topline components (plan §4.2, §5.2 "toplineStatus"):
 * banner states, the suggestion's prefilled note, the note-gated editors that
 * the worker now requires (review I-6), the calendar_unknown close-date entry
 * (R2-I1), legacy keys and the no-revenue-basis entry (review I-5).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import {
  GrowthByYearTable,
  PenetrationByYearGrid,
  RevenueBasisEntry,
  ToplineBasisLine,
  ToplineCard,
  ToplineGrowthBanner,
  ToplineNotices,
} from '@/components/project/pl/Topline';
import { toplineStatus, unavailableGrowth, SUGGESTION_45 } from './fixtures/toplineStatus';
import engineRun from './fixtures/engine/toplineRun.json';
import type { ToplineStatus } from '@/lib/api';
import type { ToplineYearRecord } from '@/lib/topline';

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

const ok = () => vi.fn(async () => true);

describe('ToplineBasisLine', () => {
  it('prints the worker’s clauses with their tone', () => {
    render(<ToplineBasisLine status={toplineStatus({ benchmark_fallback_reason: 'Competitive set: no STR report' })} />);
    expect(screen.getByTestId('topline-basis-line')).toHaveTextContent('Benchmark: STR comp set TTM May 2025');
    expect(screen.getByTestId('topline-clause-benchmark')).toHaveAttribute('data-tone', 'warn');
    expect(screen.getByTestId('topline-clause-ramp')).toHaveTextContent('Ramp 85/95/100 (default)');
  });
  it('renders nothing for an older worker', () => {
    const { container } = render(<ToplineBasisLine status={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ToplineGrowthBanner', () => {
  const props = { closeDateIso: null, closeDateEditable: true, onSaveCloseDate: vi.fn(), running: false };

  it('is absent when growth is sourced', () => {
    render(<ToplineGrowthBanner status={toplineStatus()} onSave={ok()} {...props} />);
    expect(screen.queryByTestId('topline-growth-unavailable')).toBeNull();
  });

  it('unavailable: the labelled suggestion applies revpar_growth with the PREFILLED, editable note', async () => {
    const onSave = ok();
    render(<ToplineGrowthBanner status={toplineStatus(unavailableGrowth())} onSave={onSave} {...props} />);
    const banner = screen.getByTestId('topline-growth-unavailable');
    expect(banner).toHaveAttribute('data-state', 'unavailable');
    expect(banner).toHaveTextContent('No market forecast — set growth manually');
    expect(screen.queryByTestId('topline-close-date-entry')).toBeNull();
    fireEvent.click(screen.getByTestId('topline-suggestion-apply'));
    const note = screen.getByLabelText('Platform suggestion — override justification') as HTMLInputElement;
    expect(note.value).toBe(SUGGESTION_45.note_prefill);
    fireEvent.click(screen.getByTestId('topline-suggestion-save'));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ revpar_growth: 0.045 }, SUGGESTION_45.note_prefill));
  });

  it('M-1: a stale CoStar extraction headlines the banner as re-extract with the Data Room action; the notice is not repeated', () => {
    const lines = ['CoStar report on file (Miami.pdf) but extracted before class forecasts were read — re-extract it.'];
    const st = toplineStatus({ ...unavailableGrowth(), reextract_needed: lines });
    render(
      <>
        <ToplineGrowthBanner status={st} onSave={ok()} {...props} dealId="d1" />
        <ToplineNotices status={st} dealId="d1" migrationNotice={null} onClearLegacy={vi.fn()} onRerun={vi.fn()} running={false} />
      </>,
    );
    const banner = screen.getByTestId('topline-growth-unavailable');
    expect(banner).toHaveAttribute('data-state', 'reextract');
    expect(banner).not.toHaveTextContent('No market forecast');
    expect(within(banner).getByTestId('topline-banner-reextract-action')).toHaveAttribute('href', '/projects/d1');
    expect(screen.queryByTestId('topline-reextract')).toBeNull();
  });

  it('never shows a 4.5% the worker did not send', () => {
    render(<ToplineGrowthBanner status={toplineStatus({ ...unavailableGrowth(), suggestions: [] })} onSave={ok()} {...props} />);
    expect(screen.getByTestId('topline-growth-unavailable')).not.toHaveTextContent('4.5');
  });

  it('calendar_unknown: the close-date entry comes first, above the suggestion (R2-I1)', () => {
    render(
      <ToplineGrowthBanner
        status={toplineStatus({ ...unavailableGrowth(), calendar_known: false, calendar_unknown: true })}
        onSave={ok()}
        {...props}
      />,
    );
    const banner = screen.getByTestId('topline-growth-unavailable');
    expect(banner).toHaveAttribute('data-state', 'calendar_unknown');
    expect(banner).toHaveTextContent('Set the acquisition close date to place the market forecast');
    const entry = screen.getByTestId('topline-close-date-entry');
    const suggestion = screen.getByTestId('topline-suggestion-apply');
    // DOM order: the close date precedes the suggestion.
    expect(entry.compareDocumentPosition(suggestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('calendar_unknown: the close date saves through the shared field with its note', async () => {
    const onSaveCloseDate = vi.fn();
    render(
      <ToplineGrowthBanner
        status={toplineStatus({ ...unavailableGrowth(), calendar_known: false, calendar_unknown: true })}
        onSave={ok()}
        {...props}
        onSaveCloseDate={onSaveCloseDate}
      />,
    );
    fireEvent.click(screen.getByTestId('topline-close-date'));
    fireEvent.change(screen.getByLabelText('Acquisition close date'), { target: { value: '2025-12-31' } });
    fireEvent.change(screen.getByLabelText('Override justification'), { target: { value: 'PSA close' } });
    fireEvent.click(screen.getByLabelText('Save'));
    await waitFor(() => expect(onSaveCloseDate).toHaveBeenCalledWith('2025-12-31', 'PSA close'));
  });
});

describe('ToplineNotices', () => {
  const base = { dealId: 'd1', migrationNotice: null, onClearLegacy: vi.fn(), onRerun: vi.fn(), running: false };

  it('re-extract: the worker’s line and the Data Room action', () => {
    render(<ToplineNotices status={toplineStatus({ reextract_needed: ['CoStar report on file (Miami.pdf) but extracted before class forecasts were read — re-extract it.'] })} {...base} />);
    const n = screen.getByTestId('topline-reextract');
    expect(n).toHaveTextContent('CoStar report extracted before class forecasts — re-extract');
    expect(n).toHaveTextContent('Miami.pdf');
    expect(within(n).getByTestId('topline-reextract-action')).toHaveAttribute('href', '/projects/d1');
  });

  it('names the calendar anchor; an assumed one asks for the close date', () => {
    render(
      <ToplineNotices
        status={toplineStatus({ calendar_anchor: { source: 'underwriting_as_of', date: '2025-06-30', assumed: true, year1: { year: 1, start: '2025-07-01', end: '2026-06-30', label: 'Jul 2025–Jun 2026', days: 365 }, label: 'x' } })}
        {...base}
      />,
    );
    const a = screen.getByTestId('topline-calendar-anchor');
    expect(a).toHaveAttribute('data-source', 'underwriting_as_of');
    expect(a).toHaveTextContent('set the acquisition close date');
  });

  it('legacy keys: each translation is listed and "Clear legacy keys" clears the clearable ones', () => {
    const onClearLegacy = vi.fn();
    render(
      <ToplineNotices
        status={toplineStatus({
          legacy_flags: [
            { key: 'revenue_seed_from_str_forecast', value: true, treatment: 'ignored', reason: 'legacy_key_translated', detail: 'The retired "Use STR rates" switch is ignored.' },
            { key: 'starting_adr', value: 300, treatment: 'kept_no_benchmark', reason: 'legacy_key_translated', detail: 'Kept: no benchmark.' },
          ],
        })}
        {...base}
        onClearLegacy={onClearLegacy}
      />,
    );
    expect(screen.getByTestId('topline-legacy-flags')).toHaveTextContent('The retired "Use STR rates" switch is ignored.');
    fireEvent.click(screen.getByTestId('topline-clear-legacy'));
    expect(onClearLegacy).toHaveBeenCalledWith(['revenue_seed_from_str_forecast']);
  });

  it('a run that predates topline v2 gets the one-time notice with re-run and compare', () => {
    const onRerun = vi.fn();
    render(<ToplineNotices status={null} {...base} onRerun={onRerun} migrationNotice="The topline method changed; this run predates it — re-run, then compare runs." />);
    const n = screen.getByTestId('topline-migration-notice');
    expect(n).toHaveTextContent('this run predates it');
    fireEvent.click(within(n).getByRole('button', { name: 'Re-run the model' }));
    expect(onRerun).toHaveBeenCalled();
    expect(within(n).getByRole('link')).toHaveAttribute('href', '/projects/d1?tab=activity');
  });
});

describe('ToplineCard — note-gated inputs the worker requires (review I-6)', () => {
  it('the benchmark level saves topline_benchmark_level only with a note', async () => {
    const onSave = ok();
    render(<ToplineCard status={toplineStatus()} overrides={{}} onSave={onSave} running={false} />);
    expect(screen.getByTestId('topline-level-in-use')).toHaveTextContent('STR comp set TTM May 2025');
    fireEvent.change(screen.getByLabelText('Benchmark level'), { target: { value: 'submarket' } });
    fireEvent.click(screen.getByTestId('topline-level-save'));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId('topline-level-error')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Benchmark level — override justification'), { target: { value: 'Comp set too thin' } });
    fireEvent.click(screen.getByTestId('topline-level-save'));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ topline_benchmark_level: 'submarket' }, 'Comp set too thin'));
  });

  it('penetration shows the default with the current TTM index beside it, and saves the target with a note', async () => {
    const onSave = ok();
    render(<ToplineCard status={toplineStatus()} overrides={{}} onSave={onSave} running={false} />);
    expect(screen.getByTestId('topline-penetration-occ-source')).toHaveTextContent('Default');
    expect(screen.getByTestId('topline-current-occ')).toHaveTextContent('Current MPI 109.8 (STR TTM May 2025)');
    fireEvent.change(screen.getByLabelText('Occupancy penetration (MPI)'), { target: { value: '105' } });
    fireEvent.change(screen.getByLabelText('Occupancy penetration (MPI) — override justification'), { target: { value: 'Runs above the set' } });
    fireEvent.click(screen.getByTestId('topline-penetration-occ-save'));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ index_mpi_target: 105 }, 'Runs above the set'));
  });

  it('the ramp shows the APPLIED ramp and saves penetration_ramp_by_year as fractions', async () => {
    const onSave = ok();
    render(<ToplineCard status={toplineStatus()} overrides={{}} onSave={onSave} running={false} />);
    expect(screen.getByTestId('topline-ramp-summary')).toHaveTextContent('Ramp 85/95/100 (default)');
    expect((screen.getByLabelText('Ramp Year 1 (%)') as HTMLInputElement).value).toBe('85');
    fireEvent.change(screen.getByLabelText('Ramp Year 1 (%)'), { target: { value: '90' } });
    fireEvent.change(screen.getByLabelText('Ramp — override justification'), { target: { value: 'Soft opening' } });
    fireEvent.click(screen.getByTestId('topline-ramp-save'));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ penetration_ramp_by_year: [0.9, 0.95] }, 'Soft opening'));
  });

  it('names the mode and shows why a new hotel counts from the close', () => {
    render(
      <ToplineCard
        status={toplineStatus({ ramp_mode: 'new_or_reopened', ramp_flag: 'No opening date yet, so the ramp counts from the close. Enter the opening date.' })}
        overrides={{}}
        onSave={ok()}
        running={false}
      />,
    );
    expect(screen.getByTestId('topline-ramp-mode')).toHaveTextContent('New or reopened hotel');
    expect(screen.getByTestId('topline-ramp-flag')).toHaveTextContent('No opening date yet');
  });

  it('a class-average target says it holds today’s index', () => {
    render(
      <ToplineCard
        status={toplineStatus({ penetration_adr: 57.7, penetration_adr_source: 'hold_index' })}
        overrides={{}}
        onSave={ok()}
        running={false}
      />,
    );
    expect(screen.getByTestId('topline-penetration-adr-source')).toHaveTextContent('Holds today’s index');
  });
});

describe('GrowthByYearTable', () => {
  it('shows each year’s resolved growth with its source; edits write both lists with one note', async () => {
    const onSave = ok();
    render(<GrowthByYearTable status={toplineStatus()} overrides={{}} onSave={onSave} running={false} />);
    expect(screen.getByTestId('topline-growth-row-2')).toHaveTextContent('+1.8%');
    expect(screen.getByTestId('topline-growth-row-2')).toHaveTextContent('CoStar');
    fireEvent.click(screen.getByTestId('topline-growth-edit'));
    fireEvent.change(screen.getByLabelText('ADR growth Year 3 (%)'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('Growth by year — override justification'), { target: { value: 'Broker view' } });
    fireEvent.click(screen.getByTestId('topline-growth-save'));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ occupancy_growth_by_year: null, adr_growth_by_year: [null, null, 0.03] }, 'Broker view'),
    );
  });
  it('refuses a growth outside ±50%', () => {
    render(<GrowthByYearTable status={toplineStatus()} overrides={{}} onSave={ok()} running={false} />);
    fireEvent.click(screen.getByTestId('topline-growth-edit'));
    fireEvent.change(screen.getByLabelText('ADR growth Year 1 (%)'), { target: { value: '80' } });
    expect(screen.getByTestId('topline-growth-error')).toHaveTextContent('outside');
    expect(screen.queryByTestId('topline-growth-save')).toBeNull();
  });
});

describe('GrowthByYearTable — FON-121 deal years and decision 3 page chips', () => {
  const oct = (c: number, year: number) => ({
    year, start: `${c}-10-01`, end: `${c + 1}-09-30`, label: `Oct ${c}–Sep ${c + 1}`,
    calendar_months: [{ calendar_year: c, months: 3 }, { calendar_year: c + 1, months: 9 }], days: 365,
  });
  const g = (value: number, source: string, label: string, chip: string | null) => ({
    value, source, label, chip, page: 26, doc_name: 'Miami Beach.pdf',
  });
  const status = () =>
    toplineStatus({
      growth_by_year: [
        { year: 1, deal_year: oct(2025, 1), applied: false, occupancy: g(0, 'costar_submarket_forecast', 'x', 'CoStar p.26'), adr: g(0.04, 'costar_submarket_forecast', 'x', 'CoStar p.26') },
        {
          year: 2, deal_year: oct(2026, 2), applied: true,
          occupancy: g(-0.003, 'costar_submarket_forecast', 'Forecast · Oct 2026–Sep 2027 = 3 mo × -1.2% + 9 mo × +0.0%', 'CoStar p.26'),
          adr: g(0.0, 'costar_submarket_forecast', 'Forecast · Oct 2026–Sep 2027 = 3 mo × +1.8% + 9 mo × -0.6%', 'CoStar p.26'),
        },
        {
          year: 5, deal_year: oct(2029, 5), applied: true,
          occupancy: g(0, 'held_at_stabilization', 'Assumption · occupancy held at its Year 3 (Oct 2027–Sep 2028) stabilized level', null),
          adr: g(0.02575, 'carried_forward', 'Assumption · Oct 2029–Sep 2030 = 3 mo × +2.8% + 9 mo × +2.5% CoStar forecast average (as published, p.4)', 'CoStar p.26, p.4'),
        },
      ],
      terminal_revpar_growth: {
        value: 0.025, source_id: 'growth_carried_forward', basis: 'exit_year_adr_growth', chip: 'CoStar p.4',
        average_basis: 'published_average',
        label: 'Year 6 (Oct 2030–Sep 2031, the exit-NOI year) ADR growth +2.5%',
      },
    });

  it('each year names its deal-year span and the page is visible, not only on hover', () => {
    render(<GrowthByYearTable status={status()} overrides={{}} onSave={ok()} running={false} />);
    const row2 = screen.getByTestId('topline-growth-row-2');
    expect(row2).toHaveTextContent('Year 2 · Oct 2026–Sep 2027');
    expect(within(row2).getAllByTestId('topline-growth-chip').map((c) => c.textContent)).toEqual(['CoStar p.26', 'CoStar p.26']);
    // The span's calendar mix is on the year cell's hover.
    expect(within(row2).getByText('Year 2 · Oct 2026–Sep 2027').closest('td')).toHaveAttribute('title', '3 mo of 2026 + 9 mo of 2027');
    const row5 = screen.getByTestId('topline-growth-row-5');
    expect(row5).toHaveTextContent('CoStar p.26, p.4');
    expect(screen.getByTestId('topline-terminal-growth')).toHaveTextContent('Exit-year growth: +2.5% · CoStar p.4');
    // Review M1 — a blended figure shows the precision it applies.
    expect(row5).toHaveTextContent('+2.575%');
    expect(screen.getByTestId('topline-terminal-growth')).toHaveTextContent('Oct 2030–Sep 2031');
  });
});

describe('PenetrationByYearGrid — the engine chain, from a real run (review I-1)', () => {
  // apps/worker run_all_engines on the Kimpton documents fixture (defaults,
  // Year-2 ARI 98, Year-1 displacement 10% / 6.67%) — the record shape the
  // engine actually emits: mpi_target already includes the ramp; occupancy
  // / adr are the figures that ran.
  const run = engineRun as unknown as { revenue: { topline_by_year: ToplineYearRecord[] }; topline_status: ToplineStatus };

  it('benchmark × stabilized penetration × ramp = before displacement, then displacement, then the figure that ran', () => {
    render(
      <PenetrationByYearGrid status={run.topline_status} toplineByYear={run.revenue.topline_by_year} overrides={{}} onSave={ok()} running={false} />,
    );
    const r1 = screen.getByTestId('topline-penetration-row-1');
    expect(within(r1).getByTestId('topline-grid-mpi-1')).toHaveTextContent(/^100$/); // stabilized — never 85 × 85%
    expect(within(r1).getByTestId('topline-grid-ramp-1')).toHaveTextContent('85.0%');
    expect(within(r1).getByTestId('topline-grid-pre-1')).toHaveTextContent('63.4% · $269');
    expect(within(r1).getByTestId('topline-grid-disp-1')).toHaveTextContent('−10.0% occ · −6.7% ADR');
    expect(within(r1).getByTestId('topline-grid-final-occ-1')).toHaveTextContent('57.1%');
    expect(within(r1).getByTestId('topline-grid-final-adr-1')).toHaveTextContent('$251');
    expect(within(r1).getByTestId('basis-chip-forecast')).toBeInTheDocument();
  });

  it('a per-year penetration is shown as yours, with the ramp noted for the other metric only', () => {
    render(
      <PenetrationByYearGrid status={run.topline_status} toplineByYear={run.revenue.topline_by_year} overrides={{}} onSave={ok()} running={false} />,
    );
    expect(screen.getByTestId('topline-grid-ari-2')).toHaveTextContent('98 (yours, no ramp)');
    expect(screen.getByTestId('topline-grid-ramp-2')).toHaveTextContent('95.0% (occupancy only)');
    expect(screen.getByTestId('topline-grid-disp-2')).toHaveTextContent('—');
    expect(screen.getByTestId('topline-grid-ramp-3')).toHaveTextContent('—');
  });

  it('the footer states the order honestly', () => {
    render(
      <PenetrationByYearGrid status={run.topline_status} toplineByYear={run.revenue.topline_by_year} overrides={{}} onSave={ok()} running={false} />,
    );
    const foot = screen.getByTestId('topline-penetration-grid-footer');
    expect(foot).toHaveTextContent('before displacement');
    expect(foot).toHaveTextContent('A penetration you set for a single year runs with no ramp');
    expect(foot).not.toHaveTextContent('are before any renovation displacement');
  });

  it('per-year edits write the lists with the note', async () => {
    const onSave = ok();
    render(
      <PenetrationByYearGrid status={run.topline_status} toplineByYear={run.revenue.topline_by_year} overrides={{ index_ari_target_by_year: { value: [null, 98], note: 'x' } }} onSave={onSave} running={false} />,
    );
    fireEvent.click(screen.getByTestId('topline-penetration-grid-edit'));
    expect((screen.getByLabelText('ARI Year 2') as HTMLInputElement).value).toBe('98');
    fireEvent.change(screen.getByLabelText('ARI Year 3'), { target: { value: '99' } });
    fireEvent.change(screen.getByLabelText('Penetration by year — override justification'), { target: { value: 'Sam path' } });
    fireEvent.click(screen.getByTestId('topline-penetration-grid-save'));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        { index_mpi_target_by_year: null, index_ari_target_by_year: [null, 98, 99, null, null] },
        'Sam path',
      ),
    );
  });
});

describe('RevenueBasisEntry — no Year-1 basis (review I-5)', () => {
  const message = 'The deal has no Year-1 occupancy or Year-1 ADR basis — Upload a T-12 or STR report, or enter Year-1 occupancy and ADR on the Future P&L.';
  it('prints the worker’s message verbatim and saves both rates with one note', async () => {
    const onSave = ok();
    render(<RevenueBasisEntry message={message} missing={['starting_adr', 'starting_occupancy']} onSave={onSave} running={false} editable />);
    expect(screen.getByTestId('revenue-basis-message')).toHaveTextContent(message);
    fireEvent.change(screen.getByLabelText('Year-1 occupancy (%)'), { target: { value: '72.5' } });
    fireEvent.change(screen.getByLabelText('Year-1 ADR ($)'), { target: { value: '310' } });
    fireEvent.change(screen.getByLabelText('Year-1 occupancy and ADR — override justification'), { target: { value: 'Broker pro forma' } });
    fireEvent.click(screen.getByTestId('revenue-basis-save'));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ starting_occupancy: 0.725, starting_adr: 310 }, 'Broker pro forma'),
    );
  });
  it('asks only for the missing rate', () => {
    render(<RevenueBasisEntry message={message} missing={['starting_adr']} onSave={ok()} running={false} editable />);
    expect(screen.queryByLabelText('Year-1 occupancy (%)')).toBeNull();
    expect(screen.getByLabelText('Year-1 ADR ($)')).toBeInTheDocument();
  });
});

describe('FON-116 staged rollout — growth-only topline', () => {
  it('the Topline card shows the stabilized year only — no level, penetration or ramp controls', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    render(
      <ToplineCard
        status={growthOnlyStatus()}
        overrides={{}}
        onSave={ok()}
        running={false}
        stabilizationSlot={<div data-testid="stab-slot">Stabilization year</div>}
      />,
    );
    const card = screen.getByTestId('topline-card');
    expect(card).toHaveAttribute('data-mode', 'growth_only');
    expect(screen.getByTestId('stab-slot')).toBeInTheDocument();
    expect(screen.getByTestId('topline-growth-only-note')).toHaveTextContent('occupancy is flat after the stabilized year');
    expect(screen.queryByTestId('topline-level')).toBeNull();
    expect(screen.queryByTestId('topline-penetration-occ')).toBeNull();
    expect(screen.queryByTestId('topline-penetration-adr')).toBeNull();
    expect(screen.queryByTestId('topline-ramp')).toBeNull();
  });

  it('the benchmark topline keeps every control', () => {
    render(<ToplineCard status={toplineStatus()} overrides={{}} onSave={ok()} running={false} />);
    expect(screen.getByTestId('topline-level')).toBeInTheDocument();
    expect(screen.getByTestId('topline-penetration-occ')).toBeInTheDocument();
    expect(screen.getByTestId('topline-ramp')).toBeInTheDocument();
    expect(screen.getByTestId('topline-card')).not.toHaveAttribute('data-mode');
  });

  it('the growth table and the basis line render in growth-only mode', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    render(<ToplineBasisLine status={growthOnlyStatus()} />);
    expect(screen.getByTestId('topline-clause-year1')).toHaveTextContent('Year 1: T-12 actual');
    expect(screen.queryByTestId('topline-clause-ramp')).toBeNull();
    expect(screen.queryByTestId('topline-clause-benchmark')).toBeNull();
  });
});
