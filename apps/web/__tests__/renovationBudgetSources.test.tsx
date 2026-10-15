/**
 * FON-101 — "Renovation budget sources" disclosure.
 *
 * The list is the worker's (`GET /deals/{id}/renovation-budget-candidates`):
 * every candidate (OM figure, each PIP / CapEx document with date + status),
 * the one the model is on, and why; Future CapEx documents listed separately
 * as reference only; "Use this" writes `renovation_budget` as an analyst
 * override WITH a prefilled, editable note.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { RenovationBudgetCandidatesResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/projects/deal-uuid-1',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

const FIXTURE: RenovationBudgetCandidatesResponse = {
  id: 'deal-uuid-1',
  selected_source: 'pip_document',
  selected_value_usd: 7_100_000,
  keys: 100,
  precedence: 'analyst override > latest-dated PIP / CapEx document > OM broker pro forma > seed; Future CapEx documents are reference only',
  candidates: [
    {
      id: 'document:new', kind: 'pip_document', label: 'Approved Capital Plan 2026.xlsx', filename: 'Approved Capital Plan 2026.xlsx',
      value_usd: 7_100_000, per_key_usd: 71_000, document_id: 'doc-new', page: 4, field_name: 'capex_plan.pip.total_usd',
      date: '2026-04-01', date_basis: 'plan_date', status: 'approved_capital_plan', status_label: 'Approved capital plan',
      prepared_by: 'Owner', selected: true, reason: 'Latest-dated PIP / CapEx document — outranks the OM\'s broker figure.',
    },
    {
      id: 'document:old', kind: 'pip_document', label: 'Brand PIP 2025.pdf', filename: 'Brand PIP 2025.pdf',
      value_usd: 5_000_000, per_key_usd: 50_000, document_id: 'doc-old', page: 4, field_name: 'capex_plan.pip.total_usd',
      date: '2025-11-01', date_basis: 'plan_date', status: 'brand_issued_pip', status_label: 'Brand-issued PIP',
      prepared_by: 'Hilton', selected: false, reason: 'Superseded by Approved Capital Plan 2026.xlsx dated 2026-04-01.',
    },
    {
      id: 'document:pending', kind: 'pip_document', label: 'pending.pdf', filename: 'pending.pdf',
      value_usd: null, document_id: 'doc-pending', selected: false, reason: 'Not extracted yet — no figure to read.',
    },
    {
      id: 'om:renovation_budget', kind: 'om_broker', label: 'OM.pdf', filename: 'OM.pdf',
      value_usd: 8_000_000, per_key_usd: 80_000, document_id: 'doc-om', page: 31, field_name: 'broker_proforma.renovation_budget_usd',
      date: null, date_basis: null, status: 'broker_pro_forma', status_label: 'OM broker pro forma',
      selected: false, reason: 'Outranked by the PIP / CapEx document Approved Capital Plan 2026.xlsx dated 2026-04-01 — a separate PIP is the more detailed source.',
    },
  ],
  reference_only: [
    {
      id: 'document:future', document_id: 'doc-future', filename: '5yr plan.xlsx', date: '2026-09-01', date_basis: 'plan_date',
      prepared_by: null, total_usd: 2_000_000, extracted: true, pip_total_usd: null,
      items: [
        { index: 1, name: 'Roof replacement', year: 2028, usd: 800_000, category: 'building_systems' },
        { index: 2, name: 'Soft-goods refresh', year: 2029, usd: 1_200_000, category: 'guestrooms' },
      ],
      reason: 'Future CapEx — reference only, never incorporated in the model.',
    },
  ],
};

let fixture: RenovationBudgetCandidatesResponse | Error = FIXTURE;
const candidatesSpy = vi.fn(async () => {
  if (fixture instanceof Error) throw fixture;
  return fixture;
});
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, renovationBudgetCandidates: (...a: unknown[]) => candidatesSpy(...(a as [])) },
    },
  };
});

import RenovationBudgetSources, {
  prefillNote, fmtPlanDate, referenceLaneLabel, SOURCES_TITLE, REFERENCE_ONLY_TITLE, USE_THIS_LABEL, IN_THE_MODEL_LABEL,
} from '@/components/project/RenovationBudgetSources';

beforeEach(() => { candidatesSpy.mockClear(); fixture = FIXTURE; });
afterEach(cleanup);

describe('RenovationBudgetSources — the candidate list', () => {
  it('lists every candidate with value, date, status and reason, and marks the one in the model', async () => {
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} defaultOpen />);
    await waitFor(() => expect(candidatesSpy).toHaveBeenCalledWith('deal-uuid-1', expect.anything()));

    expect(screen.getByText(SOURCES_TITLE)).toBeInTheDocument();
    const rows = await screen.findAllByTestId(/^renovation-candidate-(pip_document|om_broker|analyst_override)$/);
    expect(rows).toHaveLength(4);

    const selected = rows.find((r) => r.getAttribute('data-selected') === 'true')!;
    expect(within(selected).getByText('Approved Capital Plan 2026.xlsx')).toBeInTheDocument();
    expect(within(selected).getByText('$7,100,000')).toBeInTheDocument();
    expect(within(selected).getByText(IN_THE_MODEL_LABEL)).toBeInTheDocument();
    expect(within(selected).getByText('Apr 1, 2026 (plan date)')).toBeInTheDocument();
    expect(within(selected).getByText('Approved capital plan')).toBeInTheDocument();
    expect(within(selected).getByText('$71,000 / key')).toBeInTheDocument();
    // The model's source is never offered as a switch.
    expect(within(selected).queryByText(USE_THIS_LABEL)).toBeNull();

    // The OM figure stays visible, with its source badge and the worker's reason.
    const om = screen.getByTestId('renovation-candidate-om_broker');
    expect(within(om).getByText('OM.pdf')).toBeInTheDocument();
    expect(within(om).getByText('$8,000,000')).toBeInTheDocument();
    expect(within(om).getByText(/Outranked by the PIP \/ CapEx document/)).toBeInTheDocument();
    expect(within(om).getByText(USE_THIS_LABEL)).toBeInTheDocument();

    // Superseded PIP: dated, reasoned, selectable.
    expect(screen.getByText('Superseded by Approved Capital Plan 2026.xlsx dated 2026-04-01.')).toBeInTheDocument();
    expect(screen.getByText('Brand-issued PIP')).toBeInTheDocument();

    // A document without an extracted total renders "—" with the reason and no "Use this".
    const pending = rows.find((r) => within(r).queryByText('pending.pdf'))!;
    expect(within(pending).getByText('—')).toBeInTheDocument();
    expect(within(pending).getByText('Not extracted yet — no figure to read.')).toBeInTheDocument();
    expect(within(pending).queryByText(USE_THIS_LABEL)).toBeNull();

    // The summary line names the selected source and figure when collapsed too.
    expect(screen.getByTestId('renovation-budget-sources-toggle')).toHaveTextContent('Approved Capital Plan 2026.xlsx · $7,100,000');
  });

  it('lists Future CapEx documents separately as reference only, with their items', async () => {
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} defaultOpen />);
    const ref = await screen.findByTestId('renovation-budget-reference-only');
    expect(within(ref).getByText(REFERENCE_ONLY_TITLE)).toBeInTheDocument();
    expect(within(ref).getByText('5yr plan.xlsx')).toBeInTheDocument();
    expect(within(ref).getByText('$2,000,000')).toBeInTheDocument();
    expect(within(ref).getByText(/2028 · Roof replacement/)).toBeInTheDocument();
    expect(within(ref).getByText('$800,000')).toBeInTheDocument();
    expect(within(ref).getByText(/2029 · Soft-goods refresh/)).toBeInTheDocument();
    expect(within(ref).getByText('Future CapEx — reference only, never incorporated in the model.')).toBeInTheDocument();
    // Never offered as a candidate.
    expect(within(ref).queryByText(USE_THIS_LABEL)).toBeNull();
  });

  it('labels a Historic CapEx ledger by its own lane (never "Future CapEx") and prints its by-year totals', async () => {
    fixture = {
      ...FIXTURE,
      reference_only: [
        {
          id: 'document:hist', lane: 'historic', lane_label: 'Historic CapEx', document_id: 'doc-hist',
          filename: 'CapEx 2022-2024.xlsx', date: '2026-09-01', date_basis: 'plan_date', prepared_by: null,
          total_usd: 1_000_000, extracted: true, pip_total_usd: 12_000_000,
          items: [{ index: 1, name: 'Chiller', year: 2022, usd: 300_000, category: 'building_systems' }],
          by_year: { '2023': 700_000, '2022': 300_000 },
          reason: 'Historic CapEx — reference only, never incorporated in the model. It states a PIP total; re-tag it as PIP / Renovation budget in the Data Room to make it a candidate.',
        },
        FIXTURE.reference_only[0],
      ],
    };
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} defaultOpen />);
    const ref = await screen.findByTestId('renovation-budget-reference-only');
    const rows = within(ref).getAllByTestId('renovation-reference-doc');
    expect(rows).toHaveLength(2);
    const hist = rows[0];
    expect(hist.getAttribute('data-lane')).toBe('historic');
    expect(within(hist).getByText('Historic CapEx')).toBeInTheDocument();
    expect(within(hist).queryByText('Future CapEx')).toBeNull();
    expect(within(hist).getByText('$1,000,000')).toBeInTheDocument();
    const byYear = within(hist).getByTestId('renovation-reference-by-year');
    // Sorted by year, as printed — not by the object's key order.
    expect(byYear.textContent).toBe('By year:2022 $300,0002023 $700,000');
    expect(within(hist).getByText(/re-tag it as PIP \/ Renovation budget/)).toBeInTheDocument();
    // The Future CapEx row keeps its own lane label and has no by-year block.
    const fut = rows[1];
    expect(within(fut).getByText('Future CapEx')).toBeInTheDocument();
    expect(within(fut).queryByTestId('renovation-reference-by-year')).toBeNull();
    // Neither lane is ever offered as a candidate.
    expect(within(ref).queryByText(USE_THIS_LABEL)).toBeNull();
  });

  it('referenceLaneLabel falls back by lane id, then to Future CapEx for a worker that sends neither field', () => {
    expect(referenceLaneLabel({ lane: 'historic' })).toBe('Historic CapEx');
    expect(referenceLaneLabel({ lane: 'future' })).toBe('Future CapEx');
    expect(referenceLaneLabel({})).toBe('Future CapEx');
    expect(referenceLaneLabel({ lane: 'historic', lane_label: 'Historic CapEx (ledger)' })).toBe('Historic CapEx (ledger)');
  });

  it('"Use this" prefills the justification from the document and date, lets the analyst edit it, and writes the override through onUse', async () => {
    const onUse = vi.fn(async () => {});
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={onUse} defaultOpen />);
    const om = await screen.findByTestId('renovation-candidate-om_broker');
    fireEvent.click(within(om).getByText(USE_THIS_LABEL));

    const note = within(om).getByLabelText('Override justification') as HTMLInputElement;
    expect(note.value).toBe('Selected OM.pdf (OM broker pro forma)');
    fireEvent.change(note, { target: { value: 'Selected OM.pdf — brand PIP not yet final' } });
    fireEvent.click(within(om).getByLabelText('Confirm'));

    await waitFor(() => expect(onUse).toHaveBeenCalledWith(8_000_000, 'Selected OM.pdf — brand PIP not yet final'));
  });

  it('a PIP candidate\'s prefilled note names the document, its date and its status; a blank note cannot be confirmed', async () => {
    const onUse = vi.fn(async () => {});
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={onUse} defaultOpen />);
    const rows = await screen.findAllByTestId('renovation-candidate-pip_document');
    const old = rows.find((r) => within(r).queryByText('Brand PIP 2025.pdf'))!;
    fireEvent.click(within(old).getByText(USE_THIS_LABEL));
    const note = within(old).getByLabelText('Override justification') as HTMLInputElement;
    expect(note.value).toBe('Selected Brand PIP 2025.pdf dated 2025-11-01 (Brand-issued PIP)');

    fireEvent.change(note, { target: { value: '   ' } });
    expect((within(old).getByLabelText('Confirm') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(old).getByLabelText('Confirm'));
    expect(onUse).not.toHaveBeenCalled();

    fireEvent.click(within(old).getByLabelText('Cancel'));
    expect(within(old).queryByLabelText('Override justification')).toBeNull();
  });

  it('is collapsed by default and opens on click; renders nothing off-line', async () => {
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} />);
    const toggle = await screen.findByTestId('renovation-budget-sources-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('renovation-candidate-om_broker')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(await screen.findByTestId('renovation-candidate-om_broker')).toBeInTheDocument();

    cleanup();
    candidatesSpy.mockClear();
    render(<RenovationBudgetSources dealId="deal-uuid-1" live={false} onUse={vi.fn()} defaultOpen />);
    expect(screen.queryByTestId('renovation-budget-sources')).toBeNull();
    expect(candidatesSpy).not.toHaveBeenCalled();
  });

  it('says so when the worker returns no candidates (seed) and when the fetch fails — never a placeholder figure', async () => {
    fixture = { ...FIXTURE, selected_source: 'seed', selected_value_usd: 5_280_000, candidates: [], reference_only: [] };
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} defaultOpen />);
    expect(await screen.findByTestId('renovation-budget-sources-empty')).toHaveTextContent('the model is on the seed');
    expect(screen.getByTestId('renovation-budget-sources-toggle')).toHaveTextContent('Seed — no document states a budget');

    cleanup();
    fixture = new Error('boom');
    render(<RenovationBudgetSources dealId="deal-uuid-1" live onUse={vi.fn()} defaultOpen />);
    await waitFor(() => expect(screen.getByTestId('renovation-budget-sources-toggle')).toHaveTextContent('Sources unavailable'));
    expect(screen.queryByText(/\$[\d,]+/)).toBeNull();
  });
});

describe('RenovationBudgetSources — helpers', () => {
  it('prefillNote names the document, the date and the status; fmtPlanDate is timezone-safe', () => {
    expect(prefillNote(FIXTURE.candidates[0])).toBe('Selected Approved Capital Plan 2026.xlsx dated 2026-04-01 (Approved capital plan)');
    expect(prefillNote(FIXTURE.candidates[3])).toBe('Selected OM.pdf (OM broker pro forma)');
    expect(prefillNote({ ...FIXTURE.candidates[1], filename: null, label: '', status_label: null })).toBe('Selected the PIP / CapEx document dated 2025-11-01');
    expect(fmtPlanDate('2026-04-01')).toBe('Apr 1, 2026');
    expect(fmtPlanDate('2026-12-31T00:00:00Z')).toBe('Dec 31, 2026');
    expect(fmtPlanDate(null)).toBeNull();
    expect(fmtPlanDate('Q3 2027')).toBeNull();
  });
});
