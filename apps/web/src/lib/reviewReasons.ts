/**
 * reviewReasons — the data-derived "why is this document flagged" line the
 * Data Room shows under "Review Recommended", and the per-field explanation
 * the inline field review shows on each flagged row.
 *
 * FON-41 external testers (E-002 / R-041 / R-042 / R-043 / R-045): a document
 * read "Review Recommended" next to 97% overall confidence and never said
 * WHY; a CoStar chart read at exactly 50% showed no source text; pipeline
 * counts rendered as a bare number with the stage lost in the field path.
 *
 * Every word produced here derives from field data — `field_name`,
 * `confidence`, `source_page`, `raw_text` — or from the shared review state.
 * Nothing is synthesized: a field with no raw_text says "no source text
 * captured", never a guessed cause.
 *
 * ONE rule decides "Review Recommended", the reason line AND the field view's
 * flagged set — `flaggedFieldsForDoc` — so the three can never disagree:
 *   • financial statements (T-12 / P&L family): the flagged cells of that
 *     document's own column in Financials → Historicals (lib/reviewState,
 *     the FON-41 shared predicate), in worksheet row order;
 *   • every other document type: its own extracted fields below the 85%
 *     review threshold that no analyst has accepted / edited yet — the
 *     coverage card's long-standing non-financial count, verbatim.
 * Both are strictly per-document: another upload can never add a field to
 * this document's list.
 */

import type { ExtractionField } from '@/lib/api';
import type { ReviewState } from '@/lib/reviewState';
import { humanizeFieldName } from '@/lib/fieldLabels';

/** One flagged extracted value, carrying exactly what the UI may show. */
export interface FlaggedField {
  /** Exact extracted field_name — the field review keys its rows on it. */
  field: string;
  /** Raw 0..1 confidence (NOT rounded — the chart/table tag needs exactly 0.5). */
  confidence: number;
  sourcePage: number | null;
  rawText: string | null;
}

/** Below this rounded percentage a non-financial field is "to review". */
export const REVIEW_THRESHOLD_PCT = 85;

/**
 * The non-financial Data Room predicate, verbatim from the coverage card's
 * `toReview` count: rounded confidence < 85% and not yet accepted / edited.
 */
export function isFlaggedField(f: Pick<ExtractionField, 'confidence' | 'reviewed'>): boolean {
  return !(Math.round((f.confidence ?? 0) * 100) >= REVIEW_THRESHOLD_PCT || !!f.reviewed);
}

/**
 * The single flagged list for a document (see module doc). `financial`
 * selects the shared worksheet review state; otherwise the document's own
 * fields are filtered with `isFlaggedField`.
 */
export function flaggedFieldsForDoc(args: {
  docId: string;
  financial: boolean;
  fields: ReadonlyArray<ExtractionField>;
  reviewState: Pick<ReviewState, 'byCell'>;
}): FlaggedField[] {
  const { docId, financial, fields, reviewState } = args;
  const byName = new Map<string, ExtractionField>();
  for (const f of fields) if (!byName.has(f.field_name)) byName.set(f.field_name, f);

  const toFlagged = (name: string, confidence: number): FlaggedField => {
    const f = byName.get(name);
    return {
      field: name,
      confidence,
      sourcePage: f?.source_page ?? null,
      rawText: f?.raw_text ?? null,
    };
  };

  if (financial) {
    // byCell is filled year-column × worksheet-row, so the cells of one
    // document's column come out in worksheet row order — the order the
    // Historicals grid lands on them. One entry per flagged cell, so the
    // length is exactly reviewState.byDoc.get(docId).
    const out: FlaggedField[] = [];
    for (const cell of reviewState.byCell.values()) {
      if (cell.docId !== docId) continue;
      out.push(toFlagged(cell.field, cell.confidence));
    }
    return out;
  }
  return fields.filter(isFlaggedField).map((f) => toFlagged(f.field_name, f.confidence ?? 0));
}

// ── Labels ────────────────────────────────────────────────────────────────

// R-045: pipeline counts are keyed by stage in the field PATH
// (`…supply_pipeline.under_construction.rooms` vs `…final_planning.rooms`)
// but the leaf alone reads "Rooms". Parent segments matching one of these
// stage tokens are carried into the label.
const PIPELINE_STAGES: Record<string, string> = {
  under_construction: 'Under construction',
  in_construction: 'In construction',
  final_planning: 'Final planning',
  pre_planning: 'Pre-planning',
  planning: 'Planning',
  proposed: 'Proposed',
  deferred: 'Deferred',
  abandoned: 'Abandoned',
  unconfirmed: 'Unconfirmed',
};

// Leaves that are just "how many" — the stage IS the label, the leaf only
// says what is being counted ("Under construction rooms").
const GENERIC_COUNT_LEAVES = new Set([
  'rooms', 'room_count', 'count', 'total', 'hotels', 'hotel_count',
  'projects', 'project_count', 'properties', 'number', 'n', 'units', 'keys',
]);

/** The pipeline stage encoded in a field path's PARENT segments, if any. */
export function pipelineStageOf(path: string): string | null {
  const segs = path.split('.').filter(Boolean);
  for (let i = segs.length - 2; i >= 0; i--) {
    const hit = PIPELINE_STAGES[segs[i].toLowerCase()];
    if (hit) return hit;
  }
  return null;
}

/**
 * Analyst-facing label for a flagged field: the app's shared humanizer
 * (last segment, underscores → spaces, ADR / RevPAR / NOI / F&B / GOP kept),
 * plus the pipeline stage when the path encodes one (R-045).
 */
export function humanizeReviewField(path: string): string {
  const base = humanizeFieldName(path);
  const stage = pipelineStageOf(path);
  if (!stage) return base;
  const leaf = (path.split('.').pop() ?? '').toLowerCase();
  return GENERIC_COUNT_LEAVES.has(leaf) ? `${stage} ${base.toLowerCase()}` : `${stage} · ${base}`;
}

// ── Reason line (document row) ────────────────────────────────────────────

export interface ReviewReason {
  /** Number of flagged fields — equals the row's "N to review". */
  count: number;
  /** One line: `3 fields at 50% · ADR 2021 (p.8), RevPAR 2020 Trough (p.9), +1 more` */
  text: string;
  fields: FlaggedField[];
}

/** Names shown before "+N more". */
export const REASON_MAX_NAMES = 3;

export const pct = (confidence: number): number => Math.round(confidence * 100);

/** `null` when nothing is flagged — the row then shows no reason at all. */
export function buildReviewReason(
  flagged: ReadonlyArray<FlaggedField>,
  maxNames: number = REASON_MAX_NAMES,
): ReviewReason | null {
  const n = flagged.length;
  if (n === 0) return null;
  const pcts = flagged.map((f) => pct(f.confidence));
  const lo = Math.min(...pcts);
  const hi = Math.max(...pcts);
  const band = lo === hi ? `${lo}%` : `${lo}–${hi}%`;
  const names = flagged.slice(0, maxNames).map((f) => {
    const page = f.sourcePage != null && f.sourcePage > 0 ? ` (p.${f.sourcePage})` : '';
    return `${humanizeReviewField(f.field)}${page}`;
  });
  const more = n > maxNames ? `, +${n - maxNames} more` : '';
  return {
    count: n,
    text: `${n} field${n === 1 ? '' : 's'} at ${band} · ${names.join(', ')}${more}`,
    fields: [...flagged],
  };
}

// ── Per-field explanation (field review row) ──────────────────────────────

/**
 * Exactly 0.5 is what the extractor reports for values read off charts and
 * multi-column table rows (R-043: "2021 | $230 | -10%"). The tag is only
 * earned when the raw text is there to prove the read.
 */
export const CHART_TABLE_READ_CONFIDENCE = 0.5;
export const CHART_TABLE_READ_TAG = 'chart / table read';
export const NO_SOURCE_TEXT = 'no source text captured';

export function isChartTableRead(
  confidence: number | null | undefined,
  rawText: string | null | undefined,
): boolean {
  return (
    confidence === CHART_TABLE_READ_CONFIDENCE &&
    typeof rawText === 'string' &&
    rawText.trim().length > 0
  );
}

/** "PDF p.8" for a PDF source; "p.8" for a spreadsheet / other (sheet index). */
export function sourcePageLabel(
  page: number | null | undefined,
  docName?: string | null,
): string | null {
  if (page == null || !(page > 0)) return null;
  const isPdf = /\.pdf$/i.test((docName ?? '').trim());
  return isPdf ? `PDF p.${page}` : `p.${page}`;
}

/** `62% confidence · PDF p.3` — the data-only head of a flagged row's explanation. */
export function fieldExplanation(
  confidence: number | null | undefined,
  page: number | null | undefined,
  docName?: string | null,
): string {
  const where = sourcePageLabel(page, docName);
  return `${pct(confidence ?? 0)}% confidence${where ? ` · ${where}` : ''}`;
}
