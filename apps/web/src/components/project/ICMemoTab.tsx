'use client';

/**
 * IC Memo (FON-54) — the investor-facing one-pager.
 *
 * This is NOT another export screen. When the user lands here they see a
 * complete, IC-ready investment recommendation the moment the model has run:
 *   1. Deal Summary — the key facts + underwriting outputs.
 *   2. IC Recommendation — a verdict (Proceed / Proceed with Conditions / Do
 *      Not Proceed), an investment thesis, key highlights, and key risks —
 *      all synthesized *dynamically from this deal's live engine outputs*, not
 *      hard-coded from any example.
 *   3. Configure IC Memo — a secondary workflow: pick the level of detail and
 *      the sections, then generate / preview / download / share the formal PDF
 *      (the existing Export functionality, consolidated in here).
 *
 * The recommendation is derived client-side with a deterministic rule set over
 * the real returns (levered IRR, equity multiple, DSCR, debt yield, cap rate,
 * RevPAR). That keeps it instant and grounded in the numbers on screen — the
 * product should feel like it *analyzed the deal and prepared the analyst's
 * recommendation*, not like it generated a file.
 */

import {
  CheckCircle2, AlertTriangle, XCircle, TrendingUp, ShieldCheck,
  Sliders, FileText, ListChecks, ClipboardList, BarChart3, ArrowRight, Loader2,
} from 'lucide-react';
import Link from 'next/link';
import { useState, useEffect, useMemo } from 'react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { useEngineOutputs, getEngineField } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useVariance } from '@/lib/hooks/useVariance';
import { api } from '@/lib/api';
import type { Project } from '@/lib/mockData';
import ExportTab from './ExportTab';

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

  // Property type — prefer the brand + service level ("Kimpton · Upper
  // Upscale"), fall back to service level or the deal-type label.
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
    // FON-54 #4 — Underwriting Summary bridge inputs.
    totalRevenue: y1rev ? num(y1rev.total_revenue) : null,
    totalCost: num(g<number>('capital', 'total_capital')),
    loanAmount: num(g<number>('capital', 'debt_amount')),
    interestRate: num(g<number>('debt', 'interest_rate')),
    // The recommendation needs at least the levered return story to be honest.
    hasModel: leveredIrr != null || equityMultiple != null || revpar != null,
  };
}

// ───────────────────────── recommendation engine ─────────────────────────
// Deterministic institutional-hotel rule set. Thresholds are conservative
// value-add-acquisition defaults; Eshan can tune them later. The point is a
// defensible, numbers-grounded verdict — not a black box.

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
  let tone: Tone;
  if (failsHurdle) {
    verdict = 'Do Not Proceed';
    tone = 'red';
  } else if (clearsStrong) {
    verdict = 'Proceed';
    tone = 'green';
  } else {
    verdict = 'Proceed with Conditions';
    tone = 'amber';
  }

  const hold = m.holdYears ?? 5;
  const basis = m.pricePerKey != null ? `${fmtCurrency(m.pricePerKey, { compact: true })}/key` : null;

  // Thesis — one synthesized paragraph over the real numbers.
  const thesisBits: string[] = [];
  if (m.purchasePrice != null) {
    thesisBits.push(
      `At a ${fmtCurrency(m.purchasePrice, { compact: true })} basis${basis ? ` (${basis})` : ''}`,
    );
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

  // Highlights — pull the genuinely supportive facts.
  const highlights: string[] = [];
  if (irr != null) {
    highlights.push(
      `Levered IRR of ${fmtPct(irr, 1)} ${irr >= IRR_STRONG ? 'clears' : irr >= IRR_FLOOR ? 'approaches' : 'sits below'} a ${Math.round(IRR_STRONG * 100)}% target over a ${hold}-year hold.`,
    );
  }
  if (mult != null) {
    highlights.push(`Equity multiple of ${mult.toFixed(2)}x returns ${mult.toFixed(2)}× invested capital across the hold.`);
  }
  if (basis) {
    highlights.push(`Entry basis of ${basis}${m.purchasePrice != null ? ` (${fmtCurrency(m.purchasePrice, { compact: true })} total)` : ''}.`);
  }
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
    highlights.push(
      `Year-1 debt yield of ${fmtPct(m.debtYield, 1)}${m.debtYield >= 0.1 ? ' clears typical lender floors' : ''}.`,
    );
  }

  // Risks — surface the honest ones, weighted by the actual numbers.
  const risks: string[] = [];
  if (irr != null && irr < IRR_STRONG) {
    risks.push(
      `Levered IRR of ${fmtPct(irr, 1)} is ${irr < IRR_FLOOR ? 'below' : 'near the low end of'} our return hurdle — limited margin for underwriting slippage.`,
    );
  }
  if (m.dscr != null && m.dscr < DSCR_STRONG) {
    risks.push(`Year-1 DSCR of ${m.dscr.toFixed(2)}x is tight; a NOI shortfall would pressure debt service.`);
  }
  if (m.renovation != null && m.renovation > 0) {
    risks.push(
      `Business plan requires ${fmtCurrency(m.renovation, { compact: true })} of renovation — execution, timing, and displacement risk to the ramp.`,
    );
  }
  if (m.occupancy != null && m.occupancy >= 0.8) {
    risks.push(
      `In-place occupancy of ${fmtPct(m.occupancy, 0)} leaves limited occupancy upside; RevPAR growth must come from ADR.`,
    );
  }
  if (m.revenueCagr != null && m.revenueCagr > 0) {
    risks.push(
      `Pro forma assumes a ${fmtPct(m.revenueCagr, 1)} revenue CAGR — a softer demand environment compresses returns.`,
    );
  }
  if (m.debtYield != null && m.debtYield < 0.1) {
    risks.push(`Debt yield of ${fmtPct(m.debtYield, 1)} is thin — refinance risk if rates stay elevated at exit.`);
  }
  // Exit sensitivity is always a real hotel risk; include it if we have room.
  if (risks.length < 3) {
    risks.push('Exit value depends on cap-rate assumptions at sale; cap-rate expansion would compress the equity multiple.');
  }
  if (risks.length < 3) {
    risks.push('Returns are levered to RevPAR performance; a demand or new-supply shock pressures the pro forma.');
  }

  return { verdict, tone, thesis, highlights: highlights.slice(0, 5), risks: risks.slice(0, 5) };
}

// ─────────────────────────────── view ────────────────────────────────────

type Detail = 'condensed' | 'default' | 'expanded';

interface SectionState {
  summary: boolean;
  thesis: boolean;
  highlights: boolean;
  risks: boolean;
  diligence: boolean;
  underwriting: boolean;
  scenario: boolean;
}

const SEV_TONE: Record<string, Tone> = { CRITICAL: 'red', WARN: 'amber', INFO: 'green' };

const VERDICT_ICON: Record<Tone, typeof CheckCircle2> = {
  green: CheckCircle2,
  amber: AlertTriangle,
  red: XCircle,
};

const VERDICT_STYLE: Record<Tone, string> = {
  green: 'border-l-success-500 bg-success-50/50',
  amber: 'border-l-warn-500 bg-warn-50/50',
  red: 'border-l-danger-500 bg-danger-50/50',
};

export default function ICMemoTab({ project }: { project: Project }) {
  const dealId = String(project.id);
  const { outputs } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);

  const metrics = useMemo(() => extractMetrics(outputs, deal, project), [outputs, deal, project]);
  const rec = useMemo(() => buildRecommendation(metrics), [metrics]);

  const variance = useVariance(dealId);

  const [detail, setDetail] = useState<Detail>('default');
  const [sections, setSections] = useState<SectionState>({
    summary: true,
    thesis: true,
    highlights: true,
    risks: true,
    diligence: true,
    underwriting: true,
    scenario: true,
  });

  // Diligence items — the highest-$-impact broker-vs-T-12 variance flags,
  // surfaced compactly so the memo carries the "diligence items" the IC needs
  // without becoming the full Analysis dashboard.
  const diligenceFlags = useMemo(() => {
    const flags = variance.flags ?? [];
    return [...flags]
      .sort((a, b) => Math.abs(b.noi_impact_usd) - Math.abs(a.noi_impact_usd))
      .slice(0, detail === 'condensed' ? 3 : 5);
  }, [variance.flags, detail]);

  // Level of detail trims the memo: condensed = verdict + thesis + top items;
  // expanded = the full set plus a supporting-analysis note.
  const maxHighlights = detail === 'condensed' ? 3 : 5;
  const maxRisks = detail === 'condensed' ? 2 : 5;

  const summaryRows: { label: string; value: string }[] = [
    { label: 'Property', value: metrics.propertyName },
    { label: 'Location', value: metrics.location ?? '—' },
    { label: 'Keys', value: metrics.keys != null ? String(metrics.keys) : '—' },
    { label: 'Type', value: metrics.propertyType ?? '—' },
    { label: 'Purchase Price', value: metrics.purchasePrice != null ? fmtCurrency(metrics.purchasePrice, { compact: true }) : '—' },
    { label: 'Price / Key', value: metrics.pricePerKey != null ? fmtCurrency(metrics.pricePerKey, { compact: true }) : '—' },
    { label: 'RevPAR', value: metrics.revpar != null ? fmtCurrency(metrics.revpar) : '—' },
    { label: 'NOI (Y1)', value: metrics.noi != null ? fmtCurrency(metrics.noi, { compact: true }) : '—' },
    { label: 'Cap Rate', value: metrics.capRate != null ? fmtPct(metrics.capRate, 1) : '—' },
    { label: 'Levered IRR', value: metrics.leveredIrr != null ? fmtPct(metrics.leveredIrr, 1) : '—' },
    { label: 'Equity Multiple', value: metrics.equityMultiple != null ? `${metrics.equityMultiple.toFixed(2)}x` : '—' },
    { label: 'Y1 DSCR', value: metrics.dscr != null ? `${metrics.dscr.toFixed(2)}x` : '—' },
  ];

  const VerdictIcon = rec ? VERDICT_ICON[rec.tone] : ShieldCheck;

  return (
    <div className="space-y-5">
      {/* ── The IC-ready one-pager ─────────────────────────────────────── */}
      <Card className="p-0 overflow-hidden">
        <div className="bg-ink-900 text-white px-6 py-4 flex items-center justify-between">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-[0.14em] text-white/60">Investment Committee Memo</div>
            <h2 className="text-[18px] font-semibold truncate">{metrics.propertyName}</h2>
            <div className="text-[12px] text-white/70 mt-0.5">
              {[metrics.location, metrics.propertyType, metrics.keys != null ? `${metrics.keys} keys` : null]
                .filter(Boolean)
                .join('  ·  ') || 'Deal summary'}
            </div>
          </div>
          {rec && (
            <div className="shrink-0 text-right">
              <div className="text-[10px] uppercase tracking-[0.14em] text-white/60 mb-1">Recommendation</div>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[13px] font-semibold',
                  rec.tone === 'green' && 'bg-success-500 text-white',
                  rec.tone === 'amber' && 'bg-warn-500 text-white',
                  rec.tone === 'red' && 'bg-danger-500 text-white',
                )}
              >
                <VerdictIcon size={15} /> {rec.verdict}
              </span>
            </div>
          )}
        </div>

        {/* Deal Summary metric grid */}
        {sections.summary && (
          <div className="px-6 py-5 border-b border-border">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-4">
              {summaryRows.map((r) => (
                <div key={r.label}>
                  <div className="text-[10px] uppercase tracking-wide text-ink-400">{r.label}</div>
                  <div className="text-[15px] font-semibold text-ink-900 tabular-nums truncate" title={r.value}>
                    {r.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* IC Recommendation */}
        <div className="px-6 py-5">
          {!rec ? (
            <div className="text-center py-8">
              <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
                <FileText size={20} className="text-brand-500" />
              </div>
              <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Recommendation pending model run</h3>
              <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
                Once the underwriting engines have run on this deal, Fondok assembles the IC
                recommendation — verdict, thesis, highlights, and risks — from the live returns.
                Upload the financials and run the model to populate this memo.
              </p>
            </div>
          ) : (
            <div className="space-y-5">
              {sections.thesis && (
                <div className={cn('border-l-4 rounded-r-md p-4', VERDICT_STYLE[rec.tone])}>
                  <div className="flex items-center gap-2 mb-1.5">
                    <VerdictIcon
                      size={15}
                      className={
                        rec.tone === 'green' ? 'text-success-600' : rec.tone === 'amber' ? 'text-warn-700' : 'text-danger-600'
                      }
                    />
                    <h3 className="text-[13px] font-semibold text-ink-900">Investment Thesis</h3>
                    <Badge tone={rec.tone}>{rec.verdict}</Badge>
                  </div>
                  <p className="text-[12.5px] text-ink-700 leading-relaxed">{rec.thesis}</p>
                </div>
              )}

              <div className="grid md:grid-cols-2 gap-5">
                {sections.highlights && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <TrendingUp size={14} className="text-success-600" />
                      <h3 className="text-[13px] font-semibold text-ink-900">Key Highlights</h3>
                    </div>
                    <ul className="space-y-2">
                      {rec.highlights.slice(0, maxHighlights).map((h, i) => (
                        <li key={i} className="flex gap-2 text-[12.5px] text-ink-700 leading-relaxed">
                          <CheckCircle2 size={14} className="text-success-500 shrink-0 mt-0.5" />
                          <span>{h}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {sections.risks && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <AlertTriangle size={14} className="text-warn-700" />
                      <h3 className="text-[13px] font-semibold text-ink-900">Key Risks &amp; Considerations</h3>
                    </div>
                    <ul className="space-y-2">
                      {rec.risks.slice(0, maxRisks).map((r, i) => (
                        <li key={i} className="flex gap-2 text-[12.5px] text-ink-700 leading-relaxed">
                          <AlertTriangle size={14} className="text-warn-600 shrink-0 mt-0.5" />
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* FON-54 #4 — Underwriting Summary: a concise bridge from the
                  recommendation back to the model, not a re-run of it. */}
              {sections.underwriting && (
                <div className="border-t border-border pt-4">
                  <h3 className="text-[13px] font-semibold text-ink-900 mb-2.5">Underwriting Summary</h3>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    <UwColumn
                      title="Operating"
                      rows={[
                        ['RevPAR', metrics.revpar != null ? fmtCurrency(metrics.revpar) : '—'],
                        ['Revenue (Y1)', metrics.totalRevenue != null ? fmtCurrency(metrics.totalRevenue, { compact: true }) : '—'],
                        ['NOI (Y1)', metrics.noi != null ? fmtCurrency(metrics.noi, { compact: true }) : '—'],
                        ['NOI Margin', metrics.noi != null && metrics.totalRevenue ? fmtPct(metrics.noi / metrics.totalRevenue, 0) : '—'],
                      ]}
                    />
                    <UwColumn
                      title="Capitalization"
                      rows={[
                        ['Purchase', metrics.purchasePrice != null ? fmtCurrency(metrics.purchasePrice, { compact: true }) : '—'],
                        ['Renovation', metrics.renovation != null ? fmtCurrency(metrics.renovation, { compact: true }) : '—'],
                        ['Total Cost', metrics.totalCost != null ? fmtCurrency(metrics.totalCost, { compact: true }) : '—'],
                        ['Equity', metrics.equity != null ? fmtCurrency(metrics.equity, { compact: true }) : '—'],
                      ]}
                    />
                    <UwColumn
                      title="Debt"
                      rows={[
                        ['Loan', metrics.loanAmount != null ? fmtCurrency(metrics.loanAmount, { compact: true }) : '—'],
                        ['LTV', metrics.loanAmount != null && metrics.purchasePrice ? fmtPct(metrics.loanAmount / metrics.purchasePrice, 0) : '—'],
                        ['Rate', metrics.interestRate != null ? fmtPct(metrics.interestRate, 2) : '—'],
                        ['DSCR', metrics.dscr != null ? `${metrics.dscr.toFixed(2)}x` : '—'],
                      ]}
                    />
                    <UwColumn
                      title="Returns"
                      rows={[
                        ['Unlevered IRR', metrics.unleveredIrr != null ? fmtPct(metrics.unleveredIrr, 1) : '—'],
                        ['Levered IRR', metrics.leveredIrr != null ? fmtPct(metrics.leveredIrr, 1) : '—'],
                        ['Equity Multiple', metrics.equityMultiple != null ? `${metrics.equityMultiple.toFixed(2)}x` : '—'],
                        ['Hold', metrics.holdYears != null ? `${metrics.holdYears} yrs` : '—'],
                      ]}
                    />
                  </div>
                </div>
              )}

              {detail === 'expanded' && (
                <div className="border-t border-border pt-4">
                  <h3 className="text-[13px] font-semibold text-ink-900 mb-2">Supporting Analysis</h3>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-[12.5px]">
                    <SupportStat label="Unlevered IRR" value={metrics.unleveredIrr != null ? fmtPct(metrics.unleveredIrr, 1) : '—'} />
                    <SupportStat label="Equity Invested" value={metrics.equity != null ? fmtCurrency(metrics.equity, { compact: true }) : '—'} />
                    <SupportStat label="Debt Yield (Y1)" value={metrics.debtYield != null ? fmtPct(metrics.debtYield, 1) : '—'} />
                    <SupportStat label="Hold Period" value={metrics.holdYears != null ? `${metrics.holdYears} yrs` : '—'} />
                  </div>
                  <p className="text-[11.5px] text-ink-400 mt-3 leading-relaxed">
                    Full underwriting detail, sensitivity tables, and the source-document appendix are
                    included in the Expanded PDF export below.
                  </p>
                </div>
              )}

              {/* FON-54 #5 — Scenario Summary: a concise Base / Downside /
                  Upside readout (IRR / EM / NOI / Exit) with a link to the
                  full Scenario Analysis tab. Never duplicates the sensitivity
                  experience — just the headline outcomes for the IC. */}
              {sections.scenario && (
                <ScenarioSummary dealId={dealId} />
              )}

              {/* Diligence items — top broker-vs-T-12 variance flags by $ impact.
                  Bridges the fuller "Analysis" intent without the dashboard. */}
              {sections.diligence && diligenceFlags.length > 0 && (
                <div className="border-t border-border pt-4">
                  <div className="flex items-center gap-2 mb-2">
                    <ClipboardList size={14} className="text-ink-500" />
                    <h3 className="text-[13px] font-semibold text-ink-900">Diligence Items &amp; Broker Variance</h3>
                    {variance.critical > 0 && <Badge tone="red">{variance.critical} critical</Badge>}
                    {variance.warn > 0 && <Badge tone="amber">{variance.warn} warn</Badge>}
                  </div>
                  <ul className="space-y-1.5">
                    {diligenceFlags.map((f) => (
                      <li key={f.flag_id} className="flex items-start gap-2 text-[12px] leading-relaxed">
                        <Badge tone={SEV_TONE[f.severity] ?? 'gray'}>
                          {f.severity[0] + f.severity.slice(1).toLowerCase()}
                        </Badge>
                        <span className="flex-1">
                          <span className="font-medium text-ink-900">{f.field_label}</span>
                          {Math.abs(f.noi_impact_usd) > 0 && (
                            <span className="text-ink-500">
                              {' · '}
                              {fmtCurrency(Math.abs(f.noi_impact_usd), { compact: true })} NOI impact
                            </span>
                          )}
                          <span className="block text-[11.5px] text-ink-500">{f.explanation}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </Card>

      {/* ── Configure IC Memo (secondary) ──────────────────────────────── */}
      <Card className="p-5">
        <div className="flex items-center gap-2 mb-1">
          <Sliders size={15} className="text-ink-500" />
          <h3 className="text-[14px] font-semibold text-ink-900">Configure IC Memo</h3>
        </div>
        <p className="text-[12.5px] text-ink-500 mb-4">
          Set the level of detail and the sections included, then generate the formal memo below. The
          one-pager above updates as you change these.
        </p>

        <div className="grid md:grid-cols-2 gap-6">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-ink-400 mb-2">Level of Detail</div>
            <div className="flex flex-col gap-1.5">
              {([
                ['condensed', 'Condensed', 'Key metrics + recommendation'],
                ['default', 'Default', 'Standard IC format'],
                ['expanded', 'Expanded', 'Full detail with appendix'],
              ] as [Detail, string, string][]).map(([id, label, desc]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setDetail(id)}
                  className={cn(
                    'flex items-start gap-2.5 text-left px-3 py-2 rounded-md border transition-colors',
                    detail === id ? 'border-brand-500 bg-brand-50' : 'border-border hover:bg-ink-50',
                  )}
                >
                  <span
                    className={cn(
                      'mt-0.5 w-3.5 h-3.5 rounded-full border-2 shrink-0',
                      detail === id ? 'border-brand-500 bg-brand-500' : 'border-ink-300',
                    )}
                  />
                  <span>
                    <span className="block text-[12.5px] font-medium text-ink-900">{label}</span>
                    <span className="block text-[11.5px] text-ink-500">{desc}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-ink-400 mb-2">
              <ListChecks size={12} /> Sections
            </div>
            <div className="flex flex-col gap-1.5">
              {([
                ['summary', 'Deal Summary'],
                ['thesis', 'Investment Thesis & Recommendation'],
                ['highlights', 'Key Highlights'],
                ['risks', 'Key Risks & Considerations'],
                ['underwriting', 'Underwriting Summary'],
                ['scenario', 'Scenario Summary'],
                ['diligence', 'Diligence & Variance'],
              ] as [keyof SectionState, string][]).map(([id, label]) => (
                <label
                  key={id}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-md border border-border hover:bg-ink-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={sections[id]}
                    onChange={(e) => setSections((s) => ({ ...s, [id]: e.target.checked }))}
                    className="accent-brand-500"
                  />
                  <span className="text-[12.5px] text-ink-800">{label}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* ── Generate / Preview / Download / Share ──────────────────────── */}
      <ExportTab project={project} />
    </div>
  );
}

// ─── FON-54 #5 — Scenario Summary ───────────────────────────────────
// A concise Base / Downside / Upside readout for the IC memo. Reads the
// same run-scoped compare endpoint the Scenario Analysis tab uses, but
// renders only headline outcomes (IRR / EM / NOI / Exit) and links out to
// the full sensitivity experience rather than duplicating it.
interface ScenarioKpi {
  id: string;
  name: string;
  isBase: boolean;
  irr: number | null;
  em: number | null;
  noi: number | null;
  exit: number | null;
}

function ScenarioSummary({ dealId }: { dealId: string }) {
  const [rows, setRows] = useState<ScenarioKpi[] | null>(null);
  const [loading, setLoading] = useState(true);
  // Demo / mock deals (numeric ids) have no worker-backed scenarios.
  const isMock = /^\d+$/.test(dealId);

  useEffect(() => {
    if (isMock) { setRows([]); setLoading(false); return; }
    const ac = new AbortController();
    let alive = true;
    (async () => {
      try {
        const scs = await api.scenarios.list(dealId, ac.signal);
        if (!alive) return;
        const base = scs?.find((s) => s.is_base);
        const others = (scs ?? []).filter((s) => !s.is_base).slice(0, 2);
        const ids = [base?.id, ...others.map((s) => s.id)].filter(
          (x): x is string => typeof x === 'string',
        );
        if (ids.length === 0) { setRows([]); return; }
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
          };
        });
        kpis.sort((a, b) => (a.isBase === b.isBase ? 0 : a.isBase ? -1 : 1));
        setRows(kpis);
      } catch {
        if (alive) setRows(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; ac.abort(); };
  }, [dealId, isMock]);

  const KPI: { key: keyof ScenarioKpi; label: string; fmt: (n: number) => string }[] = [
    { key: 'irr', label: 'Levered IRR', fmt: (n) => fmtPct(n, 1) },
    { key: 'em', label: 'Equity Multiple', fmt: (n) => `${n.toFixed(2)}x` },
    { key: 'noi', label: 'NOI (Y1)', fmt: (n) => fmtCurrency(n, { compact: true }) },
    { key: 'exit', label: 'Exit Value', fmt: (n) => fmtCurrency(n, { compact: true }) },
  ];

  return (
    <div className="border-t border-border pt-4">
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2">
          <BarChart3 size={14} className="text-ink-500" />
          <h3 className="text-[13px] font-semibold text-ink-900">Scenario Summary</h3>
        </div>
        <Link
          href={`/projects/${dealId}?tab=scenarios`}
          className="inline-flex items-center gap-1 text-[11.5px] font-medium text-brand-700 hover:text-brand-800"
        >
          View Scenario Analysis <ArrowRight size={12} aria-hidden="true" />
        </Link>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-4 text-[12px] text-ink-500">
          <Loader2 size={13} className="animate-spin" /> Loading scenarios…
        </div>
      ) : !rows || rows.length === 0 ? (
        <p className="text-[12px] text-ink-500 py-2">
          No saved scenarios yet. Build a downside / upside case in{' '}
          <Link href={`/projects/${dealId}?tab=scenarios`} className="text-brand-700 hover:underline">
            Scenario Analysis
          </Link>{' '}
          to compare outcomes here.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">Outcome</th>
                {rows.map((r) => (
                  <th key={r.id} className="text-right font-medium pb-2">
                    <span className="inline-flex items-center gap-1 justify-end">
                      {r.isBase && (
                        <span className="text-[9.5px] uppercase tracking-wide bg-ink-100 text-ink-600 px-1 rounded">Base</span>
                      )}
                      {r.name}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {KPI.map((k) => {
                const baseRow = rows.find((r) => r.isBase) ?? rows[0];
                const baseVal = baseRow[k.key] as number | null;
                return (
                  <tr key={k.key} className="border-b border-border/50 last:border-0">
                    <td className="py-1.5 text-ink-700">{k.label}</td>
                    {rows.map((r) => {
                      const v = r[k.key] as number | null;
                      const delta = !r.isBase && v != null && baseVal != null
                        ? (v - baseVal) / Math.max(Math.abs(baseVal), 1e-9)
                        : null;
                      return (
                        <td key={r.id} className="py-1.5 text-right tabular-nums">
                          <div className="text-ink-900">{v == null ? '—' : k.fmt(v)}</div>
                          {delta != null && (
                            <div className={cn('text-[10px]', delta >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                              {delta >= 0 ? '+' : ''}{(delta * 100).toFixed(1)}%
                            </div>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-[11px] text-ink-400 mt-2">
            Headline outcomes for the active scenarios · deltas vs Base. Full sensitivity tables live in Scenario Analysis.
          </p>
        </div>
      )}
    </div>
  );
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

function SupportStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-400">{label}</div>
      <div className="text-[13.5px] font-semibold text-ink-900 tabular-nums">{value}</div>
    </div>
  );
}

// FON-54 #4 — one column of the Underwriting Summary bridge.
function UwColumn({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="rounded-md border border-border bg-ink-50/40 p-3">
      <div className="text-[10px] uppercase tracking-wide text-ink-400 mb-1.5">{title}</div>
      <div className="space-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-2 text-[12px]">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium text-ink-900 tabular-nums">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
