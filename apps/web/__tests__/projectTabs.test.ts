/**
 * Project tab registry — tester round R-062 / R-063 / R-069.
 *
 * Labels changed, ids did not: every `?tab=<id>` deep link and bookmark keeps
 * working. CAPEX (the former Investment tab) sits immediately after Overview.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { PROJECT_TABS, TAB_LABEL } from '@/lib/projectTabs';

describe('project tab registry', () => {
  it('investor-facing order: Overview → CAPEX → Market Comps → P&L → Financing → …', () => {
    const visible = PROJECT_TABS.filter((t) => !t.adminOnly).map((t) => t.label);
    expect(visible).toEqual([
      'Data Room', 'Overview', 'CAPEX', 'Market Comps', 'P&L', 'Financing',
      'Partnership', 'Cash Flow', 'Investment Bridge', 'Scenario Analysis', 'IC Memo',
    ]);
  });

  it('R-061 — Returns is not a tab any more (it is the Overview Returns section)', () => {
    expect(PROJECT_TABS.map((t) => t.id)).not.toContain('returns');
    expect(PROJECT_TABS.map((t) => t.label)).not.toContain('Returns');
  });

  it('R-073 — Investment Bridge sits immediately before Scenario Analysis', () => {
    const ids = PROJECT_TABS.map((t) => t.id);
    expect(ids.indexOf('investment-bridge')).toBe(ids.indexOf('scenarios') - 1);
    expect(TAB_LABEL.investmentBridge).toBe('Investment Bridge');
  });

  it('R-061 — the project page redirects ?tab=returns to Overview and no longer renders a Returns tab', () => {
    const src = readFileSync(path.resolve(__dirname, '../src/app/projects/[id]/page.tsx'), 'utf8');
    expect(src).toContain('legacyReturnsRedirect');
    expect(src).not.toMatch(/<ReturnsTab\b/);
  });

  it('R-061 — no cross-tab link targets the retired Returns tab', () => {
    for (const f of ['ICMemoTab.tsx', 'DebtTab.tsx', 'OverviewTab.tsx']) {
      const src = readFileSync(path.resolve(__dirname, `../src/components/project/${f}`), 'utf8');
      expect(src, f).not.toMatch(/tab=returns|tab: 'returns'|'→ Returns', 'returns'/);
    }
  });

  it('CAPEX is immediately after Overview', () => {
    const ids = PROJECT_TABS.map((t) => t.id);
    expect(ids.indexOf('investment')).toBe(ids.indexOf('overview') + 1);
  });

  it('keeps the stable ids so deep links still resolve', () => {
    const byId = Object.fromEntries(PROJECT_TABS.map((t) => [t.id, t.label]));
    expect(byId.investment).toBe('CAPEX');
    expect(byId.market).toBe('Market Comps');
    expect(byId.debt).toBe('Financing');
    expect(TAB_LABEL.investment).toBe('CAPEX');
    expect(TAB_LABEL.market).toBe('Market Comps');
    expect(TAB_LABEL.debt).toBe('Financing');
  });

  it('no tab is labelled with the retired names', () => {
    const labels = PROJECT_TABS.map((t) => t.label);
    for (const old of ['Investment', 'Market', 'Debt']) expect(labels).not.toContain(old);
  });

  it('the project page renders this registry (no second, divergent list)', () => {
    const src = readFileSync(path.resolve(__dirname, '../src/app/projects/[id]/page.tsx'), 'utf8');
    expect(src).toContain("import { PROJECT_TABS } from '@/lib/projectTabs'");
    expect(src).not.toMatch(/label: 'Investment'/);
  });
});
