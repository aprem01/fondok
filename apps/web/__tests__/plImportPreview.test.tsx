/**
 * FON-41 E-013 / E-017 — P&L Excel import: the preview dialog.
 *
 *   • Choosing a workbook POSTs it for a PREVIEW — nothing is applied yet.
 *   • The dialog lists every changed value old → new, every mapping error and
 *     non-numeric cell (historicals), and rejected rows + engine-computed
 *     edits (projections, "computed — edit the assumption instead").
 *   • Apply sends ONLY the previewed changes (with the old value, so the
 *     worker can skip a cell that moved) and hands the host the touched
 *     documents so it can refresh + re-model.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { HistImportPreview, ProjImportPreview } from '@/lib/api';

const HIST: HistImportPreview = {
  format: 'fondok.historicals.v1',
  changes: [{
    cell_ref: 'C6', cell_id: 'd2023::rooms_revenue', document_id: 'd2023', field_name: 'rooms_revenue',
    line_id: 'rooms_revenue', line_label: 'Rooms Revenue', period_label: 'FY 2023',
    filename: '2023 P&L.xlsx', old_value: 9_810_000, new_value: 9_900_000,
  }],
  mapping_errors: [{
    cell_ref: 'E23', cell_id: null, reason: 'no_source_line', line_label: 'Gross Operating Profit',
    period_label: 'FY 2024', detail: 'This statement has no extracted line here, so there is nothing at source to correct.',
  }],
  non_numeric: [{ cell_ref: 'C5', reason: 'non_numeric', line_label: 'ADR', period_label: 'FY 2023', raw: 'two hundred' }],
  unchanged: 41,
};

const PROJ: ProjImportPreview = {
  format: 'fondok.projections.v1',
  changes: [{ cell_ref: 'C4', key: 'revpar_growth', label: 'RevPAR growth', old_value: 0.045, new_value: 0.05, note: 'STR forecast', note_required: true }],
  rejected: [{ cell_ref: 'C5', key: 'expense_growth', label: 'Dept. expense inflation', reason: 'note_required', old_value: 0.035, new_value: 0.04, detail: 'This assumption moves the model — add a Note.' }],
  mapping_errors: [],
  non_numeric: [],
  computed_edits: [{ cell_ref: 'C2', reason: 'computed', line_label: 'Occupancy', period_label: 'Base year (Year 1)', old_value: 0.7, new_value: 0.75 }],
  unchanged: 9,
};

const previewHist = vi.fn(async () => HIST);
const previewProj = vi.fn(async () => PROJ);
const applyHist = vi.fn(async () => ({ applied: [{ cell_id: 'd2023::rooms_revenue' }], skipped: [] }));
const applyProj = vi.fn(async () => ({ applied: [{ key: 'revpar_growth' }], skipped: [], rerun_required: true }));
const download = vi.fn(async () => {});

vi.mock('@/lib/api', () => ({
  api: {
    plRoundTrip: {
      download: (...a: unknown[]) => download(...(a as [])),
      previewHistoricals: (...a: unknown[]) => previewHist(...(a as [])),
      applyHistoricals: (...a: unknown[]) => applyHist(...(a as [])),
      previewProjections: (...a: unknown[]) => previewProj(...(a as [])),
      applyProjections: (...a: unknown[]) => applyProj(...(a as [])),
    },
  },
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import { PlRoundTripControls } from '@/components/project/pl/PlRoundTrip';

const xlsx = () => new File(['PK'], 'edited.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });

beforeEach(() => {
  [previewHist, previewProj, applyHist, applyProj, download].forEach((m) => m.mockClear());
});
afterEach(cleanup);

describe('Historical P&L import preview (E-013)', () => {
  it('exports the workbook from the worker', async () => {
    render(<PlRoundTripControls dealId="deal-1" kind="historicals" />);
    fireEvent.click(screen.getByTestId('historicals-export-xlsx'));
    await waitFor(() => expect(download).toHaveBeenCalledWith('deal-1', 'historicals', 'fondok-historical-pl-deal-1.xlsx'));
  });

  it('previews changes / mapping errors / non-numeric cells, then applies only the changes', async () => {
    const onApplied = vi.fn();
    render(<PlRoundTripControls dealId="deal-1" kind="historicals" onApplied={onApplied} />);
    const file = xlsx();
    fireEvent.change(screen.getByTestId('historicals-import-file'), { target: { files: [file] } });
    const dialog = await screen.findByTestId('import-preview-dialog');
    expect(previewHist).toHaveBeenCalledWith('deal-1', file);
    expect(applyHist).not.toHaveBeenCalled(); // preview never writes

    const changes = within(dialog).getByTestId('import-changes');
    expect(within(changes).getByText(/Rooms Revenue · FY 2023/)).toBeInTheDocument();
    expect(within(changes).getByText('9,810,000')).toBeInTheDocument();
    expect(within(changes).getByText('9,900,000')).toBeInTheDocument();
    expect(within(dialog).getByTestId('import-section-mapping-errors')).toHaveTextContent('nothing at source to correct');
    expect(within(dialog).getByTestId('import-section-non-numeric-cells')).toHaveTextContent('two hundred');
    expect(within(dialog).getByText(/41 unchanged/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByTestId('import-apply'));
    await waitFor(() => expect(applyHist).toHaveBeenCalledWith('deal-1', [
      { cell_id: 'd2023::rooms_revenue', new_value: 9_900_000, old_value: 9_810_000 },
    ]));
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(
      { applied: [{ cell_id: 'd2023::rooms_revenue' }], skipped: [] }, ['d2023'],
    ));
    expect(screen.queryByTestId('import-preview-dialog')).not.toBeInTheDocument();
  });

  it('Cancel closes the preview without applying', async () => {
    render(<PlRoundTripControls dealId="deal-1" kind="historicals" />);
    fireEvent.change(screen.getByTestId('historicals-import-file'), { target: { files: [xlsx()] } });
    const dialog = await screen.findByTestId('import-preview-dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('import-preview-dialog')).not.toBeInTheDocument();
    expect(applyHist).not.toHaveBeenCalled();
  });
});

describe('Future P&L import preview (E-017)', () => {
  it('shows the note rule rejection and computed edits; Apply sends the assumption with its note', async () => {
    const onApplied = vi.fn();
    render(<PlRoundTripControls dealId="deal-1" kind="projections" onApplied={onApplied} />);
    fireEvent.change(screen.getByTestId('projections-import-file'), { target: { files: [xlsx()] } });
    const dialog = await screen.findByTestId('import-preview-dialog');
    const changes = within(dialog).getByTestId('import-changes');
    expect(within(changes).getByText('revpar_growth')).toBeInTheDocument();
    expect(within(changes).getByText('STR forecast')).toBeInTheDocument();
    expect(within(dialog).getByTestId('import-section-rejected')).toHaveTextContent('add a Note');
    expect(within(dialog).getByTestId('import-section-computed-cells')).toHaveTextContent('computed — edit the assumption instead');

    fireEvent.click(within(dialog).getByTestId('import-apply'));
    await waitFor(() => expect(applyProj).toHaveBeenCalledWith('deal-1', [
      { key: 'revpar_growth', new_value: 0.05, note: 'STR forecast', old_value: 0.045 },
    ]));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
  });
});
