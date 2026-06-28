'use client';
/**
 * MaxPricePanel — Wave 2 P2.8
 *
 * Two-number headline card: "Max price for X% IRR" + "Max price for Y× EM".
 * Binding-constraint chip indicates which target is tighter (the offerable
 * price is min(irr_price, em_price)).
 *
 * Inline "Re-solve" form lets the analyst change targets without leaving
 * the panel. Per Wave 1 no-modals rule the form opens in-place below the
 * cards.
 */
import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { fmtCurrency, fmtPct } from '@/lib/format';
import { api } from '@/lib/api';
import type { PricingMaxPriceResponse } from '@/lib/api';

interface Props {
  dealId: string;
}

const BINDING_CHIP: Record<
  PricingMaxPriceResponse['binding_constraint'],
  { label: string; className: string }
> = {
  irr: {
    label: 'Binding: IRR',
    className: 'bg-brand-50 text-brand-700 border-brand-200',
  },
  em: {
    label: 'Binding: EM',
    className: 'bg-violet-50 text-violet-700 border-violet-200',
  },
  both: {
    label: 'Binding: IRR + EM',
    className: 'bg-ink-100 text-ink-700 border-border',
  },
};

export default function MaxPricePanel({ dealId }: Props) {
  const [data, setData] = useState<PricingMaxPriceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [targetIrr, setTargetIrr] = useState('15');
  const [targetEm, setTargetEm] = useState('1.8');

  const fetchMaxPrice = async (irrPct?: number, em?: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.analysis.pricing.maxPrice(dealId, {
        target_irr: irrPct ?? 0.15,
        target_em: em ?? 1.8,
      });
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to solve max price');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (dealId) void fetchMaxPrice();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dealId]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const irr = Number(targetIrr) / 100;
    const em = Number(targetEm);
    if (!isFinite(irr) || !isFinite(em)) return;
    void fetchMaxPrice(irr, em);
    setShowForm(false);
  };

  const chip = data && BINDING_CHIP[data.binding_constraint];

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">
            Max-Price Solver
          </h3>
          <p className="text-[12px] text-ink-500">
            Bisects on purchase price to hit your IRR + EM hurdles.
          </p>
        </div>
        <Button variant="ghost" onClick={() => setShowForm(s => !s)}>
          {showForm ? 'Cancel' : 'Re-solve'}
        </Button>
      </div>

      {error && (
        <div className="text-[12.5px] text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {loading && (
        <div className="text-[12px] text-ink-500">Bisecting…</div>
      )}

      {data && !loading && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="border border-border rounded p-3">
              <div className="text-[11px] uppercase tracking-wide text-ink-500 font-semibold">
                Max price for {fmtPct(data.target_irr)} IRR
              </div>
              <div className="text-[20px] font-bold text-ink-900 mt-1">
                {fmtCurrency(data.max_price_for_irr)}
              </div>
            </div>
            <div className="border border-border rounded p-3">
              <div className="text-[11px] uppercase tracking-wide text-ink-500 font-semibold">
                Max price for {data.target_em.toFixed(2)}× EM
              </div>
              <div className="text-[20px] font-bold text-ink-900 mt-1">
                {fmtCurrency(data.max_price_for_em)}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between mt-3 text-[12px]">
            {chip && (
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded border ${chip.className} font-medium`}
              >
                {chip.label}
              </span>
            )}
            {data.final_price_per_key > 0 && (
              <span className="text-ink-700">
                Offerable per-key:{' '}
                <span className="font-semibold">
                  {fmtCurrency(data.final_price_per_key)}
                </span>
              </span>
            )}
            <span className="text-ink-500">{data.iters} iters</span>
          </div>
        </>
      )}

      {showForm && (
        <form onSubmit={onSubmit} className="mt-3 border-t border-border pt-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-[12px] text-ink-700">
              Target IRR (%)
              <input
                type="number"
                step="0.5"
                value={targetIrr}
                onChange={e => setTargetIrr(e.target.value)}
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
            <label className="text-[12px] text-ink-700">
              Target EM (×)
              <input
                type="number"
                step="0.05"
                value={targetEm}
                onChange={e => setTargetEm(e.target.value)}
                className="mt-1 w-full border border-border rounded px-2 py-1 text-[13px]"
              />
            </label>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
            <Button type="submit">Re-solve</Button>
          </div>
        </form>
      )}
    </Card>
  );
}
