/**
 * Phase 5.3 — evidence ablation on the web side.
 *
 * The worker tests prove that deleting a document removes everything derived
 * from it in the model. These prove the same thing one layer up: the two
 * surfaces that claim provenance — the Historicals grid and the Data Room's
 * "N to review" badge — must stop showing a statement's columns, cells and
 * counts the moment that statement leaves the deal, and must leave every
 * other statement's cells untouched.
 *
 * The inputs are the FON-41 live fixture (real captured extraction for Sam's
 * four financial statements) with one document removed at a time.
 *
 * Method note — nothing is mutated. Every ablation calls `buildHistoricalYears`
 * / `buildReviewState` FRESH over a smaller input set and the result is
 * asserted to be a different object than the full-fixture result. A test that
 * pruned a rendered tree could pass while the builder still carried the
 * deleted document's values in memory; this one cannot.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';
import type { EngineOutputsResponse } from '@/lib/api';
import {
  LIVE_DEAL_ID, LIVE_KEYS, LIVE_DOCS, LIVE_EXTRACTIONS, LIVE_BADGES,
  DOC_T12_2025, DOC_PNL_2024, DOC_PNL_2023, DOC_PNL_2019,
} from './helpers/fon41LiveFixture';
import { buildHistoricalYears } from '@/lib/hooks/useHistoricals';
import { buildReviewState, histHasData, cellKey } from '@/lib/reviewState';
import { WORKSHEET_ROWS } from '@/components/project/pl/GroundedWorksheet';

// ─────────────────────── the OM (embedded prior years) ───────────────────────
// The FON-41 capture is four P&L-family statements — Sam's deal had no OM
// extraction to capture. `buildHistoricalYears` backfills gap years from an
// OM's own reproduced operating history, keyed `p_and_l_usali.<YYYY>.<line>`
// (see `extractEmbeddedYears`), so an OM is constructed here to exercise that
// leg. Field NAMES are the real ontology paths the builder reads; the VALUES
// are synthetic placeholders (SYNTH) — the test only needs them present,
// numeric, and distinguishable per year.
const OM_YEARS = ['2021', '2022'] as const;
const omField = (year: string, line: string, value: number): ExtractionField => ({
  field_name: `p_and_l_usali.${year}.${line}`,
  value, unit: null, source_page: 3, confidence: 0.98, raw_text: null, reviewed: null,
});
const omYearFields = (year: string, bump: number): ExtractionField[] => [
  omField(year, 'occupancy_pct', 0.70 + bump / 100),
  omField(year, 'adr_usd', 250 + bump),
  omField(year, 'revpar_usd', 175 + bump),
  omField(year, 'rooms_revenue_usd', 8_000_000 + bump * 100_000),   // SYNTH
  omField(year, 'fb_revenue_usd', 2_000_000 + bump * 100_000),      // SYNTH
  omField(year, 'gop_usd', 3_000_000 + bump * 100_000),             // SYNTH
  omField(year, 'noi_usd', 2_000_000 + bump * 100_000),             // SYNTH
  omField(year, 'total_undistributed_expense_usd', 2_500_000),      // SYNTH
];
const DOC_OM: WorkerDocument = {
  ...DOC_PNL_2019,
  id: '5c1d9f2e-77aa-4c31-9a0e-0f3b1d6e4a10',
  filename: 'Anglers_OM.pdf',
  doc_type: 'OM',
  fiscal_year: null,
  extracted_period_year: null,
  uploaded_at: '2026-09-08T20:28:10.000000Z',
};
const EX_OM: ExtractionResult = {
  document_id: DOC_OM.id,
  status: 'EXTRACTED',
  fields: [...omYearFields('2021', 1), ...omYearFields('2022', 2)],
  confidence_report: { overall: 0.98, by_field: {}, low_confidence_fields: [], requires_human_review: false },
  agent_version: null,
  created_at: null,
};

const DOCS_WITH_OM: WorkerDocument[] = [...LIVE_DOCS, DOC_OM];
const EX_WITH_OM: Record<string, ExtractionResult> = { ...LIVE_EXTRACTIONS, [DOC_OM.id]: EX_OM };

// ───────────────────────────── pure helpers ─────────────────────────────────

/** Drop a document (and its extraction) — a fresh, smaller input set. */
const without = (
  docs: WorkerDocument[],
  extractions: Record<string, ExtractionResult | undefined>,
  docId: string,
): { docs: WorkerDocument[]; extractions: Record<string, ExtractionResult | undefined> } => {
  const next = { ...extractions };
  delete next[docId];
  return { docs: docs.filter((d) => d.id !== docId), extractions: next };
};

/** The exact pair both surfaces read: populated columns + the review state. */
const surfaces = (
  docs: WorkerDocument[],
  extractions: Record<string, ExtractionResult | undefined>,
) => {
  const years = buildHistoricalYears(docs, extractions, LIVE_KEYS).filter(histHasData);
  return { years, review: buildReviewState(docs, extractions, WORKSHEET_ROWS, years) };
};

// ══════════════════ 1. drop the T-12 — its column and cells go ═══════════════

describe('evidence ablation — removing the T-12 statement', () => {
  it('is grounded before the ablation (positive control)', () => {
    const { years, review } = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    expect(years.map((y) => y.docId)).toContain(DOC_T12_2025.id);
    expect(years.find((y) => y.docId === DOC_T12_2025.id)?.year).toBe('T-12');
    expect(review.byDoc.get(DOC_T12_2025.id)).toBe(LIVE_BADGES.T12_2025);
    // 2023 also carries flagged cells — the count is left unpinned on purpose:
    // LIVE_BADGES.PNL_2023 is the number QA *observed* against the full live
    // extraction, while the fixture carries a sampled subset of those fields,
    // so the two legitimately differ. The ablation assertions below compare
    // against the computed baseline instead.
    expect(review.byDoc.get(DOC_PNL_2023.id) ?? 0).toBeGreaterThan(0);
  });

  it('renders no T-12 column, and the padded T-12 header carries no data', () => {
    const gone = without(LIVE_DOCS, LIVE_EXTRACTIONS, DOC_T12_2025.id);
    const { years } = surfaces(gone.docs, gone.extractions);

    expect(years.map((y) => y.docId)).not.toContain(DOC_T12_2025.id);
    expect(years.map((y) => y.year)).not.toContain('T-12');
    // The unfiltered builder still emits a T-12 *header* so the grid keeps its
    // shape — it must be an empty placeholder, never a number.
    const unfiltered = buildHistoricalYears(gone.docs, gone.extractions, LIVE_KEYS);
    const padded = unfiltered.find((y) => y.year === 'T-12');
    expect(padded?.populated).toBe(false);
    expect(padded?.docId).toBeUndefined();
    expect([padded?.rooms, padded?.fb, padded?.gop, padded?.noi]).toEqual([0, 0, null, null]);
  });

  it('drops the T-12 from the Data Room byDoc map and from every flagged cell', () => {
    const before = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    const gone = without(LIVE_DOCS, LIVE_EXTRACTIONS, DOC_T12_2025.id);
    const after = surfaces(gone.docs, gone.extractions);

    expect(after.review.byDoc.has(DOC_T12_2025.id)).toBe(false);
    expect(after.review.total).toBe(before.review.total - LIVE_BADGES.T12_2025);
    for (const cell of after.review.byCell.values()) {
      expect(cell.docId).not.toBe(DOC_T12_2025.id);
      expect(cell.year).not.toBe('T-12');
    }
    // The cells the T-12 previously flagged are gone by key, not just by count.
    const t12Keys = [...before.review.byCell.entries()]
      .filter(([, c]) => c.docId === DOC_T12_2025.id)
      .map(([k]) => k);
    expect(t12Keys.length).toBe(LIVE_BADGES.T12_2025);
    for (const k of t12Keys) expect(after.review.byCell.has(k)).toBe(false);
  });

  it('leaves every cell that still renders for another document unchanged', () => {
    const before = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    const gone = without(LIVE_DOCS, LIVE_EXTRACTIONS, DOC_T12_2025.id);
    const after = surfaces(gone.docs, gone.extractions);

    for (const survivor of [DOC_PNL_2024, DOC_PNL_2023, DOC_PNL_2019]) {
      expect(after.review.byDoc.get(survivor.id) ?? 0).toBe(
        before.review.byDoc.get(survivor.id) ?? 0,
      );
      const col = after.years.find((y) => y.docId === survivor.id);
      const was = before.years.find((y) => y.docId === survivor.id);
      expect(col).toEqual(was);
    }
    // Cell-level: identical entries, same keys, same confidences.
    const survivors = new Set([DOC_PNL_2024.id, DOC_PNL_2023.id, DOC_PNL_2019.id]);
    const pick = (s: ReturnType<typeof surfaces>) =>
      [...s.review.byCell.entries()]
        .filter(([, c]) => survivors.has(c.docId))
        .sort(([a], [b]) => a.localeCompare(b));
    expect(pick(after)).toEqual(pick(before));
  });

  it('is a fresh build, not a pruned one (reference inequality)', () => {
    const before = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    const gone = without(LIVE_DOCS, LIVE_EXTRACTIONS, DOC_T12_2025.id);
    const after = surfaces(gone.docs, gone.extractions);

    expect(after.years).not.toBe(before.years);
    expect(after.review).not.toBe(before.review);
    expect(after.review.byCell).not.toBe(before.review.byCell);
    expect(after.review.byDoc).not.toBe(before.review.byDoc);
    // …and the untouched fixture is genuinely untouched: rebuilding from the
    // FULL input still produces the T-12 column and its nine flagged cells.
    const rebuilt = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    expect(rebuilt.review.byDoc.get(DOC_T12_2025.id)).toBe(LIVE_BADGES.T12_2025);
    expect(LIVE_DOCS).toHaveLength(4);
    expect(Object.keys(LIVE_EXTRACTIONS)).toHaveLength(4);
  });
});

// ═══════════ 2. drop the OM — its embedded prior-year columns go ═════════════

describe('evidence ablation — removing the OM', () => {
  it('backfills 2021 + 2022 from the OM before the ablation (positive control)', () => {
    const { years } = surfaces(DOCS_WITH_OM, EX_WITH_OM);
    const labels = years.map((y) => y.year);
    for (const y of OM_YEARS) expect(labels).toContain(y);
    // OM-embedded columns carry no docId — they are not a standalone statement.
    for (const y of OM_YEARS) {
      const col = years.find((c) => c.year === y);
      expect(col?.populated).toBe(true);
      expect(col?.docId).toBeUndefined();
      expect(col?.noi).toBeGreaterThan(0);
    }
  });

  it('makes the embedded prior-year columns vanish', () => {
    const gone = without(DOCS_WITH_OM, EX_WITH_OM, DOC_OM.id);
    const { years } = surfaces(gone.docs, gone.extractions);
    const labels = years.map((y) => y.year);
    for (const y of OM_YEARS) {
      expect(labels).not.toContain(y);
      // The skeleton may still pad the header; it must hold no numbers.
      const padded = buildHistoricalYears(gone.docs, gone.extractions, LIVE_KEYS)
        .find((c) => c.year === y);
      if (padded) {
        expect(padded.populated).toBe(false);
        expect([padded.rooms, padded.fb, padded.noi]).toEqual([0, 0, null]);
      }
    }
  });

  it('leaves the four real statements exactly as they were', () => {
    const withOm = surfaces(DOCS_WITH_OM, EX_WITH_OM);
    const gone = without(DOCS_WITH_OM, EX_WITH_OM, DOC_OM.id);
    const after = surfaces(gone.docs, gone.extractions);

    for (const doc of LIVE_DOCS) {
      expect(after.years.find((y) => y.docId === doc.id))
        .toEqual(withOm.years.find((y) => y.docId === doc.id));
      expect(after.review.byDoc.get(doc.id) ?? 0).toBe(withOm.review.byDoc.get(doc.id) ?? 0);
    }
    // An OM never contributes review cells (no per-cell meta on embedded years),
    // so the badge total is identical either way.
    expect(after.review.total).toBe(withOm.review.total);
    expect(after.years).not.toBe(withOm.years);
  });

  it('never carries an OM-embedded cell into the review state', () => {
    const { review } = surfaces(DOCS_WITH_OM, EX_WITH_OM);
    expect(review.byDoc.has(DOC_OM.id)).toBe(false);
    for (const cell of review.byCell.values()) {
      expect(cell.docId).not.toBe(DOC_OM.id);
      expect(OM_YEARS as readonly string[]).not.toContain(cell.year);
      expect(cellKey(cell.rowId, cell.year)).toBeTruthy();
    }
  });
});

// ══════════════════ 3. the rendered grid agrees with the builder ═════════════

// The api mock serves whatever `SERVED` holds when the component fetches, so a
// single mock factory can render the full deal and the ablated deal.
const SERVED = vi.hoisted(() => ({
  docs: [] as unknown[],
  extractions: {} as Record<string, unknown>,
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/api', async () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      list: async () => SERVED.docs,
      extraction: async (_deal: string, docId: string) => {
        const r = SERVED.extractions[docId];
        if (!r) throw new Error(`no extraction for ${docId}`);
        return r;
      },
      reviewField: vi.fn(async () => ({})),
    },
    deals: { update: vi.fn(async () => ({})), get: vi.fn(), status: vi.fn() },
  },
}));

const OUTPUTS = {
  deal_id: LIVE_DEAL_ID,
  engines: {
    expense: {
      deal_id: LIVE_DEAL_ID, engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
  },
} as unknown as EngineOutputsResponse;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777', keys: 132, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const RED_CELL = /^Low confidence \(\d+%\) — click to review its source$/;

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
});
afterEach(cleanup);

describe('evidence ablation — the rendered worksheet', () => {
  it('renders a T-12 pill and its nine red cells while the statement is on the deal', async () => {
    SERVED.docs = LIVE_DOCS;
    SERVED.extractions = LIVE_EXTRACTIONS as Record<string, unknown>;
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(
      // FON-41 #4 — the pill states the period, not a bare "T-12".
      () => expect(screen.getByRole('button', { name: 'T12 Mar 2025' })).toBeInTheDocument(),
      { timeout: 8000 },
    );
    const { review } = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    await waitFor(
      () => expect(screen.queryAllByRole('button', { name: RED_CELL })).toHaveLength(review.total),
      { timeout: 8000 },
    );
  }, 15000);

  it('renders no T-12 pill once the statement is removed, and the other pills survive', async () => {
    const gone = without(LIVE_DOCS, LIVE_EXTRACTIONS, DOC_T12_2025.id);
    SERVED.docs = gone.docs;
    SERVED.extractions = gone.extractions as Record<string, unknown>;
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);

    await waitFor(
      () => expect(screen.getByRole('button', { name: 'FY2023' })).toBeInTheDocument(),
      { timeout: 8000 },
    );
    expect(screen.queryByRole('button', { name: 'T12 Mar 2025' })).toBeNull();
    expect(screen.getByRole('button', { name: 'FY2019' })).toBeInTheDocument();

    // The red cells that remain are exactly the survivors' — the T-12's nine
    // are gone, and nothing else moved.
    const after = surfaces(gone.docs, gone.extractions);
    const before = surfaces(LIVE_DOCS, LIVE_EXTRACTIONS);
    expect(after.review.total).toBe(before.review.total - LIVE_BADGES.T12_2025);
    await waitFor(
      () => expect(screen.queryAllByRole('button', { name: RED_CELL })).toHaveLength(after.review.total),
      { timeout: 8000 },
    );
  }, 15000);
});
