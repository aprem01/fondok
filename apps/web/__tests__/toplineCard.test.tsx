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
        status={toplineStatus({ calendar_anchor: { source: 'underwriting_as_of', date: '2025-06-30', assumed: true, first_model_year: 2025, label: 'x' } })}
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

  it('a ramp the policy does not apply is named — no default ramp is assumed', () => {
    render(
      <ToplineCard
        status={toplineStatus({ ramp_by_year: [], ramp_source: 'not_applied_by_policy', ramp_policy: 'repositioning_only' })}
        overrides={{}}
        onSave={ok()}
        running={false}
      />,
    );
    expect(screen.getByTestId('topline-ramp-summary')).toHaveTextContent('the default ramp does not apply to this deal (ramp policy: repositioning only)');
    expect((screen.getByLabelText('Ramp Year 1 (%)') as HTMLInputElement).value).toBe('');
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

describe('PenetrationByYearGrid', () => {
  it('prints benchmark (with basis chip), applied penetration, ramp and the result per year', async () => {
    const onSave = ok();
    render(
      <PenetrationByYearGrid
        status={toplineStatus()}
        toplineByYear={[
          { year: 1, mpi_target: 100, ari_target: 100, ramp_pct: 0.85, occupancy: 0.64175, adr: 258.876 },
          { year: 2, mpi_target: 100, ari_target: 100, ramp_pct: 0.95, occupancy: 0.71345, adr: 294.5 },
        ]}
        overrides={{}}
        onSave={onSave}
        running={false}
      />,
    );
    const r1 = screen.getByTestId('topline-penetration-row-1');
    expect(r1).toHaveTextContent('75.5%');
    expect(r1).toHaveTextContent('85.0%');
    expect(r1).toHaveTextContent('64.2%');
    expect(within(r1).getByTestId('basis-chip-forecast')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('topline-penetration-grid-edit'));
    fireEvent.change(screen.getByLabelText('ARI Year 2'), { target: { value: '98' } });
    fireEvent.change(screen.getByLabelText('Penetration by year — override justification'), { target: { value: 'Sam path' } });
    fireEvent.click(screen.getByTestId('topline-penetration-grid-save'));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ index_mpi_target_by_year: null, index_ari_target_by_year: [null, 98, null] }, 'Sam path'),
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
