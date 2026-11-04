'use client';

/**
 * The acquisition close date — ONE editor and ONE save path.
 *
 * The CAPEX tab's Acquisition Date row and the Future P&L's "Set the
 * acquisition close date to place the market forecast" state (topline review
 * R2-I1, `calendar_unknown`) both render this component and both save through
 * `closeDatePatch` + the deal PATCH. The close date is an engine input (the
 * projection calendar and the topline's forecast placement read it), so a
 * change carries the analyst's note and re-runs the model.
 */

import { InlineEditControls, inlineEditInputStyle, palette, prov, useInlineEdit } from '@/components/design';
import { TAB_LABEL } from '@/lib/projectTabs';
import { applyOverridePatch, NOTE_REQUIRED_MESSAGE, patchRequiresNote, requiresNote } from '@/lib/overrideNote';

export const CLOSE_DATE_KEY = 'acquisition_close_date';

/**
 * The `field_overrides` blob a close-date save PATCHes — the shared half of
 * the save path. Throws `NOTE_REQUIRED_MESSAGE` without a note.
 */
export function closeDatePatch(
  overrides: Record<string, unknown>,
  iso: string,
  note: string,
): Record<string, unknown> {
  const patch = { [CLOSE_DATE_KEY]: iso };
  if (!note.trim() && patchRequiresNote(patch)) throw new Error(NOTE_REQUIRED_MESSAGE);
  return applyOverridePatch(overrides, patch, note);
}

/** Format an ISO date (YYYY-MM-DD) as M/D/YYYY without a timezone shift. */
function fmtISODate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';
  return `${Number(m[2])}/${Number(m[3])}/${m[1]}`;
}

/** The acquisition close date — an editable date cell. */
export default function CloseDateField({
  iso, editable, onSave, noteKey = CLOSE_DATE_KEY, emptyLabel = 'Set date', testId,
  ariaLabel = 'Acquisition close date', hint = `Click to change — ${TAB_LABEL.investment} owns this assumption`,
}: {
  iso: string | null;
  editable: boolean;
  onSave: (iso: string, note: string) => void | Promise<void>;
  /** FON-74 — the `field_overrides` key this editor writes. */
  noteKey?: string;
  emptyLabel?: string;
  testId?: string;
  ariaLabel?: string;
  hint?: string;
}) {
  const current = iso ? iso.slice(0, 10) : null;
  const ed = useInlineEdit<string>({
    current,
    unit: 'date',
    parse: (s) => (/^\d{4}-\d{2}-\d{2}$/.test(s) ? s : null),
    onSave,
    toDraft: (v) => v,
    invalidMessage: 'Pick a valid date.',
    requireNote: !!noteKey && requiresNote(noteKey),
  });
  const display = fmtISODate(iso);

  if (!editable) {
    return <span style={{ color: prov.blue, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!ed.editing) {
    return (
      <span
        data-testid={testId}
        role="button"
        tabIndex={0}
        style={{ color: prov.blue, fontWeight: 500, fontVariantNumeric: 'tabular-nums', textDecoration: 'underline dotted', cursor: 'pointer' }}
        onClick={() => ed.start()}
        onKeyDown={(e) => { if (e.key === 'Enter') ed.start(); }}
        title={hint}>
        {iso ? display : <span style={{ color: palette.textFaint, fontWeight: 400 }}>{emptyLabel}</span>}
      </span>
    );
  }
  return (
    <span ref={ed.containerRef} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <input
        type="date" value={ed.draft} autoFocus disabled={ed.saving}
        aria-label={ariaLabel}
        onChange={(e) => ed.setDraft(e.target.value)}
        onKeyDown={ed.onKeyDown}
        style={{ ...inlineEditInputStyle, textAlign: 'left' }}
      />
      <InlineEditControls
        onSave={() => void ed.submit()}
        onCancel={ed.cancel}
        saving={ed.saving}
        note={ed.requireNote ? ed.note : undefined}
        onNote={ed.requireNote ? ed.setNote : undefined}
        noteTestId={ed.requireNote ? 'acq-date-note' : undefined}
      />
    </span>
  );
}
