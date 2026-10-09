/**
 * R-026 — one "Drop everything here" zone (wizard + Data Room) with automatic
 *         classification the analyst confirms or corrects.
 * R-030 — PowerPoint and Google Sheets / Slides exports accepted; legacy /
 *         foreign formats get a "convert it first" message.
 * R-067 — a CBRE / HotStats P&L benchmark has a visible slot, is filed under
 *         Comp Set / Market Reports, and shows as the Future P&L's read-only
 *         Benchmark column.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';

vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

import {
  UPLOAD_ACCEPT,
  isUploadableFile,
  unsupportedFileMessage,
} from '@/lib/uploadFormats';
import { DocumentsStep, WIZARD_CATEGORIES } from '@/components/project/wizard/DocumentsStep';
import type { WizardFile } from '@/lib/api';
import {
  DocumentCoverage,
  categoryForFile,
  isUnconfirmedAutoClassification,
  type CoverageFile,
} from '@/components/project/DocumentCoverage';
import {
  BenchmarkComparisonTable,
  modelRatioFor,
  parseBenchmarkBlock,
} from '@/components/project/pl/BenchmarkComparison';

afterEach(cleanup);

// ───────────────────────────── R-030 ─────────────────────────────

describe('upload formats (R-030)', () => {
  it('accepts PowerPoint and the Google Sheets / Slides export formats', () => {
    for (const name of ['OM.pptx', 'T12.xlsx', 'T12.xlsm', 'STR.xls', 'export.csv', 'memo.docx', 'om.pdf']) {
      expect(isUploadableFile(name)).toBe(true);
    }
    expect(UPLOAD_ACCEPT).toContain('.pptx');
    expect(UPLOAD_ACCEPT).not.toContain('.ppt,');
  });

  it('rejects legacy / foreign formats with the conversion to do', () => {
    for (const name of ['deck.ppt', 'deck.key', 'memo.doc', 'model.ods', 'T12.gsheet', 'OM.gslides']) {
      expect(isUploadableFile(name)).toBe(false);
    }
    expect(unsupportedFileMessage('deck.key')).toMatch(/unsupported file type — Keynote/);
    expect(unsupportedFileMessage('deck.ppt')).toMatch(/re-save the deck as \.pptx/);
    expect(unsupportedFileMessage('T12.gsheet')).toMatch(/File → Download → Microsoft Excel/);
    // Unknown types keep the generic copy (the e2e suite keys on the phrase).
    expect(unsupportedFileMessage('photo.heic')).toMatch(/unsupported file type — Fondok accepts PDF.*PowerPoint/);
  });
});

// ───────────────────────────── R-026 wizard ─────────────────────────────

function renderStep(files: WizardFile[] = []) {
  const onChange = vi.fn();
  const onCanContinueChange = vi.fn();
  render(
    <DocumentsStep files={files} onChange={onChange} onCanContinueChange={onCanContinueChange} />,
  );
  return { onChange, onCanContinueChange };
}

describe('wizard "Drop everything here" zone (R-026)', () => {
  it('stages files untagged, in the auto bucket, above the per-category slots', () => {
    const { onChange } = renderStep();
    const zone = screen.getByTestId('wizard-auto-classify');
    expect(zone.textContent).toMatch(/Drop everything here/);
    expect(zone.textContent).toMatch(/Google Sheets \/ Slides: File → Download → Microsoft Excel \/ PowerPoint/);
    // The per-category slots are still there.
    expect(screen.getByRole('navigation', { name: 'Document categories' })).toBeTruthy();

    const input = screen.getByLabelText('Add files for automatic classification') as HTMLInputElement;
    const files = [
      new File(['x'], 'Harbor OM.pptx'),
      new File(['x'], 'T12 2025.xlsx'),
      new File(['x'], 'old.ppt'),
    ];
    fireEvent.change(input, { target: { files } });
    expect(onChange).toHaveBeenCalledTimes(1);
    const staged = onChange.mock.calls[0][0] as WizardFile[];
    // .ppt is filtered out client-side; the rest go with NO user tag.
    expect(staged.map((f) => f.file.name)).toEqual(['Harbor OM.pptx', 'T12 2025.xlsx']);
    for (const f of staged) {
      expect(f.category).toBe('auto');
      expect(f.user_doc_type).toBeNull();
    }
  });

  it('lists staged auto files and lets them clear the documents gate', () => {
    const auto: WizardFile = { file: new File(['x'], 'dataroom.pdf'), category: 'auto', user_doc_type: null };
    const { onCanContinueChange } = renderStep([auto]);
    const list = screen.getByRole('list', { name: 'Files to classify automatically' });
    expect(within(list).getByText('dataroom.pdf')).toBeTruthy();
    expect(onCanContinueChange).toHaveBeenLastCalledWith(true);
  });

  it('keeps the gate closed with nothing staged', () => {
    const { onCanContinueChange } = renderStep();
    expect(onCanContinueChange).toHaveBeenLastCalledWith(false);
  });
});

// ───────────────────────────── R-067 wizard slot ─────────────────────────────

describe('P&L benchmark slot (R-067)', () => {
  it('the Comp Set / Market slot names the CBRE benchmark and can tag it', () => {
    const comp = WIZARD_CATEGORIES.find((c) => c.id === 'comp_set')!;
    expect(comp.description).toMatch(/CBRE \/ HotStats P&L benchmark/);
    expect(comp.defaultDocType).toBe('STR_TREND');
    const opt = comp.picker?.options.find((o) => o.value === 'PNL_BENCHMARK');
    expect(opt?.label).toBe('P&L Benchmark (CBRE / HotStats)');
    expect(comp.picker?.options.map((o) => o.label)).toContain('Not sure');
  });

  it('the Data Room files a benchmark under Comp Set / Market Reports, not Financial Statements', () => {
    expect(categoryForFile({ docType: 'PNL_BENCHMARK' })?.id).toBe('comp_set');
    expect(categoryForFile({ docType: 'CBRE_HORIZONS' })?.id).toBe('comp_set');
    expect(categoryForFile({ docType: 'T12' })?.id).toBe('financials');
  });
});

// ───────────────────────────── R-026 Data Room ─────────────────────────────

const file = (over: Partial<CoverageFile> = {}): CoverageFile => ({
  id: 'd1',
  name: 'Harbor OM.pptx',
  docType: 'OM',
  fields: 12,
  confidence: 95,
  toReview: 0,
  fiscalYear: null,
  status: 'EXTRACTED',
  ...over,
});

describe('"Classified automatically — confirm" chip (R-026)', () => {
  it('shows only for a classified, untagged, unconfirmed document', () => {
    expect(isUnconfirmedAutoClassification(file(), { userProvidedDocType: null })).toBe(true);
    expect(isUnconfirmedAutoClassification(file(), { userProvidedDocType: 'OM' })).toBe(false);
    expect(isUnconfirmedAutoClassification(file({ status: 'EXTRACTING' }), { userProvidedDocType: null })).toBe(false);
    expect(isUnconfirmedAutoClassification(file({ status: 'FAILED' }), { userProvidedDocType: null })).toBe(false);
    expect(isUnconfirmedAutoClassification(file({ docType: '' }), { userProvidedDocType: null })).toBe(false);
    expect(isUnconfirmedAutoClassification(file(), undefined)).toBe(false);
  });

  it('lists the file in its assigned category and confirms through the callback', () => {
    const onConfirm = vi.fn();
    const onReclassify = vi.fn();
    const { container } = render(
      <DocumentCoverage
        files={[file(), file({ id: 'd2', name: 'T12.xlsx', docType: 'T12' })]}
        onReclassify={onReclassify}
        onOpenDoc={vi.fn()}
        onConfirmClassification={onConfirm}
        docMeta={{
          d1: { userProvidedDocType: null, extractionLoaded: true },
          d2: { userProvidedDocType: 'T12', extractionLoaded: true },
        }}
      />,
    );
    const omRow = container.querySelector('li[data-category="om"]')!;
    expect(omRow.textContent).toContain('Harbor OM.pptx');
    const chips = screen.getAllByTestId('auto-classified-chip');
    expect(chips).toHaveLength(1); // the analyst-tagged T-12 has none
    expect(chips[0].textContent).toBe('Classified automatically — confirm');
    fireEvent.click(chips[0]);
    expect(onConfirm).toHaveBeenCalledWith('d1');
    // The existing reclassify control stays next to it for corrections.
    const typeSelect = within(omRow as HTMLElement).getByLabelText('Document type for Harbor OM.pptx');
    fireEvent.change(typeSelect, { target: { value: 'PNL_BENCHMARK' } });
    expect(onReclassify).toHaveBeenCalledWith('d1', { doc_type: 'PNL_BENCHMARK' });
  });
});

// ───────────────────────────── R-067 Benchmark column ─────────────────────────────

const MARKET_DATA = {
  deal_id: 'deal-1',
  pnl_benchmark: {
    peer_set_size: 9,
    categories: [
      { key: 'rooms', label: 'Rooms Expense', benchmark_line: 'rooms_dept_expense', ratio: 0.25,
        ratio_basis: 'department_revenue', ratio_source: 'computed_from_totals', par_usd: 12000, por_usd: 45.5 },
      { key: 'administrative_general', label: 'Administrative & General', benchmark_line: 'a_and_g',
        ratio: 0.08, ratio_basis: 'total_revenue', ratio_source: 'reported', par_usd: null, por_usd: null },
      { key: 'insurance', label: 'Insurance', benchmark_line: 'insurance', ratio: null,
        ratio_basis: 'total_revenue', ratio_source: null, par_usd: null, por_usd: 6.25 },
    ],
  },
};

const YEAR1 = {
  roomsRevenue: 10_000_000,
  fbRevenue: 3_000_000,
  totalRevenue: 14_000_000,
  deptRoomsExpense: 2_700_000,
  undistAdminGeneral: 1_120_000,
  // no insurance line from the engine → model cell stays "—"
};

describe('Future P&L Benchmark column (R-067)', () => {
  it('is absent when the deal has no benchmark', () => {
    expect(parseBenchmarkBlock({ deal_id: 'x', pnl_benchmark: null })).toBeNull();
    expect(parseBenchmarkBlock({ deal_id: 'x', pnl_benchmark: { peer_set_size: 3, categories: [] } })).toBeNull();
    expect(parseBenchmarkBlock(null)).toBeNull();
    // Older worker without the categories list.
    expect(parseBenchmarkBlock({ pnl_benchmark: { gop_margin: 0.3 } })).toBeNull();
  });

  it('compares the model ratio on the benchmark’s own basis', () => {
    expect(modelRatioFor({ key: 'rooms', ratio_basis: 'department_revenue' }, YEAR1)).toBeCloseTo(0.27);
    expect(modelRatioFor({ key: 'administrative_general', ratio_basis: 'total_revenue' }, YEAR1)).toBeCloseTo(0.08);
    expect(modelRatioFor({ key: 'insurance', ratio_basis: 'total_revenue' }, YEAR1)).toBeNull();
    expect(modelRatioFor({ key: 'rooms', ratio_basis: 'department_revenue' }, null)).toBeNull();
  });

  it('renders Model, Benchmark, delta, POR and PAR without inventing values', () => {
    const block = parseBenchmarkBlock(MARKET_DATA)!;
    expect(block.peer_set_size).toBe(9);
    const { container } = render(<BenchmarkComparisonTable block={block} modelYear={YEAR1} />);
    expect(screen.getByTestId('benchmark-column').textContent).toBe('Benchmark');
    const cells = (key: string) =>
      Array.from(container.querySelectorAll(`tr[data-benchmark-key="${key}"] td`)).map((td) => td.textContent);
    expect(cells('rooms')).toEqual([
      'Rooms Expense% of Rooms revenue', '27.0%', '25.0%', '+2.0 pts', '$45.5', '$12,000',
    ]);
    expect(cells('administrative_general')).toEqual([
      'Administrative & General% of Total revenue', '8.0%', '8.0%', '+0.0 pts', '—', '—',
    ]);
    expect(cells('insurance')).toEqual([
      'Insurance% of Total revenue', '—', '—', '—', '$6.25', '—',
    ]);
  });
});
