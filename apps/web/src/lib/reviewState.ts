/**
 * reviewState — ONE canonical "needs review" state for extracted financial
 * values, shared by the Data Room (per-document badge + global count) and the
 * Financials → Historicals worksheet (red cells + SOURCE panel).
 *
 * FON-41 (Sam): the Data Room said "6 to review" on the 2023 P&L, the worksheet
 * filtered to 2023 showed nothing flagged, and the SOURCE panel named the 2019
 * P&L while the correction targeted 2023. Three separate predicates disagreed.
 * This module is the single predicate they now share:
 *
 *   a cell (row × year-column) is flagged  ⇔
 *     the column's HistYear carries `meta[row.metaKey]` for that line
 *     AND that meta pins a document that is still part of the deal
 *     AND the cell renders a value (histValue ≠ null)
 *     AND the LIVE extracted field (or, until it loads, the captured meta)
 *         has confidence < REVIEW_THRESHOLD and has not been accepted/edited.
 *
 * The document is pinned per COLUMN (from HistYear.meta), never resolved by
 * scanning all documents for the first matching field — so a 2023 cell can
 * only ever name and act on the 2023 statement.
 *
 * Read side only: no money math lives here. The worker has no /historicals
 * route yet, so the state is built client-side from the same documents +
 * extractions both surfaces already load.
 */

import type { ExtractionResult, WorkerDocument } from '@/lib/api';
import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { HIST_KEY_BY_ROW } from '@/lib/ontology/adapters';

/** Below this extraction confidence a value is "to review" until accepted/edited. */
export const REVIEW_THRESHOLD = 0.85;

/** The subset of a worksheet row the review state needs. */
export interface ReviewRow {
  id: string;
  /** HistYear.meta key for this line (absent → the row can never flag). */
  metaKey?: string;
}

/** One flagged cell — pinned to the column's own document. */
export interface ReviewCell {
  rowId: string;
  /** Column label (HistYear.year, unique per column — see uniqueYearLabel). */
  year: string;
  docId: string;
  /** Exact extracted field_name behind the cell. */
  field: string;
  confidence: number;
}

export interface ReviewState {
  /** docId → number of flagged cells in that document's column. */
  byDoc: Map<string, number>;
  /** cellKey(rowId, year) → flagged cell. */
  byCell: Map<string, ReviewCell>;
  /** Σ byDoc — the global "values need your review" count. */
  total: number;
}

export const cellKey = (rowId: string, year: string): string => `${rowId}|${year}`;

/** The single predicate: low confidence and not yet accepted / edited. */
export function needsReview(field: {
  confidence: number | null | undefined;
  reviewed?: string | null;
}): boolean {
  return (
    typeof field.confidence === 'number' &&
    field.confidence < REVIEW_THRESHOLD &&
    !field.reviewed
  );
}

const nOrNull = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;

/**
 * Historical value for a worksheet row from one HistYear (see useHistoricals).
 * Detail rows the source P&Ls don't break out (A&G, FF&E, …) return null and
 * render "—". Lives here (not in the worksheet) because "does this cell render
 * a value" is part of the review predicate — a value that has no cell has no
 * place to be reviewed, so it must not be counted anywhere.
 *
 * Phase 1.4: the row → HistYear-field map is the generated registry's
 * ``bindings.worksheet.hist_key`` (``HIST_KEY_BY_ROW``), not a hand-written
 * switch. Fixed-charge parts (Part B — mgmt fee / property tax / insurance)
 * are extracted individually and are optional on HistYear, so older payloads
 * still render. ``total_rev`` is the ONE row whose historical value is a SUM
 * of other fields rather than a single extracted line; it carries no
 * ``hist_key`` in the registry and stays computed here.
 */
export function histValue(rowId: string, h: HistYear): number | null {
  if (rowId === 'total_rev') {
    const parts = [h.rooms, h.fb, h.misc].map(nOrNull).filter((x): x is number => x != null);
    return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
  }
  const key = HIST_KEY_BY_ROW[rowId];
  if (!key) return null;
  return nOrNull((h as unknown as Record<string, unknown>)[key]);
}

/**
 * A year earns a worksheet column only if it carries REAL data — skeleton
 * placeholders (populated:false) and all-zero years are dropped so the grid
 * isn't padded with empty $0 columns. Shared with the Data Room so it counts
 * against exactly the columns the worksheet renders.
 */
export const histHasData = (h: HistYear): boolean =>
  h.populated !== false &&
  [h.rooms, h.fb, h.gop, h.noi].some((x) => {
    const n = nOrNull(x);
    return n != null && n !== 0;
  });

/**
 * Build the canonical review state.
 *
 * @param docs        the deal's documents (a column pinned to a document that
 *                    is no longer in the deal is orphaned and never counted)
 * @param extractions live per-doc extraction results (useDocuments) — the
 *                    source of truth for `confidence` / `reviewed` after an
 *                    Accept / Edit, so both surfaces decrement immediately
 * @param rows        worksheet rows (id + metaKey) — the cells that exist
 * @param histYears   the rendered historical columns (useHistoricals), each
 *                    carrying per-line meta { field, confidence, docId }
 */
export function buildReviewState(
  docs: WorkerDocument[],
  extractions: Record<string, ExtractionResult | undefined>,
  rows: ReadonlyArray<ReviewRow>,
  histYears: ReadonlyArray<HistYear>,
): ReviewState {
  const byDoc = new Map<string, number>();
  const byCell = new Map<string, ReviewCell>();
  const docIds = new Set(docs.map((d) => d.id));

  for (const year of histYears) {
    if (!year.meta) continue;
    for (const row of rows) {
      if (!row.metaKey) continue;
      const meta = year.meta[row.metaKey];
      if (!meta?.docId || !docIds.has(meta.docId)) continue;
      if (histValue(row.id, year) == null) continue;

      // Live field wins (post-accept it carries reviewed + confidence 1.0);
      // fall back to the meta captured at column-build time until it loads.
      const live = extractions[meta.docId]?.fields?.find((f) => f.field_name === meta.field);
      const confidence = live ? live.confidence : meta.confidence;
      const reviewed = live?.reviewed ?? null;
      if (!needsReview({ confidence, reviewed })) continue;

      byCell.set(cellKey(row.id, year.year), {
        rowId: row.id,
        year: year.year,
        docId: meta.docId,
        field: meta.field,
        confidence: confidence as number,
      });
      byDoc.set(meta.docId, (byDoc.get(meta.docId) ?? 0) + 1);
    }
  }

  let total = 0;
  for (const n of byDoc.values()) total += n;
  return { byDoc, byCell, total };
}

/** Flagged cells of one column, in row order — the worksheet's scroll target. */
export function cellsForYear(state: ReviewState, rows: ReadonlyArray<ReviewRow>, year: string): ReviewCell[] {
  const out: ReviewCell[] = [];
  for (const row of rows) {
    const c = state.byCell.get(cellKey(row.id, year));
    if (c) out.push(c);
  }
  return out;
}
