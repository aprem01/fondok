'use client';
/**
 * Sam #6 — Market / Submarket on the Overview's Investment Profile, editable
 * after creation (the wizard captured them once; there was no way to fix a
 * wrong market later).
 *
 * Market and Submarket are deal columns (`deals.market`, `deals.submarket`,
 * plus `deals.str_market_id` when the market was picked from STR's loaded
 * list). They feed no engine, so a save never re-runs the model and needs no
 * justification note. Suggestions come from `useStrMarkets` (STR's list when
 * loaded, else the name-only STR market names for Market and nothing for
 * Submarket) and are fetched only while the editor is open. Blank renders
 * "—"; "Save" writes, "Cancel" restores.
 */
import { useState, type CSSProperties } from 'react';
import { ProvenanceDot, palette, prov } from '@/components/design';
import { tidyLocation } from '@/lib/markets';
import { useStrMarkets } from '@/lib/hooks/useStrMarkets';

export interface MarketSubmarketPatch {
  market: string | null;
  str_market_id: string | null;
  submarket: string | null;
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

export function MarketSubmarketField({
  market, submarket, strMarketId, onSave, disabled = false,
}: {
  market: string | null | undefined;
  submarket: string | null | undefined;
  strMarketId: string | null | undefined;
  /** Resolves `false` when the save was refused (the editor stays open). */
  onSave: (patch: MarketSubmarketPatch) => Promise<boolean>;
  disabled?: boolean;
}) {
  const savedMarket = market?.trim() ?? '';
  const savedSub = submarket?.trim() ?? '';
  const savedId = strMarketId?.trim() ?? '';
  const [editing, setEditing] = useState(false);

  const idLine = savedId
    ? `STR market · STR id ${savedId}`
    : savedMarket
      ? 'Analyst-entered · no STR id'
      : 'Not set';

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
  initialMarket, initialSub, initialId, onSave, onDone,
}: {
  initialMarket: string; initialSub: string; initialId: string;
  onSave: (patch: MarketSubmarketPatch) => Promise<boolean>;
  onDone: () => void;
}) {
  const [draftMarket, setDraftMarket] = useState(initialMarket);
  const [draftSub, setDraftSub] = useState(initialSub);
  const [draftId, setDraftId] = useState(initialId);
  const [saving, setSaving] = useState(false);
  const sugg = useStrMarkets(draftMarket, draftId || null, draftSub);

  const save = async () => {
    setSaving(true);
    let pick = { market: tidyLocation(draftMarket), strMarketId: draftId || null };
    if (pick.market && !pick.strMarketId) pick = await sugg.resolve(draftMarket);
    const ok = await onSave({
      market: pick.market || null,
      str_market_id: pick.market ? pick.strMarketId : null,
      submarket: tidyLocation(draftSub) || null,
    });
    setSaving(false);
    if (ok) onDone();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
      <input
        aria-label="Market"
        placeholder="Market"
        value={draftMarket}
        list="overview-market-suggestions"
        autoComplete="off"
        onChange={(e) => { setDraftMarket(e.target.value); setDraftId(''); }}
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
        onChange={(e) => setDraftSub(e.target.value)}
        style={inputStyle}
      />
      <datalist id="overview-market-suggestions" data-testid="overview-market-suggestions">
        {sugg.marketOptions.map((o) => (
          <option key={`${o.strMarketId ?? 'name'}:${o.name}`} value={o.name}>{o.detail}</option>
        ))}
      </datalist>
      <datalist id="overview-submarket-suggestions" data-testid="overview-submarket-suggestions">
        {sugg.submarketOptions.map((n) => <option key={n} value={n} />)}
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
        {`${draftId ? `STR id ${draftId}` : 'No STR id'} · ${sugg.marketSourceLabel}`}
        {sugg.referenceLoaded ? '' : ' · Submarket: no suggestions until STR’s list is loaded'}
      </div>
    </div>
  );
}
