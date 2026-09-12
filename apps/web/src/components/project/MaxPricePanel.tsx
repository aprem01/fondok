'use client';
/**
 * MaxPricePanel — the canonical "Max Price Solver" block (FON-68).
 *
 * Source: `design/canonical/Returns Tab.dc.html` → Pricing sub-tab
 * (`pricingKpis` / `pricingConstraints` / `pricingNote`).
 *
 *   • 4 KPI tiles — current purchase price · max price · headroom / gap ·
 *     max price / key
 *   • constraint rows — Target levered IRR · Target MOIC (both LINKED from
 *     the Investment Profile) · max price @ IRR · max price @ MOIC ·
 *     binding constraint · hold · exit cap · LTV / rate (context)
 *   • note — the lower-of rule spelled out with the solved numbers
 *
 * The hurdles are READ from the deal (`deal.target_irr` / `deal.target_moic`,
 * set on Overview → Investment Profile). There are no panel-local hurdle
 * inputs and no default: when the deal has no target the block shows the
 * worker's 422 copy plus a "→ Investment Profile" link and NO numbers.
 * Every figure comes from `POST /analysis/{id}/pricing/max-price`.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { fmtPct } from '@/lib/format';
import {
  api,
  WorkerError,
  type EngineOutputsResponse,
  type PricingMaxPriceResponse,
  type WorkerDeal,
} from '@/lib/api';
import { ProvenanceDot, palette, prov, radius } from '@/components/design';

export const NO_TARGET_MESSAGE =
  'No return target set — set Target Levered IRR / Target MOIC on the Investment Profile or pass them explicitly';

export const BINDING_LABEL: Record<PricingMaxPriceResponse['binding_constraint'], string> = {
  irr: 'IRR',
  em: 'MOIC',
  both: 'IRR + MOIC',
};

const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;
const money = (v: number) => `${v < 0 ? '−$' : '$'}${Math.round(Math.abs(v)).toLocaleString('en-US')}`;
const x = (v: number) => `${v.toFixed(2)}x`;

/** True when the deal carries at least one hurdle the solver can use. */
export function dealHasTarget(deal: WorkerDeal | null | undefined): boolean {
  return deal?.target_irr != null || deal?.target_moic != null;
}

/** Pull the worker's `detail` out of a 422; fall back to the raw message. */
export function pricingErrorMessage(err: unknown): string {
  if (err instanceof WorkerError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      /* not JSON — fall through */
    }
    return err.body || err.message;
  }
  return err instanceof Error ? err.message : 'Failed to solve max price';
}

interface Props {
  dealId: string;
  /** The deal record — its `target_irr` / `target_moic` are the hurdles. */
  deal: WorkerDeal | null;
  /** Canonical engine outputs (unused for numbers here; the solver reports
   *  its own base price so the headline and headroom share one input). */
  outputs?: EngineOutputsResponse | null;
  /** True while a Returns Live-Assumptions sandbox is active. The solver does
   *  NOT consume it — a "max price under a hypothetical LTV" is a materially
   *  different question — so the panel says so inline rather than silently
   *  answering a question the analyst did not ask (FON-68 §1). */
  sandboxActive?: boolean;
  /** Navigate to Overview → Investment Profile (where the hurdles live). */
  onGoToProfile: () => void;
}

export default function MaxPricePanel({
  dealId,
  deal,
  sandboxActive = false,
  onGoToProfile,
}: Props) {
  const [data, setData] = useState<PricingMaxPriceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetIrr = deal?.target_irr ?? null;
  const targetMoic = deal?.target_moic ?? null;
  const hasTarget = dealHasTarget(deal);

  useEffect(() => {
    if (!dealId || !deal || !hasTarget) {
      setData(null);
      setError(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    // Empty body — the worker reads the hurdles from the deal.
    api.analysis.pricing
      .maxPrice(dealId, {}, ctrl.signal)
      .then((res) => setData(res))
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setData(null);
        setError(pricingErrorMessage(err));
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [dealId, deal, hasTarget, targetIrr, targetMoic]);

  const profileLink = (
    <button type="button" onClick={onGoToProfile} style={linkBtn}>
      → Investment Profile
    </button>
  );

  return (
    <div style={{ background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: radius.card, padding: '16px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: palette.eyebrow, textTransform: 'uppercase', letterSpacing: '.03em' }}>
          Max Price Solver
        </span>
        <span style={{ fontSize: 11, color: palette.textFaint }}>Solved against the hurdles on the Investment Profile</span>
      </div>

      {sandboxActive && <CanonicalOnlyNote />}

      {!deal && <div style={{ fontSize: 12, color: palette.textMuted }}>Loading deal…</div>}

      {deal && !hasTarget && (
        <NoTarget message={NO_TARGET_MESSAGE} link={profileLink} />
      )}

      {deal && hasTarget && error && <NoTarget message={error} link={profileLink} />}

      {deal && hasTarget && !error && loading && !data && (
        <div style={{ fontSize: 12, color: palette.textMuted }}>Bisecting on purchase price…</div>
      )}

      {deal && hasTarget && !error && data && (
        <SolvedBlock data={data} link={profileLink} />
      )}
    </div>
  );
}

/** FON-68 §1 — a sandbox is on and this block ignores it. Say so. */
export function CanonicalOnlyNote() {
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        alignItems: 'baseline',
        flexWrap: 'wrap',
        background: 'oklch(97% 0.03 250)',
        border: '1px solid #c9d4ee',
        borderRadius: 8,
        padding: '8px 12px',
        marginBottom: 12,
        fontSize: 11.5,
        color: palette.ink,
      }}
    >
      <span style={{ fontWeight: 700, color: palette.linkBlue, whiteSpace: 'nowrap' }}>
        Canonical case
      </span>
      <span>
        Solved on the canonical case; the active sensitivity is not applied. Reset the sandbox
        on Sensitivities to compare like for like.
      </span>
    </div>
  );
}

// ── The "no hurdle" state: the worker's copy + the link, no numbers. ──
function NoTarget({ message, link }: { message: string; link: React.ReactNode }) {
  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        background: palette.surfaceTint, border: `1px solid ${palette.border}`, borderRadius: 8,
        padding: '10px 12px', fontSize: 12.5, color: palette.hoverInk, lineHeight: 1.5,
      }}
    >
      <span style={{ flex: 1, minWidth: 240 }}>{message}</span>
      {link}
    </div>
  );
}

// ── Solved: 4 tiles + constraint rows + the lower-of note. ──
function SolvedBlock({ data, link }: { data: PricingMaxPriceResponse; link: React.ReactNode }) {
  const rooms = data.rooms && data.rooms > 0 ? data.rooms : null;
  const base = data.base_purchase_price;
  const maxPrice = data.max_price;
  const headroom = maxPrice != null ? maxPrice - base : null;
  const bindingStatus = data.binding_constraint === 'irr' ? data.irr_status
    : data.binding_constraint === 'em' ? data.em_status
      : (data.irr_status === 'converged' && data.em_status === 'converged' ? 'converged' : data.irr_status);
  const noPriceSub = bindingStatus === 'above_ceiling'
    ? 'Clears the hurdles at every price up to 2× the current basis'
    : 'No price clears the hurdles';

  const tiles: { label: string; value: string; sub: string; color: string; border: string; bg: string }[] = [
    {
      label: 'Current purchase price', value: mm(base),
      sub: rooms ? `${money(base / rooms)} / key` : 'Purchase price in the model',
      color: prov.black, border: palette.border, bg: palette.cardWhite,
    },
    {
      label: 'Max price', value: maxPrice != null ? mm(maxPrice) : '—',
      sub: maxPrice != null ? 'Maximum price clearing all hurdles' : noPriceSub,
      color: prov.black, border: '#dbe3f5', bg: 'oklch(97.5% 0.015 250)',
    },
    {
      label: headroom != null && headroom >= 0 ? 'Headroom' : 'Gap',
      value: headroom == null ? '—' : `${headroom >= 0 ? '+' : ''}${mm(headroom)}`,
      sub: headroom == null ? '' : headroom >= 0 ? 'Room above the current basis' : 'Current basis exceeds the max price',
      color: headroom != null && headroom >= 0 ? prov.green : prov.amber, border: palette.border, bg: palette.cardWhite,
    },
    {
      label: 'Max price / key', value: maxPrice != null && rooms ? money(maxPrice / rooms) : '—',
      sub: 'At the binding hurdle', color: prov.black, border: palette.border, bg: palette.cardWhite,
    },
  ];

  const irrLabel = data.target_irr != null ? fmtPct(data.target_irr, 1) : '—';
  const moicLabel = data.target_em != null ? x(data.target_em) : '—';
  const irrPrice = data.max_price_for_irr;
  const emPrice = data.max_price_for_em;
  const solvedValue = (price: number | null, status: PricingMaxPriceResponse['irr_status']): string => {
    if (price != null) return mm(price);
    if (status === 'not_requested') return '— not set';
    if (status === 'above_ceiling') return '≥ 2× basis';
    return '— unreachable';
  };

  interface Row { label: string; value: string; state: 'linked' | 'calculated'; color: string; weight: number; title: string }
  const rows: Row[] = [
    { label: 'Target levered IRR', value: irrLabel, state: 'linked', color: prov.green, weight: 400, title: 'Linked from Overview → Investment Profile (analyst input)' },
    { label: 'Target MOIC', value: moicLabel, state: 'linked', color: prov.green, weight: 400, title: 'Linked from Overview → Investment Profile (analyst input)' },
    { label: `Max price @ ${irrLabel} IRR`, value: solvedValue(irrPrice, data.irr_status), state: 'calculated', color: prov.gray, weight: 400, title: 'Bisection on purchase price until levered IRR equals the target' },
    { label: `Max price @ ${moicLabel} MOIC`, value: solvedValue(emPrice, data.em_status), state: 'calculated', color: prov.gray, weight: 400, title: 'Bisection on purchase price until the equity multiple equals the target' },
    { label: 'Binding constraint', value: BINDING_LABEL[data.binding_constraint], state: 'calculated', color: prov.black, weight: 700, title: 'The hurdle that yields the lower max price' },
    { label: 'Hold period', value: `${Number.isInteger(data.hold_years) ? data.hold_years : data.hold_years.toFixed(1)} years`, state: 'linked', color: prov.green, weight: 400, title: 'Linked from Investment → Exit / Reversion' },
    { label: 'Exit cap rate', value: fmtPct(data.exit_cap_rate, 2), state: 'linked', color: prov.green, weight: 400, title: 'Linked from Investment → Exit / Reversion' },
    { label: 'LTV / interest rate', value: `${fmtPct(data.ltv, 1)} · ${fmtPct(data.interest_rate, 2)}`, state: 'linked', color: prov.green, weight: 400, title: 'Linked from Debt' },
  ];

  const bindingNote = data.binding_constraint === 'both'
    ? 'both bind at the same price'
    : `the ${BINDING_LABEL[data.binding_constraint]} hurdle binds`;
  const note = data.target_irr != null && data.target_em != null
    ? `Hurdles come from the Investment Profile, not from Returns. Each hurdle is solved independently and the lower price governs: ${irrPrice != null ? mm(irrPrice) : '—'} at the IRR hurdle, ${emPrice != null ? mm(emPrice) : '—'} at the MOIC hurdle — ${bindingNote}.`
    : `Hurdles come from the Investment Profile, not from Returns. Only the ${BINDING_LABEL[data.binding_constraint]} hurdle is set, so it governs on its own — set the other target to price against both.`;

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        {tiles.map((k) => (
          <div key={k.label} style={{ border: `1px solid ${k.border}`, background: k.bg, borderRadius: 9, padding: '13px 15px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase', marginBottom: 6 }}>{k.label}</div>
            <div style={{ fontSize: 21, fontWeight: 700, color: k.color, fontVariantNumeric: 'tabular-nums' }}>{k.value}</div>
            <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 4, lineHeight: 1.4 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: '0 32px', marginTop: 14 }}>
        {rows.map((r) => (
          <div key={r.label} title={r.title} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, fontSize: 12.5, padding: '6px 0', borderBottom: `1px solid ${palette.hairlineRow}` }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
              <ProvenanceDot state={r.state} size={8} />
              <span style={{ color: palette.textSecondary }}>{r.label}</span>
            </span>
            <span style={{ color: r.color, fontWeight: r.weight, fontVariantNumeric: 'tabular-nums' }}>{r.value}</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 10 }}>
        <span style={{ fontSize: 11, color: palette.textMuted, lineHeight: 1.5, flex: 1, minWidth: 240 }}>{note}</span>
        {link}
      </div>
    </>
  );
}

const linkBtn: CSSProperties = {
  background: 'none', border: 'none', padding: 0, fontFamily: 'inherit', fontSize: 11.5,
  color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
};
