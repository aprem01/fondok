'use client';

/**
 * "Duplicate deal" confirm dialog (project header menu).
 *
 * Replaces the earlier ``window.confirm`` so the analyst can choose, per
 * copy, whether the P&L cell comments travel with it (Sam's ask). The
 * summary lists exactly what the copy gets / does not get for the current
 * choice — from ``@/lib/duplicateDeal``, kept in step with the worker route
 * POST /deals/{id}/duplicate — and the checkbox defaults to OFF.
 */

import { useEffect, useState } from 'react';
import Modal from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import {
  DEFAULT_DUPLICATE_CHOICES,
  duplicateDealCopiedItems,
  duplicateDealSkippedItems,
  type DuplicateDealChoices,
} from '@/lib/duplicateDeal';

export interface DuplicateDealDialogProps {
  open: boolean;
  dealName: string;
  /** True while the POST is in flight — disables the buttons. */
  busy?: boolean;
  onClose: () => void;
  onConfirm: (choices: DuplicateDealChoices) => void;
}

export default function DuplicateDealDialog({
  open,
  dealName,
  busy = false,
  onClose,
  onConfirm,
}: DuplicateDealDialogProps) {
  const [includeCellComments, setIncludeCellComments] = useState(
    DEFAULT_DUPLICATE_CHOICES.includeCellComments,
  );
  // Each opening starts from the default (off) — the choice is per copy.
  useEffect(() => {
    if (open) setIncludeCellComments(DEFAULT_DUPLICATE_CHOICES.includeCellComments);
  }, [open]);

  const choices: DuplicateDealChoices = { includeCellComments };
  const copied = duplicateDealCopiedItems(choices);
  const skipped = duplicateDealSkippedItems(choices);

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Duplicate deal">
      <div className="p-5 space-y-4" data-testid="duplicate-deal-dialog">
        <p className="text-[13px] text-ink-900">
          Duplicate <strong>&ldquo;{dealName}&rdquo;</strong>? The original deal is not changed.
        </p>

        <div className="space-y-1.5">
          <div className="text-[11.5px] font-semibold uppercase tracking-wide text-ink-500">
            The copy gets
          </div>
          <ul
            className="list-disc pl-5 text-[12.5px] text-ink-700 space-y-0.5"
            data-testid="duplicate-deal-copied"
          >
            {copied.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="text-[12px] text-ink-500">
            The files themselves are shared, not uploaded again.
          </p>
        </div>

        <div className="space-y-1.5">
          <div className="text-[11.5px] font-semibold uppercase tracking-wide text-ink-500">
            Not copied
          </div>
          <ul
            className="list-disc pl-5 text-[12.5px] text-ink-700 space-y-0.5"
            data-testid="duplicate-deal-skipped"
          >
            {skipped.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="text-[12px] text-ink-500">
            The new deal shows no results until you run the model on it.
          </p>
        </div>

        <label className="flex items-start gap-2 text-[12.5px] text-ink-700 cursor-pointer">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 mt-0.5"
            checked={includeCellComments}
            disabled={busy}
            onChange={(e) => setIncludeCellComments(e.target.checked)}
          />
          <span>
            Also copy P&amp;L cell comments
            <span className="block text-[11.5px] text-ink-500">
              Each copied comment is marked as copied from this deal.
            </span>
          </span>
        </label>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={busy}
            disabled={busy}
            onClick={() => onConfirm(choices)}
          >
            {busy ? 'Duplicating…' : 'Duplicate'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
