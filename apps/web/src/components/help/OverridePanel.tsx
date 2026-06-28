'use client';

/**
 * OverridePanel — right-anchored slide-out drawer for value overrides.
 *
 * Replaces the centered OverrideModal (Wave 1 UX refactor, June 2026).
 * Same contract as the modal it replaces — drop-in for OverviewTab:
 *
 *   open / onClose / onSubmit({ value, note })
 *
 * The note field is REQUIRED (Eshan's exact ask: "you should have the
 * option to hard-code a number, and a note explaining why"). Submit is
 * disabled until both value AND note are populated.
 *
 * Design discipline:
 *   - 420px on desktop, full-bleed on mobile (< 640px)
 *   - Slides in from the right (`translate-x-full` → `translate-x-0`) 200ms
 *   - Semi-transparent backdrop dims the app but doesn't block scroll
 *   - Sticky header (field name + close) and sticky footer (Cancel / Save)
 *   - ESC closes, click backdrop closes, focus trap inside panel
 *   - Returns focus to whichever element opened the panel
 *   - Respects `prefers-reduced-motion`
 *   - ARIA: role="dialog" aria-modal="false" (non-blocking dialog pattern,
 *     same as Linear's right-side issue inspector)
 *
 * Bar: Linear's right-side issue panel, Stripe Dashboard's resource drawers,
 *      Notion's page-side comments. No centered modal anywhere in modern
 *      tooling, and we are out of that business too.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/format';

type OverridePanelProps = {
  open: boolean;
  fieldPath: string;
  fieldLabel: string;
  currentValue: string | number | null | undefined;
  currentSource: string;
  onClose: () => void;
  onSubmit: (next: { value: string; note: string }) => Promise<void>;
};

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener?.('change', onChange);
    return () => mql.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

// Render a fixed-position right drawer that animates in. We keep the
// component mounted while `open` is true and on close drive it back to
// `translate-x-full` before unmounting so the slide-out animation plays.
export default function OverridePanel({
  open,
  fieldPath,
  fieldLabel,
  currentValue,
  currentSource,
  onClose,
  onSubmit,
}: OverridePanelProps) {
  const titleId = useId();
  const [mounted, setMounted] = useState(false);
  const [visible, setVisible] = useState(false); // drives the translate-x animation
  const [value, setValue] = useState<string>(
    currentValue == null ? '' : String(currentValue),
  );
  const [note, setNote] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  const panelRef = useRef<HTMLDivElement | null>(null);
  const valueInputRef = useRef<HTMLInputElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => setMounted(true), []);

  // Reset form whenever a fresh override target lands.
  useEffect(() => {
    if (!open) return;
    setValue(currentValue == null ? '' : String(currentValue));
    setNote('');
    setSubmitting(false);
  }, [open, fieldPath, currentValue]);

  // Capture the focused element so we can return focus when the panel
  // closes — keyboard users land back on the pencil button they opened
  // the panel from.
  useEffect(() => {
    if (open) {
      returnFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    }
  }, [open]);

  // Slide-in animation: mount at translate-x-full, then on the next paint
  // flip to translate-x-0. This guarantees the browser sees both states.
  useLayoutEffect(() => {
    if (!open) {
      setVisible(false);
      return;
    }
    // rAF flip — gives the DOM a paint at the closed position before
    // transitioning to the open position.
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, [open]);

  // Autofocus the value input once the panel has rendered.
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => valueInputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, [open]);

  const handleClose = useCallback(() => {
    // Animate out first if motion is allowed.
    if (reducedMotion) {
      onClose();
      // Return focus on next tick so React unmounts first.
      setTimeout(() => returnFocusRef.current?.focus?.(), 0);
      return;
    }
    setVisible(false);
    setTimeout(() => {
      onClose();
      returnFocusRef.current?.focus?.();
    }, 200);
  }, [onClose, reducedMotion]);

  // ESC closes (when not mid-submit).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) {
        e.preventDefault();
        handleClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, submitting, handleClose]);

  // Focus trap — Tab cycles inside the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !panelRef.current) return;
      const focusables = panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const canSubmit =
    value.trim().length > 0 && note.trim().length > 0 && !submitting;

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ value: value.trim(), note: note.trim() });
      handleClose();
    } catch {
      // Caller surfaces the toast — keep the panel open so the analyst
      // can adjust + retry without retyping the justification.
      setSubmitting(false);
    }
  }

  if (!open || !mounted || typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50"
      aria-hidden={false}
    >
      {/* Backdrop — semi-transparent, click closes. Kept light-touch so
          the surrounding workspace stays legible (Linear-style). */}
      <div
        onClick={handleClose}
        aria-hidden="true"
        className={cn(
          'absolute inset-0 bg-ink-900/30 backdrop-blur-[1px]',
          'transition-opacity duration-200 ease-out',
          visible ? 'opacity-100' : 'opacity-0',
          reducedMotion && 'transition-none',
        )}
      />

      {/* Drawer */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        className={cn(
          'absolute top-0 right-0 h-full bg-white shadow-2xl border-l border-border',
          'w-full sm:w-[420px] flex flex-col',
          'transition-transform duration-200 ease-out',
          visible ? 'translate-x-0' : 'translate-x-full',
          reducedMotion && 'transition-none',
        )}
      >
        {/* Sticky header */}
        <header className="sticky top-0 bg-white/95 backdrop-blur-sm border-b border-border px-5 py-3.5 flex items-start justify-between gap-3 flex-shrink-0">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
              Override
            </div>
            <h2
              id={titleId}
              className="text-[16px] font-semibold text-ink-900 leading-tight mt-0.5 truncate"
            >
              {fieldLabel}
            </h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="text-ink-500 hover:text-ink-900 rounded p-1 -m-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50"
            aria-label="Close override panel"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Current value chip */}
          <div>
            <div className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
              Current value
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center px-2.5 py-1 rounded-md bg-ink-100 border border-border font-mono text-[12.5px] text-ink-900 tabular-nums">
                {currentValue == null || currentValue === '' ? '—' : String(currentValue)}
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-brand-50 border border-brand-500/30 text-[11px] font-medium text-brand-700">
                <span className="w-1 h-1 rounded-full bg-brand-500" aria-hidden="true" />
                {currentSource || 'unknown source'}
              </span>
            </div>
          </div>

          {/* New value */}
          <label className="block">
            <span className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
              New value
            </span>
            <input
              ref={valueInputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={submitting}
              className="mt-1.5 w-full px-3 py-2 border border-border rounded-md text-[15px] text-ink-900 tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:border-brand-500 disabled:opacity-50"
              placeholder="Enter the override value"
              aria-describedby={`${titleId}-path`}
            />
            <span
              id={`${titleId}-path`}
              className="block mt-1 text-[11px] text-ink-500 font-mono"
            >
              {fieldPath}
            </span>
          </label>

          {/* Mandatory justification */}
          <label className="block">
            <span className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
              Justification <span className="text-danger-500">*</span>
            </span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={submitting}
              rows={4}
              className="mt-1.5 w-full px-3 py-2 border border-border rounded-md text-[12.5px] leading-relaxed text-ink-900 resize-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:border-brand-500 disabled:opacity-50"
              placeholder="e.g., Normalized mgmt fee from 2.8% to 3% institutional standard."
              required
            />
            <p className="text-[11px] text-ink-500 mt-1 leading-relaxed">
              Required. Future reviewers (and your IC) see this note next to
              the number on the AssumptionBadge tooltip + the IC memo.
            </p>
          </label>
        </div>

        {/* Sticky footer */}
        <footer className="sticky bottom-0 bg-white border-t border-border px-5 py-3 flex items-center justify-end gap-2 flex-shrink-0">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleClose}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            loading={submitting}
            disabled={!canSubmit}
          >
            {submitting ? 'Saving…' : 'Save Override'}
          </Button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
