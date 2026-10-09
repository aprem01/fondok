'use client';

/**
 * Investment Bridge (tester round R-073) — placed immediately before Scenario
 * Analysis (whose lead view is the Sensitivity analysis).
 *
 * A waterfall from the equity invested at close to the total cash returned to
 * equity, attributing the movement across Acquisition · Renovation / PIP ·
 * Operations · Financing · Exit. Every bar comes from the worker's
 * `GET /deals/{id}/engines/investment-bridge`, which reads the canonical run's
 * engine outputs (capital Sources & Uses, returns NOI series / sale figures,
 * and the debt inputs the returns engine consumed) — nothing is computed from
 * UI placeholders. A leg the run cannot support renders "—" with the reason,
 * never a zero bar, and the footer states whether the bars foot to the
 * engine's own total (Σ returns.cash_flows[1:]).
 */

import { Fragment, useEffect, useState } from 'react';
import { api, isWorkerConnected, type InvestmentBridgeResponse } from '@/lib/api';
import { layoutBridge, type BridgeStep } from '@/lib/investmentBridge';
import { SectionCard, palette, prov, radius } from '@/components/design';

const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;
const money = (v: number) => `${v < 0 ? '−$' : '$'}${Math.round(Math.abs(v)).toLocaleString('en-US')}`;

export default function InvestmentBridgeTab({ dealId }: { dealId: string }) {
  const isMockId = /^\d+$/.test(dealId);
  const live = isWorkerConnected() && !isMockId && !!dealId;
  const [data, setData] = useState<InvestmentBridgeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!live) return;
    const ac = new AbortController();
    setError(null);
    api.engines
      .investmentBridge(dealId, ac.signal)
      .then(setData)
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => ac.abort();
  }, [dealId, live]);

  return (
    <div style={{ maxWidth: 1320, display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="investment-bridge">
      <div
        style={{
          background: palette.cardWhite,
          border: `1px solid ${palette.border}`,
          borderRadius: radius.card,
          padding: '12px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Investment Bridge</span>
        <span style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}>
          How the equity invested at close becomes the cash returned to equity — attributed across
          acquisition, renovation, operations, financing and exit. Deal-level, before GP/LP allocation;
          read from the Base Case run.
        </span>
      </div>

      {!live ? (
        <Notice text="The Investment Bridge reads the live model — open a worker-backed deal." />
      ) : error ? (
        <Notice text={`Could not load the Investment Bridge: ${error}`} />
      ) : !data ? (
        <Notice text="Loading the Investment Bridge…" />
      ) : (
        <BridgeBody data={data} />
      )}
    </div>
  );
}

function Notice({ text }: { text: string }) {
  return (
    <SectionCard>
      <div style={{ padding: '14px 0', fontSize: 12.5, color: palette.textMuted }}>{text}</div>
    </SectionCard>
  );
}

function BridgeBody({ data }: { data: InvestmentBridgeResponse }) {
  const layout = layoutBridge(data);
  const [open, setOpen] = useState<string | null>(null);
  if (!layout) {
    return <Notice text={data.reason ?? 'The returns engine has not produced a cash-flow series for this deal.'} />;
  }
  const span = Math.max(1, layout.max - layout.min);
  const pct = (v: number) => `${((v - layout.min) / span) * 100}%`;
  const zeroAt = pct(0);

  return (
    <>
      {/* Summary strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        <Stat label="Equity invested" value={data.equity_invested != null ? mm(data.equity_invested) : '—'} sub="Funded at close · capital.equity_amount" testId="bridge-equity-invested" />
        <Stat label="Equity returned" value={data.equity_returned != null ? mm(data.equity_returned) : '—'} sub="Σ levered cash flow to equity over the hold" testId="bridge-equity-returned" />
        <Stat label="Equity profit" value={data.equity_profit != null ? mm(data.equity_profit) : '—'} sub={data.hold_years ? `Over the ${data.hold_years}-year hold` : 'Returned less invested'} testId="bridge-equity-profit" />
      </div>

      <SectionCard title="Equity invested → equity returned" note="Click a bar for its engine fields" data-testid="bridge-waterfall">
        <div role="table" aria-label="Investment Bridge" style={{ marginTop: 2 }}>
          {layout.steps.map((s) => (
            <Fragment key={s.key}>
              <BridgeRow
                step={s}
                left={pct(Math.min(s.from, s.to))}
                width={`${(Math.abs(s.to - s.from) / span) * 100}%`}
                zeroAt={zeroAt}
                expanded={open === s.key}
                onToggle={s.leg && s.leg.components.length > 0 ? () => setOpen((o) => (o === s.key ? null : s.key)) : undefined}
              />
              {open === s.key && s.leg && (
                <div data-testid={`bridge-components-${s.key}`} style={{ margin: '2px 0 8px 162px', padding: '8px 12px', background: palette.surfaceTint, border: `1px solid ${palette.border}`, borderRadius: 7 }}>
                  <div style={{ fontSize: 11, color: palette.textMuted, marginBottom: 6 }}>{s.leg.formula}</div>
                  {s.leg.components.map((c) => (
                    <div key={c.label} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12, padding: '3px 0', borderBottom: `1px solid ${palette.hairlineRow}` }}>
                      <span style={{ color: palette.ink }}>
                        {c.label} <span style={{ color: palette.textFaint, fontSize: 10.5 }}>· {c.source}</span>
                      </span>
                      <span style={{ fontVariantNumeric: 'tabular-nums', color: c.value < 0 ? prov.amber : palette.ink }}>{money(c.value)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Fragment>
          ))}
        </div>
        <div data-testid="bridge-foot" style={{ fontSize: 11, color: layout.foots ? palette.textMuted : prov.amber, marginTop: 10, lineHeight: 1.5 }}>
          {layout.foots && layout.computedTotal != null && layout.equityReturned != null
            ? `Foots: ${mm(data.equity_invested ?? 0)} invested + the five legs = ${mm(layout.computedTotal)}, equal to the ${mm(layout.equityReturned)} returned to equity (Σ levered cash flow, returns engine).`
            : data.unavailable.length > 0
              ? `Does not foot yet — ${data.unavailable.length === 1 ? 'one leg is' : `${data.unavailable.length} legs are`} unavailable on this run (${data.legs.filter((l) => l.status === 'unavailable').map((l) => l.label).join(', ')}).`
              : `Does not foot: the legs sum to ${layout.computedTotal != null ? mm(layout.computedTotal) : '—'} against ${layout.equityReturned != null ? mm(layout.equityReturned) : '—'} returned — the run's pieces disagree; re-run the model.`}
        </div>
      </SectionCard>
    </>
  );
}

function BridgeRow({
  step,
  left,
  width,
  zeroAt,
  expanded,
  onToggle,
}: {
  step: BridgeStep;
  left: string;
  width: string;
  zeroAt: string;
  expanded: boolean;
  onToggle?: () => void;
}) {
  const total = step.kind !== 'leg';
  const dash = step.value == null;
  const bg = total ? palette.inkNavy : (step.value ?? 0) < 0 ? 'oklch(70% 0.10 40)' : 'oklch(60% 0.10 155)';
  return (
    <div
      role="row"
      data-testid={`bridge-row-${step.key}`}
      onClick={onToggle}
      style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0', cursor: onToggle ? 'pointer' : 'default' }}
    >
      <span role="cell" style={{ width: 150, flexShrink: 0, fontSize: 12, color: palette.ink, fontWeight: total ? 700 : 500 }}>
        {step.label}
        {onToggle && <span style={{ color: palette.textFaint, marginLeft: 6 }}>{expanded ? '▾' : '▸'}</span>}
      </span>
      <span role="cell" style={{ flex: 1, height: 14, background: '#f3f2ee', borderRadius: 4, position: 'relative', overflow: 'hidden' }}>
        <span aria-hidden style={{ position: 'absolute', left: zeroAt, top: 0, bottom: 0, width: 1, background: palette.border }} />
        {!dash && <span style={{ position: 'absolute', left, width, top: 1, bottom: 1, background: bg, borderRadius: 3 }} />}
      </span>
      <span role="cell" data-testid={`bridge-value-${step.key}`} style={{ width: 112, flexShrink: 0, textAlign: 'right', fontSize: 12.5, fontVariantNumeric: 'tabular-nums', fontWeight: total ? 700 : 400, color: dash ? palette.textFaint : (step.value ?? 0) < 0 ? prov.amber : palette.ink }}>
        {dash ? '—' : mm(step.value as number)}
      </span>
      {dash && step.leg?.reason && (
        <span data-testid={`bridge-reason-${step.key}`} style={{ fontSize: 11, color: palette.textMuted, maxWidth: 320 }}>{step.leg.reason}</span>
      )}
    </div>
  );
}

function Stat({ label, value, sub, testId }: { label: string; value: string; sub: string; testId: string }) {
  return (
    <div data-testid={testId} style={{ background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: radius.card, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: palette.eyebrow }}>{label}</span>
      <span style={{ fontSize: 19, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
      <span style={{ fontSize: 11, color: palette.textMuted }}>{sub}</span>
    </div>
  );
}
