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
 *
 * The visual is the canonical blue edit frame from `FieldValue.tsx`
 * ("Editing" specimen) — `InlineEditControls` renders the Save · Cancel pair
 * so every tab shows the same affordance in the same order.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useToast } from '@/components/ui/Toast';
import { isNoOpEdit, type FieldUnit } from '@/lib/fieldValue';
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
  /** Persist the changed value (the PATCH). Only ever called for a real change. */
  onSave: (value: T) => void | Promise<void>;
  /** Persisted value → the draft string the editor opens with. */
  toDraft?: (current: T) => string;
  /** Message for an unparseable entry. */
  invalidMessage?: string;
  /** Notified when Save was a no-op (after edit mode has closed). */
  onNoOp?: () => void;
}

export interface InlineEditApi<T extends number | string> {
  editing: boolean;
  draft: string;
  setDraft: (v: string) => void;
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
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
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
      setEditing(true);
    },
    [openDraft],
  );

  const cancel = useCallback(() => {
    // Restore the pre-edit display and leave. Nothing is written.
    setDraft(openDraft());
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
      toast(NO_OP_EDIT_MESSAGE, { type: 'info' });
      onNoOp?.();
      return;
    }
    setSaving(true);
    try {
      await onSave(parsed);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }, [draft, parse, current, unit, onSave, toast, invalidMessage, onNoOp]);

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

  return { editing, draft, setDraft, start, cancel, submit, saving, containerRef, onKeyDown };
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
}

/**
 * The canonical Save · Cancel pair. Same order, same treatment, every tab —
 * navy Save, outlined Cancel, both labelled for the screen reader.
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
}: InlineEditControlsProps) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, ...(block ? { width: '100%' } : null), ...style }}>
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
