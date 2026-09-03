'use client';

/**
 * IC Memo (FON-54 / FON-72 canonical rebuild) — the decision WORKSPACE.
 *
 * Canonical source: `design/canonical/IC Memo Tab.dc.html`. This is not a
 * read-only export screen: it is the workspace where the analyst records the
 * committee decision — the recommendation (a selectable verdict + confirm),
 * the case for it (an editable thesis, drag-reorderable highlights), the risks
 * against it (drag-reorderable risks), the diligence still open (resolve /
 * accept / review), and the deliverables. Every FIGURE stays anchored to the
 * canonical Base Case model run (read-only, consumed from the engines — FON-73
 * routes the memo context through the canonical run); only the NARRATIVE and
 * the DECISION are editable here.
 *
 * Grounding rules (CLAUDE.md):
 *   • all numbers come from the live engine outputs (`useEngineOutputs` +
 *     `getEngineField`) — never from the prototype's representative placeholders;
 *   • the verdict + thesis + highlights + risks DEFAULT to the deterministic,
 *     numbers-grounded synthesis (`buildRecommendation`) and can be overridden;
 *   • per-figure provenance dots read the real `/provenance` `state`.
 *
 * PERSISTENCE (see report / needs a worker+schema follow-up to be durable and
 * consumed): analyst edits persist through `api.deals.update` into
 * `deal.field_overrides` under the keys `memo_thesis`, `memo_thesis_edited`,
 * `memo_highlights`, `memo_risks`, `memo_recommendation_override`. The worker
 * currently accepts arbitrary `field_overrides` but does not yet read these
 * memo_* keys back into the memo generator — so persistence round-trips through
 * the deal record but is not yet consumed downstream. Diligence resolve/accept,
 * memo format, section toggles, preview + IC-ready state are session-local
 * workspace state (as in the canonical prototype).
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { fmtCurrency, fmtPct } from '@/lib/format';
import { useEngineOutputs, getEngineField } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useVariance } from '@/lib/hooks/useVariance';
import { useTraceGraph } from '@/lib/hooks/useValueTrace';
import { api, isWorkerConnected } from '@/lib/api';
import type { ValueState } from '@/lib/api';
import type { Project } from '@/lib/mockData';
import { ProvenanceDot, palette } from '@/components/design';

// ── canonical colours (design/canonical/IC Memo Tab.dc.html) ───────────────
const GREEN = 'oklch(45% 0.12 155)';
const AMBER = 'oklch(52% 0.13 65)';
const RED = 'oklch(50% 0.15 30)';
const NAVY = '#14213d';
const LINK = '#2f4a8c';

// ─────────────────────────── metric extraction ───────────────────────────
// Every figure is read from the same live engine outputs the Overview and
// Returns tabs render, so the memo can never disagree with the rest of the app.

interface DealMetrics {
  propertyName: string;
  location: string | null;
  propertyType: string | null;
  keys: number | null;
  purchasePrice: number | null;
  pricePerKey: number | null;
  noi: number | null;
  capRate: number | null;
  revpar: number | null;
  adr: number | null;
  occupancy: number | null;
  leveredIrr: number | null;
  unleveredIrr: number | null;
  equityMultiple: number | null;
  dscr: number | null;
  debtYield: number | null;
  equity: number | null;
  holdYears: number | null;
  renovation: number | null;
  revenueCagr: number | null;
  totalRevenue: number | null;
  totalCost: number | null;
  loanAmount: number | null;
  interestRate: number | null;
  noiMargin: number | null;
  exitValue: number | null;
  hasModel: boolean;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function extractMetrics(
  outputs: ReturnType<typeof useEngineOutputs>['outputs'],
  deal: ReturnType<typeof useDeal>['deal'],
  project: Project,
): DealMetrics {
  const g = <T,>(engine: Parameters<typeof getEngineField>[1], ...path: string[]) =>
    getEngineField<T>(outputs, engine, ...path);

  const revYears = (g<Record<string, unknown>[]>('revenue', 'years') ?? []) as Record<string, unknown>[];
  const expYears = (g<Record<string, unknown>[]>('expense', 'years') ?? []) as Record<string, unknown>[];
  const y1rev = revYears[0] ?? null;
  const y1exp = expYears[0] ?? null;

  const uses = (g<Record<string, unknown>[]>('capital', 'uses') ?? []) as Record<string, unknown>[];
  const purchaseUse = uses.find((u) => /purchase/i.test(String(u?.label ?? '')));
  const renovationUse = uses.find((u) => /renovat/i.test(String(u?.label ?? '')));

  const pricePerKey = num(g<number>('capital', 'price_per_key'));
  const keys = typeof deal?.keys === 'number' && deal.keys > 0 ? deal.keys : null;

  let purchasePrice = num(purchaseUse?.amount);
  if (purchasePrice == null && pricePerKey != null && keys != null) {
    purchasePrice = pricePerKey * keys;
  }

  const noi = y1exp ? num(y1exp.noi_institutional) ?? num(y1exp.noi) : null;
  const capRate = noi != null && purchasePrice ? noi / purchasePrice : null;

  const leveredIrr = num(g<number>('returns', 'levered_irr'));
  const equityMultiple = num(g<number>('returns', 'equity_multiple'));
  const revpar = y1rev ? num(y1rev.revpar) : null;
  const totalRevenue = y1rev ? num(y1rev.total_revenue) : null;

  const brand = deal?.brand ?? null;
  const service = deal?.service ?? null;
  const typeParts = [brand, service].filter(Boolean) as string[];
  const propertyType = typeParts.length ? typeParts.join(' · ') : service;

  return {
    propertyName: deal?.name ?? project.name ?? 'Untitled deal',
    location: deal?.city ?? null,
    propertyType,
    keys,
    purchasePrice,
    pricePerKey,
    noi,
    capRate,
    revpar,
    adr: y1rev ? num(y1rev.adr) : null,
    occupancy: y1rev ? num(y1rev.occupancy) : null,
    leveredIrr,
    unleveredIrr: num(g<number>('returns', 'unlevered_irr')),
    equityMultiple,
    dscr: num(g<number>('debt', 'year_one_dscr')),
    debtYield: num(g<number>('debt', 'year_one_debt_yield')),
    equity: num(g<number>('capital', 'equity_amount')),
    holdYears: num(g<number>('returns', 'hold_years')),
    renovation: num(renovationUse?.amount),
    revenueCagr: num(g<number>('revenue', 'total_revenue_cagr')),
    totalRevenue,
    totalCost: num(g<number>('capital', 'total_capital')),
    loanAmount: num(g<number>('capital', 'debt_amount')),
    interestRate: num(g<number>('debt', 'interest_rate')),
    noiMargin: noi != null && totalRevenue ? noi / totalRevenue : null,
    exitValue: num(g<number>('returns', 'gross_sale_price')),
    hasModel: leveredIrr != null || equityMultiple != null || revpar != null,
  };
}

// ───────────────────────── recommendation engine ─────────────────────────
// Deterministic institutional-hotel rule set — a defensible, numbers-grounded
// verdict + narrative that the analyst may then override.

type Verdict = 'Proceed' | 'Proceed with Conditions' | 'Do Not Proceed';
type Tone = 'green' | 'amber' | 'red';

interface Recommendation {
  verdict: Verdict;
  tone: Tone;
  thesis: string;
  highlights: string[];
  risks: string[];
}

const IRR_STRONG = 0.15;
const IRR_FLOOR = 0.12;
const MULT_STRONG = 1.8;
const MULT_FLOOR = 1.4;
const DSCR_STRONG = 1.3;
const DSCR_FLOOR = 1.2;

function toneForVerdict(v: Verdict): Tone {
  return v === 'Proceed' ? 'green' : v === 'Do Not Proceed' ? 'red' : 'amber';
}

const ASSESS_LABEL: Record<Tone, string> = {
  green: 'Clears Hurdles',
  amber: 'Clears with Conditions',
  red: 'Below Hurdles',
};
const TONE_COLOR: Record<Tone, string> = { green: GREEN, amber: AMBER, red: RED };

function buildRecommendation(m: DealMetrics): Recommendation | null {
  if (!m.hasModel) return null;

  const irr = m.leveredIrr;
  const mult = m.equityMultiple;
  const dscr = m.dscr;

  const failsHurdle =
    (irr != null && irr < IRR_FLOOR) ||
    (mult != null && mult < MULT_FLOOR) ||
    (dscr != null && dscr < DSCR_FLOOR);
  const clearsStrong =
    irr != null && irr >= IRR_STRONG &&
    mult != null && mult >= MULT_STRONG &&
    (dscr == null || dscr >= DSCR_STRONG);

  let verdict: Verdict;
  if (failsHurdle) verdict = 'Do Not Proceed';
  else if (clearsStrong) verdict = 'Proceed';
  else verdict = 'Proceed with Conditions';
  const tone = toneForVerdict(verdict);

  const hold = m.holdYears ?? 5;
  const basis = m.pricePerKey != null ? `${fmtCurrency(m.pricePerKey, { compact: true })}/key` : null;

  const thesisBits: string[] = [];
  if (m.purchasePrice != null) {
    thesisBits.push(`At a ${fmtCurrency(m.purchasePrice, { compact: true })} basis${basis ? ` (${basis})` : ''}`);
  }
  if (irr != null && mult != null) {
    thesisBits.push(
      `the deal underwrites to a ${fmtPct(irr, 1)} levered IRR and a ${mult.toFixed(2)}x equity multiple over a ${hold}-year hold`,
    );
  } else if (irr != null) {
    thesisBits.push(`the deal underwrites to a ${fmtPct(irr, 1)} levered IRR over a ${hold}-year hold`);
  }
  if (m.noi != null && m.capRate != null) {
    thesisBits.push(
      `entering on a ${fmtPct(m.capRate, 1)} going-in cap against ${fmtCurrency(m.noi, { compact: true })} of Year-1 NOI`,
    );
  }
  let thesis = thesisBits.length ? `${thesisBits.join(', ')}. ` : '';
  thesis +=
    verdict === 'Proceed'
      ? 'Returns clear our institutional hurdles with debt-service coverage in hand — the basis and cash flow support a recommendation to advance.'
      : verdict === 'Proceed with Conditions'
        ? 'Returns are attractive but sit inside the band where terms, capital plan, or downside protection should be firmed up before committing.'
        : 'On the current underwriting the deal does not clear our return or coverage hurdles; it should be repriced or restructured before it can be advanced.';

  const highlights: string[] = [];
  if (irr != null) {
    highlights.push(
      `Levered IRR of ${fmtPct(irr, 1)} ${irr >= IRR_STRONG ? 'clears' : irr >= IRR_FLOOR ? 'approaches' : 'sits below'} a ${Math.round(IRR_STRONG * 100)}% target over a ${hold}-year hold.`,
    );
  }
  if (mult != null) highlights.push(`Equity multiple of ${mult.toFixed(2)}x returns ${mult.toFixed(2)}× invested capital across the hold.`);
  if (basis) highlights.push(`Entry basis of ${basis}${m.purchasePrice != null ? ` (${fmtCurrency(m.purchasePrice, { compact: true })} total)` : ''}.`);
  if (m.dscr != null) {
    highlights.push(
      `Year-1 DSCR of ${m.dscr.toFixed(2)}x provides ${m.dscr >= 1.5 ? 'ample' : m.dscr >= DSCR_STRONG ? 'solid' : 'thin'} debt-service coverage.`,
    );
  }
  if (m.revpar != null) {
    highlights.push(
      `In-place RevPAR of ${fmtCurrency(m.revpar)}${m.occupancy != null ? ` at ${fmtPct(m.occupancy, 0)} occupancy` : ''}${m.adr != null ? ` (${fmtCurrency(m.adr)} ADR)` : ''}.`,
    );
  }
  if (m.debtYield != null && highlights.length < 5) {
    highlights.push(`Year-1 debt yield of ${fmtPct(m.debtYield, 1)}${m.debtYield >= 0.1 ? ' clears typical lender floors' : ''}.`);
  }

  const risks: string[] = [];
  if (irr != null && irr < IRR_STRONG) {
    risks.push(
      `Levered IRR of ${fmtPct(irr, 1)} is ${irr < IRR_FLOOR ? 'below' : 'near the low end of'} our return hurdle — limited margin for underwriting slippage.`,
    );
  }
  if (m.dscr != null && m.dscr < DSCR_STRONG) risks.push(`Year-1 DSCR of ${m.dscr.toFixed(2)}x is tight; a NOI shortfall would pressure debt service.`);
  if (m.renovation != null && m.renovation > 0) {
    risks.push(
      `Business plan requires ${fmtCurrency(m.renovation, { compact: true })} of renovation — execution, timing, and displacement risk to the ramp.`,
    );
  }
  if (m.occupancy != null && m.occupancy >= 0.8) {
    risks.push(`In-place occupancy of ${fmtPct(m.occupancy, 0)} leaves limited occupancy upside; RevPAR growth must come from ADR.`);
  }
  if (m.revenueCagr != null && m.revenueCagr > 0) {
    risks.push(`Pro forma assumes a ${fmtPct(m.revenueCagr, 1)} revenue CAGR — a softer demand environment compresses returns.`);
  }
  if (m.debtYield != null && m.debtYield < 0.1) risks.push(`Debt yield of ${fmtPct(m.debtYield, 1)} is thin — refinance risk if rates stay elevated at exit.`);
  if (risks.length < 3) risks.push('Exit value depends on cap-rate assumptions at sale; cap-rate expansion would compress the equity multiple.');
  if (risks.length < 3) risks.push('Returns are levered to RevPAR performance; a demand or new-supply shock pressures the pro forma.');

  return { verdict, tone, thesis, highlights: highlights.slice(0, 6), risks: risks.slice(0, 6) };
}

// ─────────────────────────── formatting helpers ───────────────────────────
const mm = (v: number | null): string => (v == null ? '—' : `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`);
const perKeyK = (v: number | null): string => (v == null ? '—' : `$${Math.round(v / 1000)}K`);
const whole$ = (v: number | null): string => (v == null ? '—' : `$${Math.round(v)}`);
const xMult = (v: number | null): string => (v == null ? '—' : `${v.toFixed(2)}x`);
const pctOr = (v: number | null, d = 1): string => (v == null ? '—' : fmtPct(v, d));

// ─────────────────────────── memo point model ─────────────────────────────
interface MemoPoint {
  t: string;
  ai: boolean;
}
type ListKey = 'highlights' | 'risks';

function asPoints(v: unknown): MemoPoint[] | null {
  if (!Array.isArray(v)) return null;
  const out: MemoPoint[] = [];
  for (const it of v) {
    if (it && typeof it === 'object' && typeof (it as MemoPoint).t === 'string') {
      out.push({ t: (it as MemoPoint).t, ai: !!(it as MemoPoint).ai });
    }
  }
  return out;
}

// ─────────────────────────── section toggles ──────────────────────────────
interface Sections {
  deal: boolean;
  thesis: boolean;
  hr: boolean;
  uw: boolean;
  scen: boolean;
  dil: boolean;
}
type MemoFormat = 'Condensed' | 'Standard' | 'Expanded';

// ═══════════════════════════════ view ═════════════════════════════════════

export default function ICMemoTab({ project }: { project: Project }) {
  const params = useParams();
  const routeId = typeof params?.id === 'string' ? params.id : null;
  const dealId = routeId ?? String(project.id);
  const isMock = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMock;

  const { outputs } = useEngineOutputs(dealId);
  const { deal, refresh } = useDeal(dealId);
  const variance = useVariance(dealId);

  const metrics = useMemo(() => extractMetrics(outputs, deal, project), [outputs, deal, project]);
  const rec = useMemo(() => buildRecommendation(metrics), [metrics]);

  // Computed-value provenance — per-figure dots read the real /provenance state.
  const capitalTrace = useTraceGraph('capital');
  const returnsTrace = useTraceGraph('returns');
  const debtTrace = useTraceGraph('debt');
  const expenseTrace = useTraceGraph('expense');
  const revenueTrace = useTraceGraph('revenue');
  const provState = useCallback(
    (engine: string, path: string, fallback: ValueState): ValueState => {
      const g =
        engine === 'capital' ? capitalTrace
          : engine === 'returns' ? returnsTrace
            : engine === 'debt' ? debtTrace
              : engine === 'expense' ? expenseTrace
                : revenueTrace;
      return g.get(path)?.state ?? fallback;
    },
    [capitalTrace, returnsTrace, debtTrace, expenseTrace, revenueTrace],
  );

  // ── editable / persisted workspace state ───────────────────────────────
  const [verdictOverride, setVerdictOverride] = useState<Verdict | null>(null);
  const [verdictConfirmed, setVerdictConfirmed] = useState(false);
  const [recMenuOpen, setRecMenuOpen] = useState(false);
  const [thesisText, setThesisText] = useState<string | null>(null);
  const [thesisEdited, setThesisEdited] = useState(false);
  const [thesisEditing, setThesisEditing] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [highlights, setHighlights] = useState<MemoPoint[] | null>(null);
  const [risks, setRisks] = useState<MemoPoint[] | null>(null);
  const [rowMenu, setRowMenu] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ list: ListKey; index: number } | null>(null);

  // ── session-local workspace state (as in the canonical prototype) ──────
  const [dil, setDil] = useState<Record<string, { status: 'Open' | 'Resolved' | 'Accepted'; details: boolean }>>({});
  const [format, setFormat] = useState<MemoFormat>('Standard');
  const [sections, setSections] = useState<Sections>({ deal: true, thesis: true, hr: true, uw: true, scen: true, dil: true });
  const [generated, setGenerated] = useState(false);
  const [ack, setAck] = useState(false);
  const [icReady, setIcReady] = useState(false);
  const [scenariosAvailable, setScenariosAvailable] = useState(false);

  const thesisRef = useRef<HTMLParagraphElement | null>(null);
  const rowRefs = useRef<Map<string, HTMLSpanElement>>(new Map());

  // Hydrate editors from persisted field_overrides once the deal is loaded.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current || !deal) return;
    const ov = (deal.field_overrides ?? {}) as Record<string, unknown>;
    if (typeof ov.memo_thesis === 'string') {
      setThesisText(ov.memo_thesis);
      setThesisEdited(!!ov.memo_thesis_edited);
    }
    const h = asPoints(ov.memo_highlights);
    if (h) setHighlights(h);
    const r = asPoints(ov.memo_risks);
    if (r) setRisks(r);
    if (
      ov.memo_recommendation_override === 'Proceed' ||
      ov.memo_recommendation_override === 'Proceed with Conditions' ||
      ov.memo_recommendation_override === 'Do Not Proceed'
    ) {
      setVerdictOverride(ov.memo_recommendation_override);
      setVerdictConfirmed(true);
    }
    hydratedRef.current = true;
  }, [deal]);

  // Persist a patch into deal.field_overrides (durable round-trip; see header
  // note — the worker does not yet consume these memo_* keys).
  const persist = useCallback(
    (patch: Record<string, unknown>) => {
      if (!liveMode) return; // mock / preview deals edit locally only
      const next = { ...((deal?.field_overrides ?? {}) as Record<string, unknown>), ...patch };
      api.deals
        .update(dealId, { field_overrides: next })
        .then(() => refresh())
        .catch(() => {});
    },
    [liveMode, deal, dealId, refresh],
  );

  // ── effective (derived-or-overridden) values ───────────────────────────
  const effVerdict: Verdict = verdictOverride ?? rec?.verdict ?? 'Proceed with Conditions';
  const effTone = toneForVerdict(effVerdict);
  const effThesis = thesisText ?? rec?.thesis ?? '';
  const effHighlights: MemoPoint[] = highlights ?? (rec ? rec.highlights.map((t) => ({ t, ai: true })) : []);
  const effRisks: MemoPoint[] = risks ?? (rec ? rec.risks.map((t) => ({ t, ai: true })) : []);

  // ── list editing ───────────────────────────────────────────────────────
  const current = useCallback(
    (list: ListKey): MemoPoint[] => (list === 'highlights' ? effHighlights : effRisks),
    [effHighlights, effRisks],
  );
  const commitList = useCallback(
    (list: ListKey, next: MemoPoint[]) => {
      if (list === 'highlights') setHighlights(next);
      else setRisks(next);
      persist({ [list === 'highlights' ? 'memo_highlights' : 'memo_risks']: next });
    },
    [persist],
  );
  const moveItem = (list: ListKey, i: number, dir: -1 | 1) => {
    const arr = current(list).slice();
    const j = i + dir;
    if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    commitList(list, arr);
    setRowMenu(null);
  };
  const removeItem = (list: ListKey, i: number) => {
    commitList(list, current(list).filter((_, k) => k !== i));
    setRowMenu(null);
  };
  const addItem = (list: ListKey) => {
    const label = list === 'highlights' ? 'New highlight — click to write.' : 'New risk — click to write.';
    commitList(list, current(list).concat([{ t: label, ai: false }]));
  };
  const editItemText = (list: ListKey, i: number, text: string) => {
    const arr = current(list).slice();
    if (!arr[i] || arr[i].t === text) return;
    arr[i] = { ...arr[i], t: text };
    commitList(list, arr);
  };
  const focusRow = (key: string) => {
    setRowMenu(null);
    requestAnimationFrame(() => {
      const el = rowRefs.current.get(key);
      if (el) {
        el.focus();
        const sel = window.getSelection?.();
        if (sel) {
          const range = document.createRange();
          range.selectNodeContents(el);
          range.collapse(false);
          sel.removeAllRanges();
          sel.addRange(range);
        }
      }
    });
  };
  const onDrop = (list: ListKey, index: number) => {
    if (!drag || drag.list !== list || drag.index === index) {
      setDrag(null);
      return;
    }
    const arr = current(list).slice();
    const [moved] = arr.splice(drag.index, 1);
    arr.splice(index, 0, moved);
    commitList(list, arr);
    setDrag(null);
  };

  // ── thesis editing ─────────────────────────────────────────────────────
  const toggleThesisEdit = () => {
    if (thesisEditing) {
      const text = thesisRef.current?.textContent?.trim() ?? effThesis;
      setThesisText(text);
      persist({ memo_thesis: text, memo_thesis_edited: thesisEdited });
    }
    setThesisEditing((e) => !e);
  };
  const onThesisInput = () => {
    if (!thesisEdited) setThesisEdited(true);
  };
  const regenThesis = () => {
    setRegenerating(true);
    setTimeout(() => {
      setRegenerating(false);
      setThesisText(null);
      setThesisEdited(false);
      setThesisEditing(false);
      persist({ memo_thesis: rec?.thesis ?? '', memo_thesis_edited: false });
    }, 500);
  };

  // ── verdict ───────────────────────────────────────────────────────────
  const selectVerdict = (v: Verdict) => {
    setVerdictOverride(v);
    setVerdictConfirmed(false);
    setRecMenuOpen(false);
    persist({ memo_recommendation_override: v });
  };
  const toggleConfirm = () => {
    setVerdictConfirmed((c) => !c);
    setRecMenuOpen(false);
  };

  // ── diligence (mapped from live variance flags) ────────────────────────
  interface DilItem {
    id: string;
    severity: 'Critical' | 'Minor';
    sevColor: string;
    title: string;
    body: string;
    impact: string;
    raw: string;
  }
  const dilItems: DilItem[] = useMemo(() => {
    const flags = variance.flags ?? [];
    return [...flags]
      .sort((a, b) => Math.abs(b.noi_impact_usd) - Math.abs(a.noi_impact_usd))
      .slice(0, 6)
      .map((f) => {
        const critical = f.severity === 'CRITICAL';
        return {
          id: f.flag_id,
          severity: critical ? 'Critical' : 'Minor',
          sevColor: critical ? RED : AMBER,
          title: f.field_label
            ? `${f.field_label} — broker vs T-12 variance`
            : 'Broker vs T-12 variance',
          body: f.explanation,
          impact:
            Math.abs(f.noi_impact_usd) > 0
              ? `Estimated NOI impact: ${fmtCurrency(Math.abs(f.noi_impact_usd), { compact: true })} · ${f.recommended_action}`
              : f.recommended_action,
          raw: `rule ${f.rule_id} · field ${f.metric}`,
        };
      });
  }, [variance.flags]);

  const dilStatus = (id: string) => dil[id] ?? { status: 'Open' as const, details: false };
  const openCritical = dilItems.filter((d) => d.severity === 'Critical' && dilStatus(d.id).status === 'Open').length;
  const setDilStatus = (id: string, status: 'Resolved' | 'Accepted') =>
    setDil((s) => ({ ...s, [id]: { status, details: false } }));
  const toggleDilDetails = (id: string) =>
    setDil((s) => ({ ...s, [id]: { status: (s[id] ?? { status: 'Open' }).status, details: !(s[id]?.details) } }));

  // ── IC-readiness checklist ─────────────────────────────────────────────
  const checklist = useMemo(() => {
    const items: { label: string; ok: boolean }[] = [
      { label: 'Base model run complete', ok: metrics.hasModel },
      { label: 'Required underwriting sections complete', ok: metrics.purchasePrice != null && metrics.noi != null },
      { label: 'Returns calculated', ok: metrics.leveredIrr != null || metrics.equityMultiple != null },
      { label: scenariosAvailable ? 'Scenario analysis available' : 'Scenario analysis not yet available', ok: scenariosAvailable },
      {
        label: openCritical
          ? `${openCritical} critical diligence item${openCritical === 1 ? '' : 's'} unresolved${ack ? ' (acknowledged)' : ''}`
          : 'Critical diligence items resolved',
        ok: openCritical === 0,
      },
      { label: generated ? 'IC memo previewed and reviewed' : 'IC memo not yet previewed', ok: generated },
    ];
    return items;
  }, [metrics, scenariosAvailable, openCritical, ack, generated]);
  const blockers = checklist.filter((c) => !c.ok).length;
  const canMark = blockers === 0 || (openCritical > 0 && ack && generated);

  // ── deal snapshot (per-figure provenance dots) ─────────────────────────
  interface Snap {
    label: string;
    value: string;
    state: ValueState;
    src: string;
  }
  const overriddenPP = (deal?.field_overrides ?? {})['purchase_price'] != null;
  const snapshot: Snap[] = [
    { label: 'Purchase Price', value: mm(metrics.purchasePrice), state: provState('capital', 'purchase_price', overriddenPP ? 'assumption' : 'linked'), src: 'Investment' },
    { label: 'Price / Key', value: perKeyK(metrics.pricePerKey), state: provState('capital', 'price_per_key', 'calculated'), src: 'Investment' },
    { label: 'RevPAR', value: whole$(metrics.revpar), state: provState('revenue', 'years.0.revpar', 'linked'), src: 'Financials / Projections' },
    { label: 'NOI (Y1)', value: mm(metrics.noi), state: provState('expense', 'years.0.noi', 'calculated'), src: 'Financials / Projections' },
    { label: 'Going-In Cap Rate', value: pctOr(metrics.capRate, 2), state: provState('capital', 'entry_cap_rate', 'calculated'), src: 'Investment' },
    { label: 'Levered IRR', value: pctOr(metrics.leveredIrr), state: provState('returns', 'levered_irr', 'calculated'), src: 'Returns' },
    { label: 'Equity Multiple', value: xMult(metrics.equityMultiple), state: provState('returns', 'equity_multiple', 'calculated'), src: 'Returns' },
    { label: 'DSCR (Y1)', value: xMult(metrics.dscr), state: provState('debt', 'year_one_dscr', 'calculated'), src: 'Debt' },
    { label: 'Hold Period', value: metrics.holdYears != null ? `${metrics.holdYears} years` : '—', state: provState('returns', 'hold_years', 'assumption'), src: 'Investment' },
  ];

  // ── underwriting summary groups ────────────────────────────────────────
  const uwGroups: { title: string; link: string; tab: string; rows: [string, string][] }[] = [
    {
      title: 'Operating', link: 'View Financials →', tab: 'pl',
      rows: [
        ['RevPAR', whole$(metrics.revpar)],
        ['Revenue (Y1)', mm(metrics.totalRevenue)],
        ['NOI (Y1)', mm(metrics.noi)],
        ['NOI Margin', pctOr(metrics.noiMargin, 0)],
      ],
    },
    {
      title: 'Capitalization', link: 'View Investment →', tab: 'investment',
      rows: [
        ['Purchase Price', mm(metrics.purchasePrice)],
        ['Renovation / CapEx', mm(metrics.renovation)],
        ['Total Project Cost', mm(metrics.totalCost)],
        ['Initial Equity', mm(metrics.equity)],
      ],
    },
    {
      title: 'Debt', link: 'View Debt →', tab: 'debt',
      rows: [
        ['Loan Amount', mm(metrics.loanAmount)],
        ['LTV', metrics.loanAmount != null && metrics.purchasePrice ? pctOr(metrics.loanAmount / metrics.purchasePrice, 0) : '—'],
        ['Interest Rate', pctOr(metrics.interestRate, 2)],
        ['DSCR (Y1)', xMult(metrics.dscr)],
      ],
    },
    {
      title: 'Returns', link: 'View Returns →', tab: 'returns',
      rows: [
        ['Unlevered IRR', pctOr(metrics.unleveredIrr)],
        ['Levered IRR', pctOr(metrics.leveredIrr)],
        ['Equity Multiple', xMult(metrics.equityMultiple)],
        ['Hold Period', metrics.holdYears != null ? `${metrics.holdYears} years` : '—'],
      ],
    },
  ];

  const configSummary = `${format} · ${Object.values(sections).filter(Boolean).length} of 6 sections`;
  const formatNote: Record<MemoFormat, string> = {
    Condensed: 'Condensed — key metrics + recommendation',
    Standard: 'Standard — standard IC format',
    Expanded: 'Expanded — full detail + appendix',
  };
  const dilSummaryColor = openCritical ? RED : GREEN;
  const dilSummary = openCritical
    ? `${openCritical} critical diligence item${openCritical === 1 ? '' : 's'} remain open`
    : 'All critical diligence items resolved';

  // ═══════════════════════════ render ═════════════════════════════════════
  return (
    <div style={{ maxWidth: 1320, fontFamily: 'inherit' }} onClick={() => { setRecMenuOpen(false); setRowMenu(null); }}>
      {/* Intro — the decision-workspace framing (canonical intro card). */}
      <div style={{ ...card(), padding: '12px 16px', marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>IC Memo</span>
        <span style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}>
          The decision workspace: the recommendation, the case for it, the risks against it, and the deliverables the
          committee needs. Every figure is the canonical Base Case — nothing is underwritten here.
        </span>
      </div>

      {!rec ? (
        <PendingBanner metrics={metrics} />
      ) : (
        <>
          {/* ── Navy banner: identity · model assessment · IC recommendation ── */}
          <div
            style={{
              background: NAVY, borderRadius: 10, padding: '20px 24px', display: 'flex',
              justifyContent: 'space-between', alignItems: 'flex-start', gap: 32, flexWrap: 'wrap', marginBottom: 2,
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.1em', color: '#8b93a7', textTransform: 'uppercase' }}>
                Investment Committee Memo
              </span>
              <span style={{ fontSize: 23, fontWeight: 600, color: '#fff' }}>{metrics.propertyName}</span>
              <span style={{ fontSize: 12, color: '#9fb2df' }}>
                {[metrics.location, metrics.keys != null ? `${metrics.keys} keys` : null, metrics.propertyType]
                  .filter(Boolean)
                  .join('  ·  ') || 'Deal summary'}
              </span>
              {project.name && project.name !== metrics.propertyName && (
                <span style={{ fontSize: 11, color: '#6b7794' }}>Project: {project.name}</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 34, flexWrap: 'wrap' }}>
              {/* Model assessment — auto-derived from the numbers. */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.08em', color: '#8b93a7', textTransform: 'uppercase' }}>
                  Model assessment
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 16, fontWeight: 600, color: TONE_COLOR[rec.tone] }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: TONE_COLOR[rec.tone], display: 'inline-block' }} />
                  {ASSESS_LABEL[rec.tone]}
                </span>
                <span style={{ fontSize: 10.5, color: '#6b7794', maxWidth: 220, lineHeight: 1.45 }}>
                  Levered IRR {pctOr(metrics.leveredIrr)} vs. ≥{Math.round(IRR_STRONG * 100)}% target · DSCR {xMult(metrics.dscr)} vs. ≥{DSCR_FLOOR.toFixed(2)}x minimum
                </span>
              </div>
              {/* IC recommendation — selectable verdict + confirm. */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, position: 'relative' }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.08em', color: '#8b93a7', textTransform: 'uppercase' }}>
                  IC recommendation
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  aria-haspopup="listbox"
                  aria-expanded={recMenuOpen}
                  onClick={(e) => { e.stopPropagation(); setRecMenuOpen((o) => !o); }}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 16, fontWeight: 600, color: '#fff', cursor: 'pointer' }}
                >
                  {effVerdict}
                  <span style={{ fontSize: 11, color: '#8b93a7' }}>⌄</span>
                </span>
                <span style={{ fontSize: 10.5, color: verdictConfirmed ? '#7fbf9a' : '#c8a86b' }}>
                  {verdictConfirmed ? '✓ Analyst confirmed' : 'Awaiting analyst confirmation'}
                </span>
                {recMenuOpen && (
                  <div
                    role="listbox"
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: 'absolute', top: 62, right: 0, zIndex: 30, background: '#fff', border: '1px solid #e2e1dc',
                      borderRadius: 8, boxShadow: '0 10px 26px rgba(0,0,0,.18)', padding: 5, width: 230, display: 'flex', flexDirection: 'column',
                    }}
                  >
                    {(['Proceed', 'Proceed with Conditions', 'Do Not Proceed'] as Verdict[]).map((label) => (
                      <div
                        key={label}
                        role="option"
                        aria-selected={label === effVerdict}
                        onClick={() => selectVerdict(label)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: palette.ink,
                          fontWeight: label === effVerdict ? 600 : 400, padding: '8px 9px', borderRadius: 5, cursor: 'pointer',
                        }}
                      >
                        <span style={{ width: 11 }}>{label === effVerdict ? '✓' : ''}</span>
                        {label}
                      </div>
                    ))}
                    <div
                      onClick={toggleConfirm}
                      style={{ borderTop: '1px solid #f2f1ec', marginTop: 4, padding: '8px 9px', fontSize: 12, color: LINK, fontWeight: 600, cursor: 'pointer' }}
                    >
                      {verdictConfirmed ? 'Withdraw confirmation' : 'Confirm recommendation'}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── Deal snapshot (attached under the banner) ── */}
          {sections.deal && (
            <div style={{ ...card(), borderTop: 'none', borderRadius: '0 0 10px 10px', padding: '14px 24px 16px', marginBottom: 16 }}>
              <HeaderRow title="Deal snapshot" note="Base Case · Latest model run" />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(9, 1fr)', gap: 0 }}>
                {snapshot.map((m, i) => (
                  <div
                    key={m.label}
                    title={`${m.label} → ${m.src} · Base Case output, read-only here`}
                    style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '2px 14px 2px 0', borderLeft: i === 0 ? 'none' : '1px solid #f2f1ec', paddingLeft: i === 0 ? 0 : 14, cursor: 'help' }}
                  >
                    <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.04em', color: palette.textMuted, textTransform: 'uppercase', lineHeight: 1.3 }}>
                      {m.label}
                    </span>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 16, fontWeight: 600, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>
                      <ProvenanceDot state={m.state} size={7} />
                      {m.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Investment Thesis (editable) ── */}
          {sections.thesis && (
            <div style={{ ...card(), marginBottom: 16 }}>
              <div style={{ padding: '12px 18px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Investment Thesis</span>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: LINK, background: '#eef2fb', border: '1px solid #dbe3f5', borderRadius: 4, padding: '2px 6px' }}>
                    {thesisEdited ? 'Analyst edited' : 'AI drafted'}
                  </span>
                </span>
                <span style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                  <span role="button" tabIndex={0} onClick={toggleThesisEdit} style={{ fontSize: 11.5, color: LINK, fontWeight: 600, cursor: 'pointer' }}>
                    {thesisEditing ? 'Done editing' : 'Edit'}
                  </span>
                  <span role="button" tabIndex={0} onClick={regenThesis} style={{ fontSize: 11.5, color: palette.textSecondary, cursor: 'pointer' }}>
                    {regenerating ? 'Regenerating…' : 'Regenerate'}
                  </span>
                </span>
              </div>
              <div style={{ padding: '16px 18px' }}>
                <p
                  ref={thesisRef}
                  contentEditable={thesisEditing}
                  suppressContentEditableWarning
                  onInput={onThesisInput}
                  aria-label="Investment thesis"
                  style={{
                    margin: 0, fontSize: 13.5, lineHeight: 1.75, color: palette.hoverInk,
                    background: thesisEditing ? '#fbfbf9' : 'transparent', padding: thesisEditing ? '10px 12px' : 0,
                    borderRadius: 6, outline: 'none',
                  }}
                >
                  {effThesis}
                </p>
                <div style={{ fontSize: 11, color: palette.textFaint, marginTop: 10 }}>
                  Narrative only — editing this text never changes an underwriting assumption.
                </div>
              </div>
            </div>
          )}

          {/* ── Highlights / Risks (drag-reorderable, editable) ── */}
          {sections.hr && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
              <MemoList
                title="Key highlights"
                titleColor={GREEN}
                items={effHighlights}
                listKey="highlights"
                rowMenu={rowMenu}
                setRowMenu={setRowMenu}
                rowRefs={rowRefs}
                onMove={moveItem}
                onRemove={removeItem}
                onAdd={addItem}
                onEditText={editItemText}
                onFocusRow={focusRow}
                drag={drag}
                setDrag={setDrag}
                onDropAt={onDrop}
              />
              <MemoList
                title="Key risks & considerations"
                titleColor={AMBER}
                items={effRisks}
                listKey="risks"
                rowMenu={rowMenu}
                setRowMenu={setRowMenu}
                rowRefs={rowRefs}
                onMove={moveItem}
                onRemove={removeItem}
                onAdd={addItem}
                onEditText={editItemText}
                onFocusRow={focusRow}
                drag={drag}
                setDrag={setDrag}
                onDropAt={onDrop}
              />
            </div>
          )}

          {/* ── Underwriting summary (read-only, consumed from Base Case) ── */}
          {sections.uw && (
            <div style={{ ...card(), marginBottom: 16 }}>
              <HeaderRow title="Underwriting summary" note="Read-only · consumed from the Base Case" divider />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {uwGroups.map((grp, i) => (
                  <div key={grp.title} style={{ padding: '11px 16px 12px', borderLeft: i === 0 ? 'none' : '1px solid #f2f1ec' }}>
                    <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.07em', color: palette.textFaint, textTransform: 'uppercase', marginBottom: 2 }}>
                      {grp.title}
                    </div>
                    {grp.rows.map(([label, value]) => (
                      <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, fontSize: 12.5, height: 34, borderBottom: '1px solid #f7f6f3' }}>
                        <span style={{ color: '#7c8088' }}>{label}</span>
                        <span style={{ color: palette.ink, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
                      </div>
                    ))}
                    <Link href={`/projects/${dealId}?tab=${grp.tab}`} style={{ display: 'inline-block', fontSize: 11, color: LINK, fontWeight: 600, paddingTop: 9, textDecoration: 'none' }}>
                      {grp.link}
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Scenario summary ── */}
          {sections.scen && (
            <ScenarioSummary dealId={dealId} isMock={isMock} onLoaded={setScenariosAvailable} />
          )}

          {/* ── Diligence & open items ── */}
          {sections.dil && (
            <div style={{ ...card(), marginBottom: 16 }}>
              <div style={{ padding: '11px 16px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <span style={eyebrow()}>Diligence &amp; open items</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, fontWeight: 600, color: dilSummaryColor }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: dilSummaryColor, display: 'inline-block' }} />
                  {dilSummary}
                </span>
              </div>
              {dilItems.length === 0 ? (
                <div style={{ padding: '16px', fontSize: 12.5, color: palette.textSecondary }}>
                  No open diligence flags. Broker-vs-T-12 variance items surface here as the model detects them.
                </div>
              ) : (
                dilItems.map((d) => {
                  const st = dilStatus(d.id);
                  const open = st.status === 'Open';
                  const title = open
                    ? d.title
                    : st.status === 'Accepted'
                      ? `${d.title} — variance accepted`
                      : `${d.title} — resolved`;
                  return (
                    <div key={d.id} style={{ padding: '13px 16px', borderBottom: '1px solid #f7f6f3', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                      <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: d.sevColor, background: d.severity === 'Critical' ? 'oklch(97% 0.03 30)' : 'oklch(97% 0.03 75)', border: `1px solid ${d.severity === 'Critical' ? 'oklch(88% 0.06 30)' : 'oklch(90% 0.05 75)'}`, borderRadius: 4, padding: '3px 7px', whiteSpace: 'nowrap', marginTop: 1 }}>
                        {d.severity}
                      </span>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: open ? palette.ink : palette.eyebrow }}>{title}</span>
                        <span style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.6 }}>{d.body}</span>
                        <span style={{ fontSize: 11.5, color: palette.eyebrow }}>{d.impact}</span>
                        {open && (
                          <span style={{ display: 'flex', gap: 16, alignItems: 'center', paddingTop: 4, flexWrap: 'wrap' }}>
                            <Link href={`/projects/${dealId}`} style={{ fontSize: 11.5, color: LINK, fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>Review source →</Link>
                            <span role="button" tabIndex={0} onClick={() => setDilStatus(d.id, 'Resolved')} style={{ fontSize: 11.5, color: LINK, fontWeight: 600, cursor: 'pointer' }}>Resolve</span>
                            <span role="button" tabIndex={0} onClick={() => setDilStatus(d.id, 'Accepted')} style={{ fontSize: 11.5, color: palette.textSecondary, cursor: 'pointer' }}>Accept variance</span>
                            <span role="button" tabIndex={0} onClick={() => toggleDilDetails(d.id)} style={{ fontSize: 11.5, color: palette.textMuted, cursor: 'pointer' }}>
                              {st.details ? 'Hide technical detail' : 'Technical detail'}
                            </span>
                          </span>
                        )}
                        {st.details && (
                          <div style={{ background: '#fbfbf9', border: '1px solid #f0efeb', borderRadius: 6, padding: '9px 11px', marginTop: 6, fontSize: 11, color: palette.eyebrow, lineHeight: 1.6, fontFamily: 'ui-monospace,SFMono-Regular,Menlo,monospace' }}>
                            {d.raw}
                          </div>
                        )}
                      </div>
                      <span style={{ fontSize: 11, fontWeight: 600, color: open ? AMBER : GREEN, whiteSpace: 'nowrap', marginTop: 2 }}>{st.status}</span>
                    </div>
                  );
                })
              )}
            </div>
          )}

          {/* ── Configure IC memo ── */}
          <div style={{ ...card(), marginBottom: 16 }}>
            <HeaderRow title="Configure IC memo" note={configSummary} divider />
            <div style={{ padding: '13px 16px', display: 'grid', gridTemplateColumns: '300px 1fr auto', gap: 24, alignItems: 'start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.07em', color: palette.textFaint, textTransform: 'uppercase' }}>Memo format</span>
                <div style={{ display: 'inline-flex', border: '1px solid #e2e1dc', borderRadius: 6, overflow: 'hidden', width: 'fit-content' }}>
                  {(['Condensed', 'Standard', 'Expanded'] as MemoFormat[]).map((f) => {
                    const on = f === format;
                    return (
                      <button
                        key={f}
                        type="button"
                        onClick={() => { setFormat(f); setGenerated(false); }}
                        style={{ fontFamily: 'inherit', fontSize: 12, fontWeight: on ? 700 : 500, padding: '6px 14px', border: 'none', borderRight: '1px solid #e2e1dc', background: on ? NAVY : '#fff', color: on ? '#fff' : palette.textSecondary, cursor: 'pointer' }}
                      >
                        {f}
                      </button>
                    );
                  })}
                </div>
                <span style={{ fontSize: 11, color: palette.textMuted, lineHeight: 1.5 }}>{formatNote[format]}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.07em', color: palette.textFaint, textTransform: 'uppercase' }}>Include sections</span>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(150px, 1fr))', gap: '4px 18px' }}>
                  {([
                    ['deal', 'Deal Summary'],
                    ['thesis', 'Investment Thesis'],
                    ['hr', 'Highlights & Risks'],
                    ['uw', 'Underwriting Summary'],
                    ['scen', 'Scenario Summary'],
                    ['dil', 'Diligence & Open Items'],
                  ] as [keyof Sections, string][]).map(([k, label]) => {
                    const on = sections[k];
                    return (
                      <div
                        key={k}
                        role="checkbox"
                        aria-checked={on}
                        tabIndex={0}
                        onClick={() => { setSections((s) => ({ ...s, [k]: !s[k] })); setGenerated(false); }}
                        style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 12.5, color: on ? palette.ink : palette.textMuted, cursor: 'pointer', padding: '3px 0' }}
                      >
                        <span style={{ width: 14, height: 14, borderRadius: 4, border: on ? `1px solid ${NAVY}` : '1px solid #cfcec9', background: on ? NAVY : '#fff', color: '#fff', fontSize: 9.5, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                          {on ? '✓' : ''}
                        </span>
                        {label}
                      </div>
                    );
                  })}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setGenerated(true)}
                style={{ background: NAVY, color: '#fff', border: 'none', borderRadius: 6, padding: '9px 18px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap' }}
              >
                {generated ? '✓ Preview reviewed' : 'Preview Memo'}
              </button>
            </div>
          </div>

          {/* ── Export & share (Coming Soon) ── */}
          <div style={{ ...card(), marginBottom: 16 }}>
            <div style={{ padding: '11px 16px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={eyebrow()}>Export &amp; share</span>
                <span style={{ fontSize: 11.5, color: palette.textMuted }}>Deliverables will be generated from the latest completed Base Case model run.</span>
              </span>
              <span style={{ fontSize: 10.5, color: palette.textFaint }}>Base Case · Latest model run</span>
            </div>
            <div style={{ padding: '14px 16px', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
              {[
                ['Excel Model', '.xlsx', 'Complete underwriting model with assumptions, calculations and source references.'],
                ['IC Memo', '.pdf', 'Investment Committee memo based on the configuration above.'],
                ['Deal Presentation', '.pptx', 'Presentation-ready summary of the investment case, market, underwriting and returns.'],
              ].map(([title, ext, body]) => (
                <div key={title} style={{ border: '1px solid #eae9e4', borderRadius: 8, padding: '14px 15px', display: 'flex', flexDirection: 'column', gap: 7 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: palette.ink }}>{title}</span>
                    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.textMuted, background: palette.ground, borderRadius: 4, padding: '2px 6px' }}>{ext}</span>
                  </div>
                  <span style={{ fontSize: 12, color: palette.textSecondary, lineHeight: 1.55, flex: 1 }}>{body}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 2 }}>
                    <button disabled style={{ fontFamily: 'inherit', fontSize: 12, fontWeight: 600, padding: '7px 14px', borderRadius: 6, border: '1px solid #eae9e4', background: palette.ground, color: '#a8a7a2', cursor: 'default' }}>
                      🔒 Coming Soon
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ padding: '0 16px 14px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <button disabled style={{ fontFamily: 'inherit', fontSize: 12, fontWeight: 600, padding: '7px 14px', borderRadius: 6, border: '1px solid #eae9e4', background: palette.ground, color: '#a8a7a2', cursor: 'default' }}>
                🔒 Share with team · Coming Soon
              </button>
              <span style={{ fontSize: 11.5, color: palette.textFaint, fontWeight: 600 }}>Copy secure link · 🔒 Coming Soon</span>
            </div>
          </div>

          {/* ── Ready for Investment Committee ── */}
          <div style={{ ...card(), border: `1px solid ${icReady ? (openCritical > 0 ? 'oklch(85% 0.07 75)' : 'oklch(80% 0.08 155)') : '#eae9e4'}` }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>
                {icReady
                  ? openCritical > 0
                    ? `✓ IC Ready · ${openCritical} acknowledged critical item${openCritical === 1 ? '' : 's'}`
                    : '✓ This deal is IC Ready'
                  : 'Ready for Investment Committee?'}
              </span>
              <span style={{ fontSize: 11, color: palette.textMuted }}>
                {icReady
                  ? `Marked IC Ready · Base Case${openCritical > 0 ? ' · unresolved items acknowledged, not resolved' : ''}`
                  : `${blockers} item${blockers === 1 ? '' : 's'} outstanding`}
              </span>
            </div>
            <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 24, alignItems: 'center' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(240px, 1fr))', gap: '4px 24px' }}>
                {checklist.map((c) => (
                  <div key={c.label} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 12.5, color: c.ok ? palette.hoverInk : palette.ink, padding: '4px 0' }}>
                    <span style={{ width: 14, textAlign: 'center', color: c.ok ? GREEN : AMBER, fontSize: 12 }}>{c.ok ? '✓' : '⚠'}</span>
                    {c.label}
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9, alignItems: 'flex-end' }}>
                {!icReady && openCritical > 0 && (
                  <div
                    role="checkbox"
                    aria-checked={ack}
                    tabIndex={0}
                    onClick={() => setAck((a) => !a)}
                    style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 12, color: palette.hoverInk, cursor: 'pointer', maxWidth: 300, textAlign: 'left' }}
                  >
                    <span style={{ width: 14, height: 14, borderRadius: 4, border: ack ? `1px solid ${NAVY}` : '1px solid #cfcec9', background: ack ? NAVY : '#fff', color: '#fff', fontSize: 9.5, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      {ack ? '✓' : ''}
                    </span>
                    Acknowledge the unresolved critical items and proceed to committee
                  </div>
                )}
                <button
                  type="button"
                  disabled={!icReady && !canMark}
                  onClick={() => { if (icReady || canMark) setIcReady((r) => !r); }}
                  style={{
                    fontFamily: 'inherit', fontSize: 12.5, fontWeight: 600, padding: '9px 18px', borderRadius: 6, border: 'none',
                    background: icReady ? '#fff' : canMark ? NAVY : '#e6e5e0',
                    color: icReady ? palette.ink : canMark ? '#fff' : '#a8a7a2',
                    cursor: icReady || canMark ? 'pointer' : 'not-allowed', whiteSpace: 'nowrap',
                    boxShadow: icReady ? 'inset 0 0 0 1px #d8d7d2' : undefined,
                  }}
                >
                  {icReady ? 'Undo IC Ready' : 'Mark as IC Ready'}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────── shared chrome helpers ────────────────────────
function card(): React.CSSProperties {
  return { background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: 10 };
}
function eyebrow(): React.CSSProperties {
  return { fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', color: palette.eyebrow, textTransform: 'uppercase' };
}
function HeaderRow({ title, note, divider }: { title: string; note: string; divider?: boolean }) {
  return (
    <div
      style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap',
        ...(divider ? { padding: '11px 16px', borderBottom: '1px solid #f2f1ec' } : { marginBottom: 10 }),
      }}
    >
      <span style={eyebrow()}>{title}</span>
      <span style={{ fontSize: 10.5, color: palette.textFaint }}>{note}</span>
    </div>
  );
}

// ─────────────────────────── pending (no model) ───────────────────────────
function PendingBanner({ metrics }: { metrics: DealMetrics }) {
  return (
    <>
      <div style={{ background: NAVY, borderRadius: 10, padding: '20px 24px', marginBottom: 16 }}>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.1em', color: '#8b93a7', textTransform: 'uppercase' }}>
          Investment Committee Memo
        </div>
        <div style={{ fontSize: 23, fontWeight: 600, color: '#fff', marginTop: 5 }}>{metrics.propertyName}</div>
        <div style={{ fontSize: 12, color: '#9fb2df', marginTop: 4 }}>Model assessment · Pending model run</div>
      </div>
      <div style={{ ...card(), padding: '28px 24px', textAlign: 'center' }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: palette.ink, margin: '0 0 6px' }}>Recommendation pending model run</h3>
        <p style={{ fontSize: 12.5, color: palette.textSecondary, maxWidth: 520, margin: '0 auto', lineHeight: 1.6 }}>
          Once the underwriting engines have run on this deal, Fondok assembles the IC recommendation — verdict, thesis,
          highlights and risks — from the live Base Case returns. Upload the financials and run the model to populate this
          workspace.
        </p>
      </div>
    </>
  );
}

// ─────────────────────────── highlights / risks list ──────────────────────
interface MemoListProps {
  title: string;
  titleColor: string;
  items: MemoPoint[];
  listKey: ListKey;
  rowMenu: string | null;
  setRowMenu: (v: string | null) => void;
  rowRefs: React.MutableRefObject<Map<string, HTMLSpanElement>>;
  onMove: (list: ListKey, i: number, dir: -1 | 1) => void;
  onRemove: (list: ListKey, i: number) => void;
  onAdd: (list: ListKey) => void;
  onEditText: (list: ListKey, i: number, text: string) => void;
  onFocusRow: (key: string) => void;
  drag: { list: ListKey; index: number } | null;
  setDrag: (v: { list: ListKey; index: number } | null) => void;
  onDropAt: (list: ListKey, index: number) => void;
}

function MemoList(props: MemoListProps) {
  const { title, titleColor, items, listKey, rowMenu, setRowMenu, rowRefs, onMove, onRemove, onAdd, onEditText, onFocusRow, drag, setDrag, onDropAt } = props;
  return (
    <div style={{ ...card(), display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '11px 16px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', color: titleColor, textTransform: 'uppercase' }}>{title}</span>
        <span style={{ fontSize: 10.5, color: palette.textFaint }}>{items.length} points</span>
      </div>
      <div style={{ padding: '4px 16px 10px', flex: 1 }}>
        {items.map((it, i) => {
          const key = `${listKey}:${i}`;
          const open = rowMenu === key;
          return (
            <div
              key={key}
              draggable
              onDragStart={(e) => { setDrag({ list: listKey, index: i }); e.dataTransfer.effectAllowed = 'move'; }}
              onDragOver={(e) => { if (drag && drag.list === listKey) e.preventDefault(); }}
              onDrop={(e) => { e.preventDefault(); onDropAt(listKey, i); }}
              onDragEnd={() => setDrag(null)}
              style={{
                display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 6px 10px 0',
                borderBottom: '1px solid #f7f6f3', borderRadius: 6, position: 'relative',
                opacity: drag && drag.list === listKey && drag.index === i ? 0.5 : 1,
              }}
            >
              <span title="Drag to reorder" style={{ fontSize: 11, color: '#dedcd6', cursor: 'grab', paddingTop: 3, flexShrink: 0 }}>⠿</span>
              <span
                ref={(el) => {
                  if (el) rowRefs.current.set(key, el);
                  else rowRefs.current.delete(key);
                }}
                contentEditable
                suppressContentEditableWarning
                onBlur={(e) => onEditText(listKey, i, e.currentTarget.textContent?.trim() ?? '')}
                style={{ fontSize: 12.5, lineHeight: 1.6, color: palette.hoverInk, flex: 1, minWidth: 0, outline: 'none' }}
              >
                {it.t}
              </span>
              <span style={{ display: 'flex', gap: 9, alignItems: 'center', flexShrink: 0, paddingTop: 1 }}>
                {it.ai && (
                  <span title="Drafted by Fondok from the underwriting" style={{ fontSize: 9, fontWeight: 600, letterSpacing: '.04em', color: '#c3c2bd', whiteSpace: 'nowrap' }}>
                    AI drafted
                  </span>
                )}
                <span
                  role="button"
                  tabIndex={0}
                  title="More"
                  onClick={(e) => { e.stopPropagation(); setRowMenu(open ? null : key); }}
                  style={{ fontSize: 11, color: '#c3c2bd', cursor: 'pointer', lineHeight: 1 }}
                >
                  •••
                </span>
              </span>
              {open && (
                <div
                  onClick={(e) => e.stopPropagation()}
                  style={{ position: 'absolute', top: 30, right: 4, zIndex: 20, background: '#fff', border: '1px solid #e2e1dc', borderRadius: 7, boxShadow: '0 8px 20px rgba(0,0,0,.10)', padding: 4, width: 150, display: 'flex', flexDirection: 'column' }}
                >
                  <div onClick={() => onFocusRow(key)} style={menuItem()}>Edit text</div>
                  <div onClick={() => onMove(listKey, i, -1)} style={menuItem()}>Move up</div>
                  <div onClick={() => onMove(listKey, i, 1)} style={menuItem()}>Move down</div>
                  <div onClick={() => onRemove(listKey, i)} style={{ ...menuItem(), color: RED }}>Delete</div>
                </div>
              )}
            </div>
          );
        })}
        <div role="button" tabIndex={0} onClick={() => onAdd(listKey)} style={{ fontSize: 11.5, color: LINK, fontWeight: 600, cursor: 'pointer', padding: '10px 0 2px' }}>
          + Add point
        </div>
      </div>
    </div>
  );
}
function menuItem(): React.CSSProperties {
  return { fontSize: 12, color: palette.ink, padding: '7px 9px', borderRadius: 5, cursor: 'pointer' };
}

// ─────────────────────────── scenario summary ─────────────────────────────
// Reads the same run-scoped compare endpoint as Scenario Analysis, but renders
// only the headline Base / Downside / Upside outcomes for the IC. Never
// duplicates the sensitivity experience — the memo stays anchored to Base.
interface ScenarioKpi {
  id: string;
  name: string;
  isBase: boolean;
  irr: number | null;
  em: number | null;
  noi: number | null;
  exit: number | null;
  dscr: number | null;
}

function pathNum(obj: unknown, path: (string | number)[]): number | null {
  let cur: unknown = obj;
  for (const key of path) {
    if (cur == null) return null;
    if (typeof key === 'number') {
      if (!Array.isArray(cur)) return null;
      cur = cur[key];
    } else if (typeof cur === 'object') {
      cur = (cur as Record<string, unknown>)[key];
    } else {
      return null;
    }
  }
  return typeof cur === 'number' ? cur : null;
}

function ScenarioSummary({ dealId, isMock, onLoaded }: { dealId: string; isMock: boolean; onLoaded: (v: boolean) => void }) {
  const [rows, setRows] = useState<ScenarioKpi[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isMock || !isWorkerConnected()) { setRows([]); setLoading(false); onLoaded(false); return; }
    let alive = true;
    const ac = new AbortController();
    (async () => {
      try {
        const scs = await api.scenarios.list(dealId, ac.signal);
        if (!alive) return;
        const base = scs?.find((s) => s.is_base);
        const others = (scs ?? []).filter((s) => !s.is_base).slice(0, 2);
        const ids = [base?.id, ...others.map((s) => s.id)].filter((x): x is string => typeof x === 'string');
        if (ids.length === 0) { setRows([]); onLoaded(false); return; }
        const cmp = await api.scenarios.compare(dealId, ids);
        if (!alive) return;
        const kpis: ScenarioKpi[] = cmp.scenarios.map((c) => {
          const e = c.engines as Record<string, { outputs?: unknown }>;
          return {
            id: c.scenario_id,
            name: c.scenario_name,
            isBase: c.is_base,
            irr: pathNum(e.returns?.outputs, ['levered_irr']),
            em: pathNum(e.returns?.outputs, ['equity_multiple']),
            noi: pathNum(e.expense?.outputs, ['years', 0, 'noi']),
            exit: pathNum(e.returns?.outputs, ['gross_sale_price']),
            dscr: pathNum(e.debt?.outputs, ['year_one_dscr']),
          };
        });
        kpis.sort((a, b) => (a.isBase === b.isBase ? 0 : a.isBase ? -1 : 1));
        setRows(kpis);
        onLoaded(kpis.length > 0);
      } catch {
        if (alive) { setRows(null); onLoaded(false); }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; ac.abort(); };
  }, [dealId, isMock, onLoaded]);

  const METRICS: { key: keyof ScenarioKpi; label: string; fmt: (n: number) => string; delta: (d: number) => string }[] = [
    { key: 'irr', label: 'Levered IRR', fmt: (n) => fmtPct(n, 1), delta: (d) => `${d >= 0 ? '+' : '−'}${Math.abs(d * 100).toFixed(1)} pts` },
    { key: 'em', label: 'Equity Multiple', fmt: (n) => `${n.toFixed(2)}x`, delta: (d) => `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(2)}x` },
    { key: 'noi', label: 'Stabilized NOI', fmt: (n) => mm(n), delta: (d) => `${d >= 0 ? '+' : '−'}$${(Math.abs(d) / 1e6).toFixed(2)}M` },
    { key: 'exit', label: 'Exit Value', fmt: (n) => mm(n), delta: (d) => `${d >= 0 ? '+' : '−'}$${(Math.abs(d) / 1e6).toFixed(2)}M` },
    { key: 'dscr', label: 'Avg. DSCR', fmt: (n) => `${n.toFixed(2)}x`, delta: (d) => `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(2)}x` },
  ];

  const base = rows?.find((r) => r.isBase) ?? rows?.[0] ?? null;
  const cols = rows ?? [];

  return (
    <div style={{ ...card(), marginBottom: 16 }}>
      <div style={{ padding: '11px 16px', borderBottom: '1px solid #f2f1ec', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
        <span style={eyebrow()}>Scenario summary</span>
        <Link href={`/projects/${dealId}?tab=scenarios`} style={{ fontSize: 11, color: LINK, fontWeight: 600, textDecoration: 'none' }}>View Scenario Analysis →</Link>
      </div>
      <div style={{ padding: '2px 16px 10px' }}>
        {loading ? (
          <div style={{ padding: '14px 0', fontSize: 12, color: palette.textMuted }}>Loading scenarios…</div>
        ) : cols.length === 0 ? (
          <p style={{ fontSize: 12, color: palette.textSecondary, padding: '10px 0' }}>
            No saved scenarios yet. Build a downside / upside case in{' '}
            <Link href={`/projects/${dealId}?tab=scenarios`} style={{ color: LINK }}>Scenario Analysis</Link>{' '}to compare outcomes here.
          </p>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: `1.4fr repeat(${cols.length}, 1fr)`, fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.textFaint, textTransform: 'uppercase', padding: '10px 0 8px', borderBottom: '1px solid #f2f1ec' }}>
              <span>Metric</span>
              {cols.map((c) => (
                <span key={c.id} style={{ textAlign: 'right' }}>{c.isBase && c.name.toLowerCase() !== 'base' ? `${c.name} (Base)` : c.name}</span>
              ))}
            </div>
            {METRICS.map((m) => {
              const baseVal = base ? (base[m.key] as number | null) : null;
              return (
                <div key={String(m.key)} style={{ display: 'grid', gridTemplateColumns: `1.4fr repeat(${cols.length}, 1fr)`, alignItems: 'center', padding: '9px 0', borderBottom: '1px solid #f7f6f3', fontSize: 12.5 }}>
                  <span style={{ color: '#7c8088' }}>{m.label}</span>
                  {cols.map((c) => {
                    const v = c[m.key] as number | null;
                    const d = !c.isBase && v != null && baseVal != null ? v - baseVal : null;
                    const good = d != null && d >= 0;
                    return (
                      <span key={c.id} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                        <span style={{ color: c.isBase ? palette.ink : palette.hoverInk, fontWeight: c.isBase ? 600 : 400 }}>{v == null ? '—' : m.fmt(v)}</span>
                        {d != null && Math.abs(d) > 1e-9 && (
                          <span style={{ fontSize: 10.5, color: good ? GREEN : RED }}>{m.delta(d)}</span>
                        )}
                      </span>
                    );
                  })}
                </div>
              );
            })}
            <div style={{ fontSize: 10.5, color: palette.textFaint, lineHeight: 1.5, paddingTop: 9 }}>
              Saved scenarios are comparisons against Base. The memo and every export stay anchored to the Base Case.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
