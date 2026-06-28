'use client';
/**
 * LOIPanel — Wave 2 P2.8
 *
 * Pre-rendered LOI markdown body in a card with a "Copy to clipboard"
 * button + disabled "Download .docx" stub (export pipeline lands in a
 * later wave — kept disabled with a tooltip).
 *
 * Editable buyer/seller/dates live in an inline edit-in-place panel
 * above the rendered body — NO modals (Wave 1 rule). Hitting Save
 * re-fetches the LOI from the server so the markdown rebuilds in
 * lockstep with the structured fields.
 *
 * Safety guarantee: this endpoint NEVER persists the LOI as a
 * document. The "Save" button on this panel only refreshes the
 * preview; analyst would click a future "Save to Documents" button
 * to persist (intentionally absent in this wave).
 */
import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { fmtCurrency, fmtPct } from '@/lib/format';
import { api } from '@/lib/api';
import type { PricingLOIBody, PricingLOIResponse } from '@/lib/api';

interface Props {
  dealId: string;
}

export default function LOIPanel({ dealId }: Props) {
  const [data, setData] = useState<PricingLOIResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const { toast } = useToast();

  // Editable scratch state — committed via Save / discarded via Cancel.
  const [scratch, setScratch] = useState<PricingLOIBody>({});

  const fetchLoi = async (body: PricingLOIBody = {}) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.analysis.pricing.loi(dealId, body);
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to draft LOI');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (dealId) void fetchLoi();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dealId]);

  const onCopy = () => {
    if (!data) return;
    void navigator.clipboard.writeText(data.rendered_markdown);
    toast('LOI markdown copied to clipboard', { type: 'success' });
  };

  const onSave = () => {
    void fetchLoi(scratch);
    setEditing(false);
  };

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">
            LOI Draft
          </h3>
          <p className="text-[12px] text-ink-500">
            Templated hospitality LOI — copy-paste-ready. Server never
            saves this as a document.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setEditing(e => !e)}>
            {editing ? 'Cancel' : 'Edit terms'}
          </Button>
          <Button
            variant="ghost"
            disabled
            title="Export pipeline lands in a later wave"
          >
            Download .docx
          </Button>
          <Button onClick={onCopy} disabled={!data}>
            Copy to clipboard
          </Button>
        </div>
      </div>

      {error && (
        <div className="text-[12.5px] text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {editing && (
        <div className="border border-border rounded p-3 mb-3 bg-ink-50">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-[12px] text-ink-700">
              Buyer
              <input
                type="text"
                defaultValue={data?.buyer ?? ''}
                onChange={e =>
                  setScratch(s => ({ ...s, buyer: e.target.value }))
                }
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
            <label className="text-[12px] text-ink-700">
              Seller
              <input
                type="text"
                defaultValue={data?.seller ?? ''}
                onChange={e =>
                  setScratch(s => ({ ...s, seller: e.target.value }))
                }
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
            <label className="text-[12px] text-ink-700">
              Due-diligence days
              <input
                type="number"
                defaultValue={data?.due_diligence_days ?? 30}
                onChange={e =>
                  setScratch(s => ({
                    ...s,
                    due_diligence_days: Number(e.target.value),
                  }))
                }
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
            <label className="text-[12px] text-ink-700">
              Earnest money (% of price)
              <input
                type="number"
                step="0.001"
                defaultValue={data?.earnest_money_pct ?? 0.01}
                onChange={e =>
                  setScratch(s => ({
                    ...s,
                    earnest_money_pct: Number(e.target.value),
                  }))
                }
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
          </div>
          <div className="flex justify-end gap-2 mt-3">
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button onClick={onSave}>Save & re-render</Button>
          </div>
        </div>
      )}

      {loading && !data && (
        <div className="text-[12px] text-ink-500">Drafting LOI…</div>
      )}

      {data && (
        <div className="grid grid-cols-3 gap-3 mb-3 text-[12px]">
          <div className="border border-border rounded p-2">
            <div className="text-ink-500">Asset</div>
            <div className="font-semibold text-ink-900">{data.asset_name}</div>
          </div>
          <div className="border border-border rounded p-2">
            <div className="text-ink-500">Proposed price</div>
            <div className="font-semibold text-ink-900">
              {fmtCurrency(data.proposed_price)} (
              {fmtCurrency(data.proposed_price_per_key)}/key)
            </div>
          </div>
          <div className="border border-border rounded p-2">
            <div className="text-ink-500">DD / Earnest</div>
            <div className="font-semibold text-ink-900">
              {data.due_diligence_days}d /{' '}
              {fmtPct(data.earnest_money_pct)}
            </div>
          </div>
        </div>
      )}

      {data && (
        <pre className="border border-border rounded p-3 text-[11.5px] leading-relaxed whitespace-pre-wrap max-h-[480px] overflow-auto bg-white text-ink-800">
          {data.rendered_markdown}
        </pre>
      )}
    </Card>
  );
}
