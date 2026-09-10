'use client';
/**
 * PricingSensitivityPanel — "Pricing Sensitivity — Max Purchase Price" (FON-68).
 *
 * Source: `design/canonical/Returns Tab.dc.html` → Pricing sub-tab
 * (`pricingMatrices`). Exit cap rate (rows) × NOI growth (columns); every
 * cell is the highest purchase price that still clears BOTH hurdles on the
 * Investment Profile — each hurdle solved independently, the lower price
 * governs, and the binding constraint is marked in the cell.
 *
 * Driven entirely by `POST /analysis/{id}/pricing/max-price-grid`, which
 * reads the hurdles from the deal. No hard-coded target: with no target set
 * the card shows the worker's copy plus a "→ Investment Profile" link and
 * renders no numbers. A cell where no price clears the hurdles renders "—".
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { fmtPct } from '@/lib/format';
import {
  api,
  type PricingMaxPriceGridCell,
  type PricingMaxPriceGridResponse,
  type WorkerDeal,
} from '@/lib/api';
import { palette, prov, radius } from '@/components/design';
import { BINDING_LABEL, NO_TARGET_MESSAGE, dealHasTarget, pricingErrorMessage } from './MaxPricePanel';

const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;
const money = (v: number) => `${v < 0 ? '−$' : '$'}${Math.round(Math.abs(v)).toLocaleString('en-US')}`;
const x = (v: number) => `${v.toFixed(2)}x`;

interface Props {
  dealId: string;
  /** The deal record — its `target_irr` / `target_moic` are the hurdles. */
  deal: WorkerDeal | null;
  onGoToProfile: () => void;
}

export default function PricingSensitivityPanel({ dealId, deal, onGoToProfile }: Props) {
  const [grid, setGrid] = useState<PricingMaxPriceGridResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetIrr = deal?.target_irr ?? null;
  const targetMoic = deal?.target_moic ?? null;
  const hasTarget = dealHasTarget(deal);

  useEffect(() => {
    if (!dealId || !deal || !hasTarget) {
      setGrid(null);
      setError(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    // Empty body — axes default around the deal's own assumptions and the
    // hurdles come from the deal.
    api.analysis.pricing
      .maxPriceGrid(dealId, {}, ctrl.signal)
      .then((res) => setGrid(res))
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setGrid(null);
        setError(pricingErrorMessage(err));
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [dealId, deal, hasTarget, targetIrr, targetMoic]);

  const rows = useMemo(() => {
    if (!grid) return [];
    const byKey = new Map<string, PricingMaxPriceGridCell>();
    for (const c of grid.cells) byKey.set(`${c.exit_cap_pct}__${c.noi_growth_pct}`, c);
    return grid.cap_axis.map((cap) => ({
      cap,
      cells: grid.noi_growth_axis.map((g) => byKey.get(`${cap}__${g}`)),
    }));
  }, [grid]);

  const bothHurdles = grid ? grid.target_irr != null && grid.target_em != null : hasTarget && targetIrr != null && targetMoic != null;
  const hurdleText = grid
    ? [
        grid.target_irr != null ? `${fmtPct(grid.target_irr, 1)} IRR` : null,
        grid.target_em != null ? `${x(grid.target_em)} MOIC` : null,
      ].filter(Boolean).join(' and ')
    : '';

  const profileLink = (
    <button type="button" onClick={onGoToProfile} style={linkBtn}>
      → Investment Profile
    </button>
  );

  return (
    <div style={{ background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: radius.card, padding: '16px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: palette.eyebrow, textTransform: 'uppercase', letterSpacing: '.03em' }}>
          Pricing Sensitivity — Max Purchase Price
        </span>
        <span style={{ fontSize: 11, color: palette.textFaint }}>
          Exit cap rate × NOI growth · highest price that still clears {bothHurdles ? 'both hurdles' : 'the hurdle'}
        </span>
      </div>

      {!deal && <div style={{ fontSize: 12, color: palette.textMuted }}>Loading deal…</div>}

      {deal && (!hasTarget || error) && (
        <div
          role="status"
          style={{
            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
            background: palette.surfaceTint, border: `1px solid ${palette.border}`, borderRadius: 8,
            padding: '10px 12px', fontSize: 12.5, color: palette.hoverInk, lineHeight: 1.5,
          }}
        >
          <span style={{ flex: 1, minWidth: 240 }}>{error ?? NO_TARGET_MESSAGE}</span>
          {profileLink}
        </div>
      )}

      {deal && hasTarget && !error && loading && !grid && (
        <div style={{ fontSize: 12, color: palette.textMuted }}>Solving {'≤'}25 cells…</div>
      )}

      {deal && hasTarget && !error && grid && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <div
              role="table"
              aria-label="Max purchase price by exit cap rate and NOI growth"
              style={{ display: 'grid', gridTemplateColumns: `150px repeat(${grid.noi_growth_axis.length},minmax(132px,1fr))`, width: 'max-content', minWidth: '100%' }}
            >
              <div style={{ padding: '7px 12px', background: palette.inkNavy, color: palette.gridHeaderText, fontSize: 10, fontWeight: 700, letterSpacing: '.04em', whiteSpace: 'nowrap' }}>
                EXIT CAP \ NOI GROWTH
              </div>
              {grid.noi_growth_axis.map((g) => (
                <div key={g} style={{ padding: '7px 12px', background: palette.inkNavy, color: palette.gridHeaderText, fontSize: 10.5, fontWeight: 600, textAlign: 'right', borderLeft: `1px solid ${palette.gridHeaderDivider}`, whiteSpace: 'nowrap' }}>
                  {fmtPct(g, 1)}
                </div>
              ))}
              {rows.map((row) => (
                <RowCells key={row.cap} cap={row.cap} cells={row.cells} rooms={grid.rooms} />
              ))}
            </div>
          </div>
          <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
            Each cell solves for the maximum purchase price that still meets {bothHurdles ? 'both ' : ''}the {hurdleText} hurdle{bothHurdles ? 's' : ''}; the tag names the binding constraint and “—” means no price clears the hurdles at that combination. Sensitivities answers how returns move; Pricing answers how much you can pay. NOI growth re-tilts the model’s NOI series relative to the base growth assumption ({fmtPct(grid.base_noi_growth_pct, 1)}); the outlined base cell equals the Max Price Solver headline.
          </div>
        </>
      )}
    </div>
  );
}

function RowCells({ cap, cells, rooms }: { cap: number; cells: (PricingMaxPriceGridCell | undefined)[]; rooms: number | null }) {
  return (
    <>
      <div style={{ padding: '7px 12px', borderBottom: `1px solid ${palette.hairlineRow}`, fontSize: 12, color: palette.ink, fontWeight: 600, background: palette.surfaceTint, whiteSpace: 'nowrap' }}>
        {fmtPct(cap, 2)}
      </div>
      {cells.map((c, i) => {
        if (!c) return <div key={i} style={{ padding: '7px 12px', borderBottom: `1px solid ${palette.hairlineRow}` }} />;
        const solvable = c.max_price != null;
        const perKey = solvable && rooms && rooms > 0 ? `${money((c.max_price as number) / rooms)} / key` : '';
        const title = solvable
          ? `${fmtPct(c.exit_cap_pct, 2)} exit cap · ${fmtPct(c.noi_growth_pct, 1)} NOI growth — binding constraint ${BINDING_LABEL[c.binding_constraint]}`
          : 'No price clears the hurdles at this combination';
        return (
          <div
            key={i}
            role="cell"
            title={title}
            data-base={c.is_base ? 'true' : undefined}
            data-binding={solvable ? c.binding_constraint : undefined}
            style={{
              padding: '7px 12px', borderBottom: `1px solid ${palette.hairlineRow}`, borderLeft: `1px solid ${palette.hairlineRow}`,
              textAlign: 'right', fontSize: 12, fontVariantNumeric: 'tabular-nums',
              color: c.is_base ? prov.black : solvable ? prov.gray : prov.muted,
              fontWeight: c.is_base ? 700 : 400,
              background: c.is_base ? 'oklch(97% 0.03 250)' : 'transparent',
              boxShadow: c.is_base ? 'inset 0 0 0 2px #2f4a8c' : 'none',
              whiteSpace: 'nowrap', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2,
            }}
          >
            <span>{solvable ? mm(c.max_price as number) : '—'}</span>
            <span style={{ fontSize: 10.5, fontWeight: 400, color: palette.textMuted }}>
              {solvable ? `${perKey}${perKey ? ' · ' : ''}${BINDING_LABEL[c.binding_constraint]}` : ''}
            </span>
          </div>
        );
      })}
    </>
  );
}

const linkBtn: CSSProperties = {
  background: 'none', border: 'none', padding: 0, fontFamily: 'inherit', fontSize: 11.5,
  color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
};
