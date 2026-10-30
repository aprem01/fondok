'use client';
/**
 * Sam #6 / FON-96 — Market / Submarket on the Overview's Investment Profile,
 * editable after creation (the wizard captured them once; there was no way
 * to fix a wrong market later).
 *
 * Market and Submarket are deal columns (`deals.market`, `deals.submarket`,
 * plus the taxonomy keys `deals.str_market_id` / `deals.submarket_id` when
 * picked from the loaded CoStar list — the keys are Fondok's own and are
 * never shown). They feed no engine, so a save never re-runs the model and
 * needs no justification note. Suggestions come from `useStrMarkets` (the
 * CoStar list when available to this workspace, else the public STR market
 * names for Market and nothing for Submarket) and are fetched only while
 * the editor is open. The provenance line says "CoStar market" /
 * "CoStar submarket of {market}" / "Analyst-entered · not in the CoStar
 * list", and "matched from the Offering Memorandum" when the OM filled an
 * empty market on an exact match. Blank renders "—"; "Save" writes,
 * "Cancel" restores.
 */
import { useState, type CSSProperties } from 'react';
import { ProvenanceDot, palette, prov } from '@/components/design';
import { MARKET_PROVENANCE_LABEL, tidyLocation } from '@/lib/markets';
import { useStrMarkets } from '@/lib/hooks/useStrMarkets';

export interface MarketSubmarketPatch {
  market: string | null;
  str_market_id: string | null;
  submarket: string | null;
  submarket_id: string | null;
}

const labelStyle: CSSProperties = {
  fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.textMuted, textTransform: 'uppercase',
};
const inputStyle: CSSProperties = {
  flex: 1, minWidth: 0, fontSize: 11.5, fontFamily: 'inherit', fontWeight: 600, color: prov.blue,
  background: palette.surfaceTint, border: `1px solid ${palette.disabledBorder}`, borderRadius: 6,
  padding: '4px 8px',
};
const buttonStyle: CSSProperties = {
  fontSize: 11, fontFamily: 'inherit', border: `1px solid ${palette.disabledBorder}`, borderRadius: 5,
  background: '#fff', cursor: 'pointer', padding: '3px 9px',
};
const hintStyle: CSSProperties = { fontSize: 10, color: palette.textFaint, lineHeight: 1.3 };

/** The provenance line for a saved market / submarket — never the key. */
export function marketProvenanceLine(
  market: string, marketKey: string, submarketKey: string, marketSource: string | null | undefined,
): string {
  let line: string;
  if (marketKey) {
    line = submarketKey
      ? `${MARKET_PROVENANCE_LABEL.market} · ${MARKET_PROVENANCE_LABEL.submarketOf(market)}`
      : MARKET_PROVENANCE_LABEL.market;
  } else if (market) {
    line = MARKET_PROVENANCE_LABEL.analyst;
  } else {
    return MARKET_PROVENANCE_LABEL.notSet;
  }
  if (marketSource === 'om_exact_match') line = `${line} · ${MARKET_PROVENANCE_LABEL.fromOm}`;
  return line;
}

export function MarketSubmarketField({
  market, submarket, strMarketId, submarketId, marketSource, onSave, disabled = false,
}: {
  market: string | null | undefined;
  submarket: string | null | undefined;
  strMarketId: string | null | undefined;
  submarketId?: string | null | undefined;
  marketSource?: string | null | undefined;
  /** Resolves `false` when the save was refused (the editor stays open). */
  onSave: (patch: MarketSubmarketPatch) => Promise<boolean>;
  disabled?: boolean;
}) {
  const savedMarket = market?.trim() ?? '';
  const savedSub = submarket?.trim() ?? '';
  const savedId = strMarketId?.trim() ?? '';
  const savedSubId = submarketId?.trim() ?? '';
  const [editing, setEditing] = useState(false);

  const idLine = marketProvenanceLine(savedMarket, savedId, savedSubId, marketSource);

  return (
    <div data-testid="market-submarket-field" style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 5 }}>
        <ProvenanceDot state="assumption" size={7} title="Analyst input" />
        Market / Submarket
      </div>
      {editing ? (
        <MarketEditor
          initialMarket={savedMarket}
          initialSub={savedSub}
          initialId={savedId}
          initialSubId={savedSubId}
          onSave={onSave}
          onDone={() => setEditing(false)}
        />
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: prov.blue, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <span data-testid="market-value">{savedMarket || '—'}</span>
              <span style={{ color: palette.textFaint }}> / </span>
              <span data-testid="submarket-value">{savedSub || '—'}</span>
            </div>
            <button
              type="button"
              aria-label="Edit market and submarket"
              disabled={disabled}
              onClick={() => setEditing(true)}
              style={{ ...buttonStyle, cursor: disabled ? 'not-allowed' : 'pointer' }}
            >
              Edit
            </button>
          </div>
          <div data-testid="market-source-line" style={hintStyle}>{`${idLine} · descriptive, no model re-run`}</div>
        </>
      )}
    </div>
  );
}

function MarketEditor({
  initialMarket, initialSub, initialId, initialSubId, onSave, onDone,
}: {
  initialMarket: string; initialSub: string; initialId: string; initialSubId: string;
  onSave: (patch: MarketSubmarketPatch) => Promise<boolean>;
  onDone: () => void;
}) {
  const [draftMarket, setDraftMarket] = useState(initialMarket);
  const [draftSub, setDraftSub] = useState(initialSub);
  const [draftId, setDraftId] = useState(initialId);
  const [draftSubId, setDraftSubId] = useState(initialSubId);
  const [saving, setSaving] = useState(false);
  const sugg = useStrMarkets(draftMarket, draftId || null, draftSub);

  const save = async () => {
    setSaving(true);
    let pick = { market: tidyLocation(draftMarket), strMarketId: draftId || null };
    if (pick.market && !pick.strMarketId) pick = await sugg.resolve(draftMarket);
    // FON-96 — the submarket carries a key only on an exact, unique match
    // within the picked market; a typed submarket is saved without one.
    let sub = { submarket: tidyLocation(draftSub), submarketKey: draftSubId || null };
    if (sub.submarket && !sub.submarketKey && pick.strMarketId) {
      const r = await sugg.resolveSubmarket(draftSub, pick.strMarketId);
      sub = { submarket: r.submarket, submarketKey: r.submarketKey };
    }
    const ok = await onSave({
      market: pick.market || null,
      str_market_id: pick.market ? pick.strMarketId : null,
      submarket: sub.submarket || null,
      submarket_id: sub.submarket && pick.strMarketId ? sub.submarketKey : null,
    });
    setSaving(false);
    if (ok) onDone();
  };

  const editorLine = draftId
    ? (draftSubId
      ? `${MARKET_PROVENANCE_LABEL.market} · ${MARKET_PROVENANCE_LABEL.submarketOf(draftMarket)}`
      : MARKET_PROVENANCE_LABEL.market)
    : (draftMarket.trim()
      ? (sugg.referenceLoaded ? MARKET_PROVENANCE_LABEL.analyst : MARKET_PROVENANCE_LABEL.analystNoList)
      : MARKET_PROVENANCE_LABEL.notSet);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
      <input
        aria-label="Market"
        placeholder="Market"
        value={draftMarket}
        list="overview-market-suggestions"
        autoComplete="off"
        onChange={(e) => { setDraftMarket(e.target.value); setDraftId(''); setDraftSubId(''); }}
        onBlur={(e) => {
          void sugg.resolve(e.target.value).then((r) => {
            setDraftMarket(r.market);
            setDraftId(r.strMarketId ?? '');
          });
        }}
        style={inputStyle}
      />
      <input
        aria-label="Submarket"
        placeholder="Submarket"
        value={draftSub}
        list="overview-submarket-suggestions"
        autoComplete="off"
        onChange={(e) => { setDraftSub(e.target.value); setDraftSubId(''); }}
        onBlur={(e) => {
          void sugg.resolveSubmarket(e.target.value, draftId || null).then((r) => {
            setDraftSub(r.submarket);
            setDraftSubId(r.submarketKey ?? '');
            // A "{submarket} · {market}" pick with no market selected sets the
            // market too — the submarket names exactly one.
            if (!draftId && r.marketKey && r.market) {
              setDraftMarket(r.market);
              setDraftId(r.marketKey);
            }
          });
        }}
        style={inputStyle}
      />
      <datalist id="overview-market-suggestions" data-testid="overview-market-suggestions">
        {sugg.marketOptions.map((o) => (
          <option key={`${o.strMarketId ?? 'name'}:${o.name}`} value={o.name}>{o.detail}</option>
        ))}
      </datalist>
      <datalist id="overview-submarket-suggestions" data-testid="overview-submarket-suggestions">
        {sugg.submarketOptions.map((o) => <option key={o.submarketKey} value={o.value}>{o.label}</option>)}
      </datalist>
      <div style={{ display: 'flex', gap: 6 }}>
        <button type="button" onClick={() => void save()} disabled={saving} style={buttonStyle}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onDone} disabled={saving} style={buttonStyle}>
          Cancel
        </button>
      </div>
      <div data-testid="market-source-line" style={hintStyle}>
        {`${editorLine} · ${sugg.marketSourceLabel}`}
        {sugg.referenceLoaded ? '' : ' · Submarket: no suggestions (the CoStar submarket list is not available to this workspace)'}
      </div>
    </div>
  );
}
