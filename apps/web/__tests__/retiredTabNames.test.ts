/**
 * Sam #5 — the live app's tab names are the source of truth:
 *   Data Room · Overview · CAPEX · Market Comps · P&L · Financing · Partnership
 *   · Cash Flow · Investment Bridge · Scenario Analysis · IC Memo
 * (Returns is a section inside Overview; P&L's sub-tabs are Historical P&L /
 * Future P&L). The retired names — Investment, Debt, Market, Financials,
 * Historicals, Projections, the Returns / Export / Forecasting tabs — must not
 * reach a user through copy, tooltips, crash screens, CTAs, toasts, export
 * sheet names or the methodology page.
 *
 * This guard scans every source file under `src/` with comments stripped (tab
 * ids, URL slugs and identifiers such as `HistoricalsSection` / `?tab=debt`
 * never match: the patterns are capitalised display words). Anything that must
 * keep a retired word is listed in ALLOW with the reason.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { PL_SUB_LABEL, PROJECT_TABS, TAB_LABEL } from '@/lib/projectTabs';

const SRC = path.resolve(__dirname, '../src');

/** Retired user-facing tab names, as they would read in copy. */
const RETIRED: { re: RegExp; why: string }[] = [
  { re: /\b(?:Investment|Debt|Market|Financials|Historicals|Projections|Returns|Scenarios|Export|Forecasting) tabs?\b/, why: 'a retired tab name ("the Debt tab")' },
  { re: /\b(?:Investment|Debt|Market|Financials|Historicals|Projections)\s*(?:→|&rarr;|->)/, why: 'a retired tab in a navigation path ("Financials → …")' },
  { re: /(?:→|&rarr;)\s*(?:Historicals|Projections)\b/, why: 'a retired P&L sub-tab in a navigation path' },
  { re: /\bHistoricals\b/, why: 'the retired sub-tab name — it is the Historical P&L' },
  { re: /\bProjections\b/, why: 'the retired sub-tab name — it is the Future P&L' },
  { re: /tabName="(?:Investment|Debt|Market|Financials|Returns)"/, why: 'a crash screen naming a retired tab' },
  { re: /\bInvestment owns\b/, why: 'CAPEX owns those assumptions' },
  { re: /\b(?:source|sourceLabel|from|src):\s*'(?:Investment|Debt|Financials|Debt module)'/, why: 'a source chip naming a retired tab' },
  { re: /\bfrom Financials\b/, why: 'the Financials tab is P&L' },
  { re: /\b(?:editable on|Change it on|Edit it on) Debt\b/, why: 'the Debt tab is Financing' },
  { re: /\bAnalysis → Sources\b/, why: 'not a tab — link the Sources ledger instead' },
];

/** File (relative to src/) + the exact text that may keep a retired word. */
const ALLOW: { file: string; text: string; why: string }[] = [
  {
    file: 'lib/provenance.ts',
    text: "STR_MARKET_OVERRIDE_NOTE = 'STR comp-set market rates (Market tab)'",
    why: 'persisted protocol string: written onto field_overrides and matched by equality in the worker '
      + '(engine_runner.STR_MARKET_OVERRIDE_NOTE) to tag existing deals str_comp_set; renaming it would un-tag them',
  },
  {
    // FON-120 — the engineering methodology text is generated from
    // packages/methodology/sections/03-sources.md into this module.
    file: 'lib/methodology/engineering.generated.ts',
    text: 'STR comp-set market rates (Market tab)',
    why: 'quotes that persisted note verbatim, so an analyst can match it to the audit trail',
  },
  {
    file: 'components/project/pl/indexAnalysisModel.ts',
    text: 'the Base Year Projections print',
    why: 'owned by the Index Analysis fixer (Sam close-out); renamed in that change set',
  },
];

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(p);
  }
  return out;
}

/** Blank out block + line comments while keeping line numbers. */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, (m, pre: string) => pre + ' '.repeat(m.length - pre.length));
}

function findRetired(): string[] {
  const hits: string[] = [];
  for (const abs of walk(SRC)) {
    const rel = path.relative(SRC, abs).split(path.sep).join('/');
    stripComments(readFileSync(abs, 'utf8')).split('\n').forEach((line, i) => {
      for (const { re, why } of RETIRED) {
        if (!re.test(line)) continue;
        if (ALLOW.some((a) => a.file === rel && line.includes(a.text))) continue;
        hits.push(`${rel}:${i + 1} — ${why}\n    ${line.trim().slice(0, 160)}`);
      }
    });
  }
  return hits;
}

describe('Sam #5 — no retired tab names in user-facing copy', () => {
  it('apps/web/src carries none of the retired names outside comments / the allowlist', () => {
    expect(findRetired()).toEqual([]);
  });

  it('the scanner itself catches the strings Sam reported', () => {
    const sample = [
      '<ErrorBoundary tabName="Investment"><InvestmentTab /></ErrorBoundary>',
      'The Debt tab encountered an error.',
      'Fix them in place on the Historicals worksheet',
      'Open Historicals',
      "source: 'Investment',",
      'View Projections →',
      'values flow from Financials, CAPEX and Financing',
    ];
    for (const s of sample) expect(RETIRED.some(({ re }) => re.test(s)), s).toBe(true);
    // …and leaves ids, slugs and identifiers alone.
    for (const ok of ["?tab=debt", "sub: 'projections'", 'HistoricalsSection', 'useHistoricals', "tab: 'investment'"]) {
      expect(RETIRED.some(({ re }) => re.test(ok)), ok).toBe(false);
    }
  });

  it('comments are stripped, so code history may still name the old tabs', () => {
    expect(stripComments('// the Debt tab\nconst x = 1; /* Historicals */').includes('Debt')).toBe(false);
    expect(stripComments("const u = 'https://x.y/z';")).toContain('https://x.y/z');
  });

  it('every crash screen on the project page names its tab from TAB_LABEL', () => {
    const src = readFileSync(path.join(SRC, 'app/projects/[id]/page.tsx'), 'utf8');
    for (const t of PROJECT_TABS) {
      const key = (Object.keys(TAB_LABEL) as (keyof typeof TAB_LABEL)[]).find((k) => TAB_LABEL[k] === t.label);
      expect(key, t.label).toBeDefined();
      expect(src, t.label).toContain(`<ErrorBoundary tabName={TAB_LABEL.${key}}>`);
    }
  });

  it('the P&L sub-tab names come from one place', () => {
    expect(PL_SUB_LABEL).toEqual({ historicals: 'Historical P&L', projections: 'Future P&L' });
    const pl = readFileSync(path.join(SRC, 'components/project/PLTab.tsx'), 'utf8');
    expect(pl).toContain("{ id: 'historicals', label: PL_SUB_LABEL.historicals }");
    expect(pl).toContain("{ id: 'projections', label: PL_SUB_LABEL.projections }");
  });
});
