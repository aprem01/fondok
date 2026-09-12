'use client';

/**
 * InlineEdit — the ONE inline-editing primitive (FON-63, FON-66 §1, FON-65).
 *
 * Before this, every tab hand-rolled its own `useState` pair for an inline
 * editor. Sam found the consequences on 2026-09-11: *"there is no Cancel or
 * other way to exit without saving"* and *"clicking Save still causes Fondok to
 * treat the value as an analyst Override, even though the value itself was
 * unchanged."*
 *
 * So the edit contract lives in exactly one place:
 *
 *   • `cancel()`   — restores the pre-edit draft and exits. NO network call.
 *   • `Esc`        — routes to `cancel()`.
 *   • click-outside — routes to `cancel()` (a `pointerdown` listener on the
 *                     editor's own ref; attach `containerRef`).
 *   • `submit()`   — asks `isNoOpEdit` FIRST. On a no-op it exits edit mode,
 *                    says so, and makes no request, so nothing is written and
 *                    the value keeps reporting the source it came from.
 *                    THEN, and only then, asks for the justification (FON-74):
 *                    opening a field to inspect it must never demand one.
 *
 * The visual is the canonical blue edit frame from `FieldValue.tsx`
 * ("Editing" specimen) — `InlineEditControls` renders the Save · Cancel pair
 * so every tab shows the same affordance in the same order, with the FON-74
 * note row directly above it when the key needs a justification.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useToast } from '@/components/ui/Toast';
import { isNoOpEdit, type FieldUnit } from '@/lib/fieldValue';
import { NOTE_PLACEHOLDER, NOTE_REQUIRED_MESSAGE } from '@/lib/overrideNote';
import { palette, radius, field } from './tokens';

/** What the analyst is told when Save changed nothing. */
export const NO_OP_EDIT_MESSAGE = 'No change — provenance unchanged';

export interface UseInlineEditOptions<T extends number | string> {
  /** The current EFFECTIVE value, in persisted units (what Save is compared to). */
  current: T | null | undefined;
  /** How that value is persisted — drives the comparison precision. */
  unit: FieldUnit;
  /** Draft string → persisted value. `null` means "not a valid entry". */
  parse: (draft: string) => T | null;
  /**
   * Persist the changed value (the PATCH). Only ever called for a real change,
   * and — when `requireNote` — only with a non-empty `note`.
   */
  onSave: (value: T, note: string) => void | Promise<void>;
  /** Persisted value → the draft string the editor opens with. */
  toDraft?: (current: T) => string;
  /** Message for an unparseable entry. */
  invalidMessage?: string;
  /** Notified when Save was a no-op (after edit mode has closed). */
  onNoOp?: () => void;
  /**
   * FON-74 — this edit changes a number an engine runs on, so Save is refused
   * until the analyst types a justification. Resolve it with
   * `requiresNote(key)` from `@/lib/overrideNote`; never hard-code true.
   */
  requireNote?: boolean;
}

export interface InlineEditApi<T extends number | string> {
  editing: boolean;
  draft: string;
  setDraft: (v: string) => void;
  /** FON-74 — the analyst's justification. Empty until they type one. */
  note: string;
  setNote: (v: string) => void;
  /** Whether this editor demands a justification before it will save. */
  requireNote: boolean;
  /** Enter edit mode (optionally with an explicit draft). */
  start: (initial?: string) => void;
  /** Leave edit mode, restoring the draft. Never calls the network. */
  cancel: () => void;
  /** Guarded save — no-op edits exit without a request. */
  submit: () => Promise<void>;
  saving: boolean;
  /**
   * Attach to the editor's wrapper (`ref={containerRef}`) so a pointerdown
   * anywhere outside it cancels. A callback ref, so it fits any element type.
   */
  containerRef: (node: HTMLElement | null) => void;
  /** Enter → submit, Escape → cancel. */
  onKeyDown: (e: { key: string; preventDefault?: () => void }) => void;
}

/**
 * Cancel-on-click-outside. Returns a callback ref to put on the editor's
 * wrapper; while `enabled`, a pointerdown anywhere outside it calls `onCancel`.
 * Shared so every editor discards (never saves) on a click away.
 */
export function useCancelOnOutside(
  enabled: boolean,
  onCancel: () => void,
): (node: HTMLElement | null) => void {
  const nodeRef = useRef<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => {
    nodeRef.current = node;
  }, []);
  useEffect(() => {
    if (!enabled || typeof document === 'undefined') return;
    const onPointerDown = (e: Event) => {
      const node = nodeRef.current;
      const target = e.target as Node | null;
      if (!node || !target) return;
      if (node.contains(target)) return;
      onCancel();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [enabled, onCancel]);
  return ref;
}

export function useInlineEdit<T extends number | string>(
  opts: UseInlineEditOptions<T>,
): InlineEditApi<T> {
  const { current, unit, parse, onSave, toDraft, invalidMessage, onNoOp } = opts;
  const requireNote = opts.requireNote ?? false;
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const openDraft = useCallback(
    (initial?: string): string => {
      if (initial !== undefined) return initial;
      if (current === null || current === undefined) return '';
      return toDraft ? toDraft(current) : String(current);
    },
    [current, toDraft],
  );

  const start = useCallback(
    (initial?: string) => {
      setDraft(openDraft(initial));
      setNote('');
      setEditing(true);
    },
    [openDraft],
  );

  const cancel = useCallback(() => {
    // Restore the pre-edit display and leave. Nothing is written, and the
    // abandoned justification goes with the abandoned value.
    setDraft(openDraft());
    setNote('');
    setEditing(false);
  }, [openDraft]);

  const submit = useCallback(async () => {
    const parsed = parse(draft);
    if (parsed === null || parsed === undefined) {
      toast(invalidMessage ?? 'Enter a valid number.', { type: 'error' });
      return;
    }
    // FON-63 — the guard. An unchanged value is not an override.
    if (isNoOpEdit(parsed, current ?? null, unit)) {
      setEditing(false);
      setNote('');
      toast(NO_OP_EDIT_MESSAGE, { type: 'info' });
      onNoOp?.();
      return;
    }
    // FON-74 — the justification gate, deliberately AFTER the no-op guard:
    // opening a field to inspect it and saving it back unchanged must exit
    // quietly, never demand a reason for a change that isn't one.
    const trimmed = note.trim();
    if (requireNote && trimmed === '') {
      toast(NOTE_REQUIRED_MESSAGE, { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      await onSave(parsed, trimmed);
      setNote('');
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }, [draft, note, requireNote, parse, current, unit, onSave, toast, invalidMessage, onNoOp]);

  const onKeyDown = useCallback(
    (e: { key: string; preventDefault?: () => void }) => {
      if (e.key === 'Enter') {
        e.preventDefault?.();
        void submit();
      } else if (e.key === 'Escape') {
        e.preventDefault?.();
        cancel();
      }
    },
    [submit, cancel],
  );

  // Click-outside → cancel (never save). Sam: an editor you click away from
  // must discard, exactly like Esc.
  const containerRef = useCancelOnOutside(editing, cancel);

  return {
    editing, draft, setDraft, note, setNote, requireNote,
    start, cancel, submit, saving, containerRef, onKeyDown,
  };
}

export interface InlineEditControlsProps {
  onSave: () => void;
  onCancel: () => void;
  saving?: boolean;
  saveLabel?: string;
  /** Rendered inside the Save button when saving. */
  savingLabel?: string;
  saveTestId?: string;
  cancelTestId?: string;
  /** Fill the row (popover editors) instead of hugging the value. */
  block?: boolean;
  style?: CSSProperties;
  /**
   * FON-74 — render the justification row above the Save · Cancel pair.
   * Pass BOTH to turn it on; omit them on an editor that needs no note.
   */
  note?: string;
  onNote?: (v: string) => void;
  noteTestId?: string;
  /** Label for the note field (screen readers + the e2e locator). */
  noteLabel?: string;
}

/** The canonical justification input treatment. */
const noteInputStyle: CSSProperties = {
  fontSize: 11,
  fontFamily: 'inherit',
  border: `1px solid ${palette.linkBlue}`,
  borderRadius: radius.control,
  padding: '4px 7px',
  textAlign: 'left',
  outlineColor: field.input,
  width: '100%',
  minWidth: 150,
};

/**
 * The canonical Save · Cancel pair. Same order, same treatment, every tab —
 * navy Save, outlined Cancel, both labelled for the screen reader.
 *
 * FON-74: when `note` / `onNote` are given the justification field renders
 * directly above the pair, so the affordance is identical on every editor that
 * changes a number.
 */
export function InlineEditControls({
  onSave,
  onCancel,
  saving = false,
  saveLabel = 'Save',
  savingLabel,
  saveTestId,
  cancelTestId,
  block = false,
  style,
  note,
  onNote,
  noteTestId,
  noteLabel = 'Override justification',
}: InlineEditControlsProps) {
  const buttons = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, ...(block ? { width: '100%' } : null), ...(onNote ? null : style) }}>
      <button
        type="button"
        aria-label={saveLabel}
        data-testid={saveTestId}
        onClick={onSave}
        disabled={saving}
        style={{
          flex: block ? 1 : undefined,
          background: palette.inkNavy,
          color: '#fff',
          border: 'none',
          borderRadius: radius.control,
          padding: block ? 8 : '5px 9px',
          fontSize: block ? 12 : 11,
          fontWeight: 600,
          cursor: saving ? 'default' : 'pointer',
          fontFamily: 'inherit',
          opacity: saving ? 0.7 : 1,
        }}
      >
        {saving ? (savingLabel ?? 'Saving…') : saveLabel}
      </button>
      <button
        type="button"
        aria-label="Cancel"
        title="Cancel — exits without changing the value (Esc)"
        data-testid={cancelTestId}
        onClick={onCancel}
        disabled={saving}
        style={{
          background: '#fff',
          border: `1px solid ${palette.buttonSecondaryBorder}`,
          color: palette.textSecondary,
          borderRadius: radius.control,
          padding: block ? '8px 11px' : '4px 8px',
          fontSize: block ? 12 : 11,
          fontWeight: 600,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        Cancel
      </button>
    </span>
  );

  if (!onNote) return buttons;

  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'stretch', gap: 5, ...style }}>
      <input
        type="text"
        value={note ?? ''}
        aria-label={noteLabel}
        placeholder={NOTE_PLACEHOLDER}
        data-testid={noteTestId}
        disabled={saving}
        onChange={(e) => onNote(e.target.value)}
        onKeyDown={(e) => {
          // Same contract as the value field: Enter saves, Esc discards.
          if (e.key === 'Enter') { e.preventDefault(); onSave(); }
          if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
        }}
        style={noteInputStyle}
      />
      {buttons}
    </span>
  );
}

/** The canonical inline input treatment (the blue edit frame's inner field). */
export const inlineEditInputStyle: CSSProperties = {
  fontSize: 12.5,
  fontFamily: 'inherit',
  border: `1px solid ${palette.linkBlue}`,
  borderRadius: radius.control,
  padding: '4px 7px',
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
  outlineColor: field.input,
};
