'use client';

/**
 * Override Modal — Wave 1 scaffolding.
 *
 * Roadmap item #6 (June 2026 call). Eshan's exact ask: "you should have
 * the option to hard-code a number, and a note explaining why."
 *
 * STATUS: skeleton. Form structure + validation contract are in place;
 * the submit handler needs wiring to the deal PATCH endpoint and the
 * backend FieldOverrideRecord schema in apps/worker/app/api/deals.py.
 *
 * Contract:
 *   - The note field is REQUIRED (non-empty).
 *   - Submit is disabled until both value AND note are populated.
 *   - On success, the deal recomputes with the new override + note.
 *   - The note is surfaced on the AssumptionBadge tooltip and in the
 *     IC memo's underwriting section.
 */

import { useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/Button';

type OverrideModalProps = {
  open: boolean;
  fieldPath: string;
  fieldLabel: string;
  currentValue: string | number | null | undefined;
  currentSource: string;
  onClose: () => void;
  onSubmit: (next: { value: string; note: string }) => Promise<void>;
};

export default function OverrideModal({
  open,
  fieldPath,
  fieldLabel,
  currentValue,
  currentSource,
  onClose,
  onSubmit,
}: OverrideModalProps) {
  const [value, setValue] = useState<string>(
    currentValue == null ? '' : String(currentValue),
  );
  const [note, setNote] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  const canSubmit = value.trim().length > 0 && note.trim().length > 0 && !submitting;

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ value: value.trim(), note: note.trim() });
      onClose();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-[16px] font-semibold text-ink-900">
              Override value
            </h2>
            <p className="text-[12px] text-ink-500 mt-0.5">{fieldLabel}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-500 hover:text-ink-900"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Read-only current value */}
        <div className="mb-4 p-3 rounded-md bg-ink-50 text-[12.5px]">
          <div className="text-ink-500 mb-1">Current value</div>
          <div className="font-mono text-ink-900">
            {currentValue == null ? '—' : String(currentValue)}
          </div>
          <div className="text-[11px] text-ink-500 mt-1">
            Source: {currentSource}
          </div>
        </div>

        {/* New value */}
        <label className="block mb-3">
          <span className="text-[12.5px] font-medium text-ink-900">
            New value
          </span>
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="mt-1 w-full px-3 py-2 border border-border rounded-md text-[13px]"
            placeholder="Enter the override value"
          />
        </label>

        {/* Mandatory note */}
        <label className="block mb-4">
          <span className="text-[12.5px] font-medium text-ink-900">
            Justification <span className="text-danger-500">*</span>
          </span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            className="mt-1 w-full px-3 py-2 border border-border rounded-md text-[13px] resize-none"
            placeholder="e.g., Normalized mgmt fee from 2.8% to 3% institutional standard"
            required
          />
          <p className="text-[11px] text-ink-500 mt-1">
            Required. Future reviewers (and your IC) will see this note
            next to the number.
          </p>
        </label>

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {submitting ? 'Saving…' : 'Save override'}
          </Button>
        </div>
      </div>
    </div>
  );
}
