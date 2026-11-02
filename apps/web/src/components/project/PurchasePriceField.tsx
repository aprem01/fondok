'use client';

/**
 * W5 "price" — THE purchase price editor. One component, one save path, used
 * on Overview → Entry Valuation, CAPEX → Deal Summary, and the CAPEX inputs
 * card shown when the capital engine refused for a missing price.
 *
 * The price is an analyst input: saving writes the analyst override
 * `field_overrides.purchase_price = {value, note}` (FON-74 — the note is
 * required) through PATCH /deals/{id}, then runs the full model so every
 * price-dependent engine re-derives. The badge says where the price on
 * screen came from — Override (the analyst), OM (the broker's stated asking
 * price, with its page), or Deal (the deal record) — exactly the loader's
 * precedence. No price renders "—"; nothing is ever inferred.
 */

import { useCallback, type MouseEvent } from 'react';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { useToast } from '@/components/ui/Toast';
import {
  InlineEditControls,
  inlineEditInputStyle,
  palette,
  prov,
  useInlineEdit,
} from '@/components/design';
import { api, workerErrorMessage } from '@/lib/api';
import { fmtCurrency } from '@/lib/format';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { applyOverridePatch, requiresNote } from '@/lib/overrideNote';
import {
  PURCHASE_PRICE_KEY,
  parsePurchasePrice,
  resolvePurchasePrice,
} from '@/lib/purchasePrice';

/** The run-all a save starts (a tab's `useEngineRun(..., {runMode:'all'})`). */
export interface PurchasePriceRunner {
  run: () => Promise<void>;
}

export interface PurchasePriceFieldProps {
  dealId: string;
  /** The deal's saved `field_overrides` (read for the current override and
   *  merged into on save). */
  overrides: Record<string, unknown>;
  /** The capital engine's price on the latest run, when it ran. */
  enginePrice: number | undefined;
  /** False on a sample / offline deal: the value renders read-only. */
  editable: boolean;
  /** After the PATCH lands (before the run finishes) — refresh the deal row. */
  onSaved?: () => void;
  /** The tab's run-all hook, so the tab sees the save-triggered run as in
   *  flight (its Run buttons and "re-running…" markers). Without one the
   *  field runs through its own hook. */
  runner?: PurchasePriceRunner;
  /** Run the model after the save (default true). False while another
   *  identity input is still missing — that run could only be refused. */
  runAfterSave?: boolean;
  /** What the empty, editable field invites (default "—"). */
  emptyLabel?: string;
  /** Show the source badge beside the value (default true). */
  showBadge?: boolean;
  testId?: string;
  bold?: boolean;
  color?: string;
}

export default function PurchasePriceField({
  dealId,
  overrides,
  enginePrice,
  editable,
  onSaved,
  runAfterSave = true,
  runner,
  emptyLabel,
  showBadge = true,
  testId = 'purchase-price',
  bold = false,
  color,
}: PurchasePriceFieldProps) {
  const { toast } = useToast();
  const resolved = useSource(PURCHASE_PRICE_KEY);
  const input = resolvePurchasePrice({ overrides, enginePrice, resolved });
  const ownRun = useEngineRun(editable && !runner ? dealId : '', 'returns', { runMode: 'all' });
  const run = runner ?? ownRun;

  const save = useCallback(
    async (value: number, note: string) => {
      const next = applyOverridePatch(overrides, { [PURCHASE_PRICE_KEY]: value }, note);
      try {
        await api.deals.update(dealId, { field_overrides: next });
      } catch (err) {
        toast(`Save failed — ${workerErrorMessage(err)}`, { type: 'error' });
        return;
      }
      onSaved?.();
      if (runAfterSave) {
        toast('Purchase price saved — re-running the model…', { type: 'success' });
        void run.run();
      } else {
        toast('Purchase price saved — enter the key count to run the model.', { type: 'success' });
      }
    },
    [overrides, dealId, toast, onSaved, run, runAfterSave],
  );

  const ed = useInlineEdit<number>({
    current: input.value ?? null,
    unit: 'usd',
    parse: parsePurchasePrice,
    toDraft: (v) => String(Math.round(v)),
    invalidMessage: 'Enter the purchase price in dollars (e.g. 36400000).',
    onSave: save,
    requireNote: requiresNote(PURCHASE_PRICE_KEY),
  });

  const textColor = color ?? (input.source === 'analyst_override' ? prov.blue : palette.ink);
  const display = input.value !== undefined ? fmtCurrency(input.value) : (editable && emptyLabel) || '—';
  // The Overview row opens its provenance popover on click; editing the value
  // must not also open it.
  const stop = (e: MouseEvent) => e.stopPropagation();

  const badge = showBadge && input.source ? (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }} data-testid={`${testId}-source`}>
      <AssumptionBadge
        source={input.source}
        documentId={input.documentId}
        dealId={dealId}
        overrideNote={input.note}
      />
      {input.source === 'om_broker' && input.page != null && (
        <span style={{ fontSize: 10, color: palette.textMuted }}>p. {input.page}</span>
      )}
    </span>
  ) : null;

  if (!editable) {
    return (
      <span onClick={stop} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {badge}
        <span
          data-testid={testId}
          style={{ color: textColor, fontWeight: bold ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}
        >
          {display}
        </span>
      </span>
    );
  }
  if (!ed.editing) {
    return (
      <span onClick={stop} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {badge}
        <span
          role="button"
          tabIndex={0}
          data-testid={testId}
          onClick={() => ed.start()}
          onKeyDown={(e) => { if (e.key === 'Enter') ed.start(); }}
          title="Click to enter the purchase price — an analyst input with a justification note"
          style={{
            color: textColor, fontWeight: bold ? 700 : 500, fontVariantNumeric: 'tabular-nums',
            textDecoration: 'underline dotted', cursor: 'pointer', whiteSpace: 'nowrap',
          }}
        >
          {display}
        </span>
      </span>
    );
  }
  return (
    <span
      ref={ed.containerRef}
      onClick={stop}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
    >
      <input
        type="number"
        aria-label="Purchase price"
        value={ed.draft}
        autoFocus
        disabled={ed.saving}
        onChange={(e) => ed.setDraft(e.target.value)}
        onKeyDown={ed.onKeyDown}
        className="w-36"
        style={{ ...inlineEditInputStyle, textAlign: 'right' }}
      />
      <InlineEditControls
        onSave={() => void ed.submit()}
        onCancel={ed.cancel}
        saving={ed.saving}
        note={ed.requireNote ? ed.note : undefined}
        onNote={ed.requireNote ? ed.setNote : undefined}
        noteTestId={ed.requireNote ? `${testId}-note` : undefined}
      />
    </span>
  );
}
