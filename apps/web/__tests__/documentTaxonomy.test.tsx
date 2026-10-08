/**
 * Document taxonomy — Sam's decision 5 (Linear FON-41; R-027, R-031, R-034,
 * R-035, R-036, R-037, R-038).
 *
 * A DISPLAY taxonomy over the fixed DocType enum. Pinned here:
 *
 *  1. The wizard's upload slots run in Sam's order with Sam's labels.
 *  2. The new slots upload with the doc types the worker already knows
 *     (Hotel Program → ROOM_MIX, Comp Set / Market Reports → STR_TREND,
 *     Future CapEx → CAPEX).
 *  3. Due Diligence uses the same status colour as every other slot (it used
 *     to render gray because the dot was keyed off ``requiredForIc``).
 *  4. The Data Room's category rows mirror the slot labels and order.
 *  5. An STR report the analyst left on "Not sure" shows "Detected: …" from
 *     what the worker returned (extraction coverage note / Router / doc_type).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-tax' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      download: vi.fn().mockResolvedValue(undefined),
      reviewField: vi.fn().mockResolvedValue(undefined),
      reclassify: vi.fn().mockResolvedValue(undefined),
      acceptClassification: vi.fn().mockResolvedValue(undefined),
      acceptYear: vi.fn().mockResolvedValue(undefined),
    },
  },
}));
// The Data Room wiring test: one STR Trend the analyst left on "Not sure",
// extracted by the STR template (weekly STAR variant in the coverage note).
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [
      {
        id: 'str-1',
        filename: 'ANG-20250500-USD-E.xlsx',
        doc_type: 'STR_TREND',
        status: 'EXTRACTED',
        uploaded_at: '2026-01-01T00:00:00Z',
        size_bytes: 2048,
        user_provided_doc_type: null,
        ai_proposed_doc_type: null,
      },
    ],
    uploading: false,
    upload: vi.fn(),
    extractions: {
      'str-1': {
        status: 'EXTRACTED',
        confidence_report: {
          overall: 1,
          by_field: {},
          low_confidence_fields: [],
          requires_human_review: false,
          template: 'str_trend',
          coverage_note: 'variant=weekly_star_xlsx; weekly STAR report: comp-set roster + rollups extracted',
        },
        fields: [],
      },
    },
    error: null,
    refresh: vi.fn(),
    refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));
vi.mock('@/lib/hooks/useEngineOutputs', () => ({ useEngineOutputs: () => ({ outputs: null }) }));
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run: vi.fn() }) }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({ WORKSHEET_ROWS: [] }));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));

import type { WizardFile } from '@/lib/api';
import { DocumentsStep, WIZARD_CATEGORIES } from '@/components/project/wizard/DocumentsStep';
import {
  CATEGORIES as DATA_ROOM_CATEGORIES,
  DocumentCoverage,
  detectedStrReportType,
  type CoverageFile,
} from '@/components/project/DocumentCoverage';
import DataRoomTab from '@/components/project/DataRoomTab';

afterEach(cleanup);

const EXPECTED_SLOTS: [string, string][] = [
  ['om', 'Offering Memorandum'],
  ['room_mix', 'Hotel Program'],
  ['financials', 'Financial Statements'],
  ['str', 'STR Reports'],
  ['comp_set', 'Comp Set / Market Reports'],
  ['capex', 'Historic CapEx'],
  ['insurance', 'Insurance Records'],
  ['property_tax', 'Property Taxes'],
  ['future_capex', 'Future CapEx'],
  ['property_info', 'Other Property Info'],
  ['leases', 'Leases & Agreements'],
  ['surveys', 'Due Diligence'],
];

function renderStep(files: WizardFile[] = [], onChange = vi.fn()) {
  render(
    <DocumentsStep files={files} onChange={onChange} onCanContinueChange={vi.fn()} />,
  );
  return onChange;
}

function sidebarLabels(): string[] {
  const nav = screen.getByRole('navigation', { name: 'Document categories' });
  return within(nav)
    .getAllByRole('button')
    .map((b) => (b.getAttribute('aria-label') ?? '').replace(/ \(.*\)$/, ''));
}

describe('wizard upload slots — order and labels', () => {
  it('runs in Sam’s order with Sam’s labels', () => {
    expect(WIZARD_CATEGORIES.map((c) => [c.id, c.label])).toEqual(EXPECTED_SLOTS);
    renderStep();
    expect(sidebarLabels()).toEqual(EXPECTED_SLOTS.map(([, label]) => label));
  });

  it('carries the new guidance copy', () => {
    const byId = Object.fromEntries(WIZARD_CATEGORIES.map((c) => [c.id, c]));
    expect(byId.room_mix.description).toMatch(/floor plans, design documents, and program summaries/);
    expect(byId.comp_set.description).toMatch(/CoStar submarket, pipeline, and sales reports plus comp-set definitions/);
    expect(byId.future_capex.description).toMatch(/PIP budgets and rebranding \/ repositioning capital plans/);
    expect(byId.surveys.description).toMatch(/Property condition reports \(PCRs\), legal memos, surveys, and reviews/);
    // R-038 — agreements first, in order; floor plans gone from this slot.
    const info = byId.property_info.description;
    const order = ['Management Agreement', 'Franchise Agreement', 'business plans', 'ownership / entity'];
    const idx = order.map((t) => info.indexOf(t));
    expect(idx.every((i) => i >= 0)).toBe(true);
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
    expect(info.toLowerCase()).not.toContain('floor');
    expect(byId.property_info.exampleChip.toLowerCase()).not.toContain('floor');
  });

  it('keeps the STR report-type sub-select with a "Not sure" option', () => {
    const str = WIZARD_CATEGORIES.find((c) => c.id === 'str')!;
    expect(str.picker?.options.map((o) => o.label)).toEqual([
      'STR Trend (TTM)',
      'STR Star (Daily)',
      'Not sure',
    ]);
  });
});

describe('wizard upload slots — doc types sent', () => {
  it.each([
    ['Hotel Program', 'room_mix', 'ROOM_MIX'],
    ['Comp Set / Market Reports', 'comp_set', 'STR_TREND'],
    ['Historic CapEx', 'capex', 'CAPEX'],
    ['Future CapEx', 'future_capex', 'CAPEX'],
    ['Other Property Info', 'property_info', 'PROPERTY_INFO'],
    ['Due Diligence', 'surveys', 'SURVEYS'],
  ])('%s stages files as %s → %s', (label, category, docType) => {
    const onChange = renderStep();
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label.replace(/[/]/g, '\\/')} \\(`) }));
    const input = screen.getByLabelText(`Add ${label} files`) as HTMLInputElement;
    const file = new File(['x'], 'doc.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onChange).toHaveBeenCalledTimes(1);
    const staged = onChange.mock.calls[0][0] as WizardFile[];
    expect(staged).toHaveLength(1);
    expect(staged[0].category).toBe(category);
    expect(staged[0].user_doc_type).toBe(docType);
  });
});

describe('Due Diligence status colour (R-031)', () => {
  it('matches every other empty slot instead of rendering gray', () => {
    renderStep();
    const dd = screen.getByTestId('slot-status-surveys').className;
    expect(dd).toBe(screen.getByTestId('slot-status-insurance').className);
    expect(dd).toBe(screen.getByTestId('slot-status-om').className);
    expect(dd).not.toContain('bg-ink-300');
  });

  it('matches a covered slot once it has a file', () => {
    const f = (category: WizardFile['category'], name: string): WizardFile => ({
      file: new File(['x'], name),
      category,
      user_doc_type: null,
    });
    renderStep([f('surveys', 'pcr.pdf'), f('insurance', 'coi.pdf')]);
    expect(screen.getByTestId('slot-status-surveys').className).toBe(
      screen.getByTestId('slot-status-insurance').className,
    );
  });
});

describe('Data Room categories mirror the slots', () => {
  it('uses the slot labels in the slot order', () => {
    const slotIds = WIZARD_CATEGORIES.map((c) => c.id as string);
    const dataRoomSlots = DATA_ROOM_CATEGORIES.filter((c) => slotIds.includes(c.id));
    expect(dataRoomSlots.map((c) => [c.id, c.label])).toEqual(EXPECTED_SLOTS);
    // Extra buckets (Debt / Partnership / Other) only ever follow the slots.
    const firstExtra = DATA_ROOM_CATEGORIES.findIndex((c) => !slotIds.includes(c.id));
    expect(firstExtra).toBe(EXPECTED_SLOTS.length);
  });

  it('renders the rows in that order', () => {
    const { container } = render(
      <DocumentCoverage files={[]} onReclassify={vi.fn()} onOpenDoc={vi.fn()} />,
    );
    const ids = Array.from(container.querySelectorAll('li[data-category]')).map((li) =>
      li.getAttribute('data-category'),
    );
    expect(ids.slice(0, EXPECTED_SLOTS.length)).toEqual(EXPECTED_SLOTS.map(([id]) => id));
    // Due Diligence is a regular row now — no muted "optional" tag.
    const dd = container.querySelector('li[data-category="surveys"]')!;
    expect(dd.textContent).not.toMatch(/optional/);
  });

  it('files market studies under Comp Set / Market Reports and CapEx under Historic CapEx', () => {
    const files: CoverageFile[] = [
      { id: 'm1', name: 'CoStar Submarket.pdf', docType: 'MARKET_STUDY', fields: 3, confidence: 90, toReview: 0, fiscalYear: null, status: 'EXTRACTED' },
      { id: 'c1', name: 'PIP Budget.xlsx', docType: 'CAPEX', fields: 3, confidence: 90, toReview: 0, fiscalYear: null, status: 'EXTRACTED' },
    ];
    const { container } = render(
      <DocumentCoverage files={files} onReclassify={vi.fn()} onOpenDoc={vi.fn()} />,
    );
    expect(container.querySelector('li[data-category="comp_set"]')!.textContent).toContain('CoStar Submarket.pdf');
    expect(container.querySelector('li[data-category="capex"]')!.textContent).toContain('PIP Budget.xlsx');
    expect(container.querySelector('li[data-category="future_capex"]')!.textContent).toMatch(
      /listed under Historic CapEx/,
    );
  });
});

describe('detected STR report type (R-034)', () => {
  it('reads the template variant first, then the Router, then doc_type', () => {
    expect(detectedStrReportType('STR_TREND', { coverageNote: 'variant=monthly_star_xlsx; …' })).toBe(
      'STR Trend (TTM) · monthly STAR',
    );
    expect(detectedStrReportType('STR_TREND', { coverageNote: 'variant=daily_star_xlsx; …' })).toBe(
      'STR Star (Daily)',
    );
    expect(detectedStrReportType('STR_TREND', { aiProposedDocType: 'STR' })).toBe('STR Star (Daily)');
    expect(detectedStrReportType('STR_TREND', {})).toBe('STR Trend (TTM)');
    // Nothing the worker returned names an STR report → no guess.
    expect(detectedStrReportType('OM', { coverageNote: 'forecast tables' })).toBeNull();
  });

  const strFile: CoverageFile = {
    id: 's1', name: 'STR export.xlsx', docType: 'STR_TREND', fields: 20, confidence: 100,
    toReview: 0, fiscalYear: null, status: 'EXTRACTED',
  };

  it('shows "Detected: …" next to the type select when the analyst left "Not sure"', () => {
    render(
      <DocumentCoverage
        files={[strFile]}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
        docMeta={{ s1: { userProvidedDocType: null, coverageNote: 'variant=weekly_star_xlsx; weekly STAR' } }}
      />,
    );
    expect(screen.getByTestId('detected-report-type').textContent).toBe('Detected: STR Star (Weekly)');
    // The report-type select still renders the stored type.
    expect(
      (screen.getByLabelText('Document type for STR export.xlsx') as HTMLSelectElement).value,
    ).toBe('STR_TREND');
  });

  it('stays quiet when the analyst picked the report type themselves', () => {
    render(
      <DocumentCoverage
        files={[strFile]}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
        docMeta={{ s1: { userProvidedDocType: 'STR_TREND', coverageNote: 'variant=weekly_star_xlsx; weekly STAR' } }}
      />,
    );
    expect(screen.queryByTestId('detected-report-type')).toBeNull();
  });

  it('is wired from the Data Room’s worker document + extraction', () => {
    render(<DataRoomTab projectId="deal-uuid-tax" />);
    expect(screen.getByTestId('detected-report-type').textContent).toBe('Detected: STR Star (Weekly)');
  });
});
