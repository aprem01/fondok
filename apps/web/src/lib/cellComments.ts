/**
 * FON-41 E-011 — stable keys for P&L cell comments.
 *
 * A comment is pinned to WHAT a cell is, never where it sits on screen:
 *
 *   • Historical P&L → `hist:<document_id>::<field_name>` — the extracted line
 *     the cell shows. It is the same id the historicals Excel round-trip
 *     carries (`document_id::field_name`), so a comment and an import name the
 *     same cell. Reordering / hiding columns or rows never orphans it.
 *   • Future P&L → `proj:<engine>.years[<i>].<path>` — the engine output path
 *     and model-year index, the same root the lineage drawer opens.
 *
 * The worker validates the shape (`app/api/cell_comments.py::CELL_KEY_RE`).
 */
import type { CellComment } from '@/lib/api';

export const histCommentKey = (documentId: string, fieldName: string): string =>
  `hist:${documentId}::${fieldName}`;

/** `path` is the engine path INCLUDING the year segment, e.g. `years[2].adr`. */
export const projCommentKey = (engine: string, path: string): string => `proj:${engine}.${path}`;

export interface CommentThread {
  cellKey: string;
  label: string | null;
  comments: CellComment[];
  /** Comments not yet resolved. A thread with 0 open is "resolved". */
  open: number;
  lastAt: string;
}

/** Group a deal's flat comment list into per-cell threads (oldest comment first). */
export function groupThreads(comments: readonly CellComment[]): Map<string, CommentThread> {
  const out = new Map<string, CommentThread>();
  const sorted = [...comments].sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const c of sorted) {
    const t = out.get(c.cell_key) ?? { cellKey: c.cell_key, label: null, comments: [], open: 0, lastAt: c.created_at };
    t.comments.push(c);
    if (!c.resolved_at) t.open += 1;
    if (c.cell_label) t.label = c.cell_label;
    t.lastAt = c.created_at;
    out.set(c.cell_key, t);
  }
  return out;
}
