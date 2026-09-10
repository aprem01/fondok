/**
 * Phase 1.4 — the web reads the GENERATED concept registry instead of its own
 * alias / label maps. This suite is the safety net for that swap.
 *
 * ``__tests__/fixtures/ontology/web_aliases_pre_registry.json`` pins, verbatim,
 * every hand-maintained map the web carried at 08bf554. Here we assert:
 *
 *   1. every registry-derived alias list is a SUPERSET of the pinned one —
 *      both literally and under ``findField``'s normalization, so nothing that
 *      resolved before stops resolving;
 *   2. the worksheet rows still carry the exact same keys (overrideKey /
 *      reviewKey / metaKey / y1Read / y1Src / fmt) and hist_key mapping;
 *   3. every pinned source label, badge, explanation and kind still maps to
 *      the SAME text — the six labels the web never knew are a pure widening.
 *
 * A failure here means Sam would see something change. The pinned JSON is the
 * contract: fix the adapter, never the fixture.
 */
import { describe, it, expect } from 'vitest';

import pinned from './fixtures/ontology/web_aliases_pre_registry.json';
import {
  HISTORICALS_ALIASES,
  PERIOD_ALIASES,
  WEB_SUBORDINATE_NAMESPACES,
  isSubordinatePath,
  worksheetBinding,
  HIST_KEY_BY_ROW,
  SOURCE_IDS,
  SOURCE_BADGE_FROM_REGISTRY,
} from '@/lib/ontology/adapters';
import { SUBORDINATE_NAMESPACES } from '@/lib/ontology/concepts.generated';
import { SOURCE_LABEL, sourceLabel, sourceExplanation, sourceKind } from '@/lib/provenance';
import { WORKSHEET_ROWS } from '@/components/project/pl/GroundedWorksheet';
import { histValue } from '@/lib/reviewState';
import type { HistYear } from '@/components/project/pl/HistoricalsSection';

// ``findField``'s matching surface: normalized alias + the unit-stripped form.
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
const stripUnit = (s: string) => s.replace(/(usd|pct|percent|ratio|amount)$/i, '');
function matchKeys(aliases: string[]): Set<string> {
  const out = new Set<string>();
  for (const a of aliases) {
    const n = norm(a);
    out.add(n);
    out.add(stripUnit(n));
  }
  return out;
}

describe('ontology adapters — buildHistYear alias lists', () => {
  const old = pinned.build_hist_year_aliases as Record<string, string[]>;

  it('covers every row the hand-written lists covered', () => {
    expect(Object.keys(HISTORICALS_ALIASES).sort()).toEqual(Object.keys(old).sort());
  });

  for (const row of Object.keys(pinned.build_hist_year_aliases as Record<string, string[]>)) {
    it(`${row} — registry list is a literal superset of the pinned list`, () => {
      const derived = new Set(HISTORICALS_ALIASES[row]);
      const missing = old[row].filter((a) => !derived.has(a));
      expect(missing, `${row} lost alias paths`).toEqual([]);
      expect(HISTORICALS_ALIASES[row].length).toBeGreaterThanOrEqual(old[row].length);
    });

    it(`${row} — every pinned alias still resolves under findField's normalization`, () => {
      const derived = matchKeys(HISTORICALS_ALIASES[row]);
      const missing = [...matchKeys(old[row])].filter((k) => !derived.has(k));
      expect(missing, `${row} lost match keys`).toEqual([]);
    });
  }

  it('the Misc. Income column still reads BOTH other_revenue and misc_revenue', () => {
    // The registry keeps them as two concepts; Historicals collapses them into
    // one column and must not drop either (worker DRIFT_NOTES.md §3.2).
    expect(HISTORICALS_ALIASES.misc).toContain('p_and_l_usali.other_operated_departments.revenue_usd');
    expect(HISTORICALS_ALIASES.misc).toContain('p_and_l_usali.miscellaneous_income.revenue_usd');
  });

  it('carries no templated alias paths — findField is a literal matcher', () => {
    for (const [row, aliases] of Object.entries(HISTORICALS_ALIASES)) {
      expect(aliases.filter((a) => a.includes('{')), row).toEqual([]);
    }
  });
});

describe('ontology adapters — deriveYearLabel period aliases', () => {
  const old = pinned.derive_year_label_aliases as Record<string, string[]>;

  for (const key of ['period_ending', 'period_type', 'period_label']) {
    it(`${key} — registry list is a superset of the pinned list`, () => {
      const derived = new Set(PERIOD_ALIASES[key]);
      expect(old[key].filter((a) => !derived.has(a)), key).toEqual([]);
    });
  }

  it('keeps the two web-only period_ending aliases the registry lacks', () => {
    // Recorded in DRIFT_NOTES.web.md → web_only_aliases.
    expect(PERIOD_ALIASES.period_ending).toContain('period_end');
    expect(PERIOD_ALIASES.period_ending).toContain('statement_period_end');
  });
});

describe('ontology adapters — subordinate namespaces', () => {
  it('enforces exactly the namespaces the web enforced before', () => {
    expect([...WEB_SUBORDINATE_NAMESPACES].sort()).toEqual(
      [...(pinned.subordinate_namespaces as string[])].sort(),
    );
  });

  it('draws them from the generated registry list', () => {
    for (const ns of WEB_SUBORDINATE_NAMESPACES) expect(SUBORDINATE_NAMESPACES).toContain(ns);
  });

  it('rejects the same paths the literal checks rejected', () => {
    for (const p of [
      'p_and_l_usali.monthly.jan.rooms_revenue_usd',
      'p_and_l_usali.quarterly.q1.rooms_revenue_usd',
      'p_and_l_usali.q2.rooms_revenue_usd',
      'p_and_l_usali.per_month.jan.rooms_revenue_usd',
      'p_and_l_usali.page5.rooms_revenue_usd',
    ]) {
      expect(isSubordinatePath(p), p).toBe(true);
    }
    for (const p of [
      'p_and_l_usali.operating_revenue.rooms_revenue',
      'p_and_l_usali.revenues.rooms_usd',
      'rooms_revenue',
    ]) {
      expect(isSubordinatePath(p), p).toBe(false);
    }
  });
});

describe('ontology adapters — worksheet rows', () => {
  type PinnedRow = {
    id: string; label: string; kind: string;
    overrideKey?: string; reviewKey?: string; metaKey?: string;
    y1Read?: string[]; y1Src?: string; fmt?: string;
  };
  const rows = pinned.worksheet_rows as PinnedRow[];

  for (const row of rows.filter((r) => r.kind !== 'section')) {
    it(`${row.id} — registry binding reproduces the hand-written keys`, () => {
      const b = worksheetBinding(row.id);
      expect(b.override_key ?? undefined, 'overrideKey').toBe(row.overrideKey);
      expect(b.review_key ?? undefined, 'reviewKey').toBe(row.reviewKey);
      expect(b.meta_key ?? undefined, 'metaKey').toBe(row.metaKey);
      expect(b.y1_read.length ? b.y1_read : undefined, 'y1Read').toEqual(row.y1Read);
      expect(b.y1_src ?? undefined, 'y1Src').toBe(row.y1Src);
      expect(b.fmt === 'currency' ? undefined : b.fmt, 'fmt').toBe(row.fmt);
    });
  }

  it('the rendered row model still has the same ids and metaKeys, in order', () => {
    expect(WORKSHEET_ROWS.map((r) => r.id)).toEqual(rows.map((r) => r.id));
    expect(WORKSHEET_ROWS.map((r) => r.metaKey)).toEqual(rows.map((r) => r.metaKey));
  });
});

describe('ontology adapters — histValue', () => {
  const old = pinned.hist_value as Record<string, string>;
  const SUM_ROW = 'total_rev';

  it('maps every pinned row to the same HistYear field', () => {
    for (const [rowId, field] of Object.entries(old)) {
      if (rowId === SUM_ROW) continue;
      expect(HIST_KEY_BY_ROW[rowId], rowId).toBe(field);
    }
  });

  it('reads the same values off a HistYear as the hand-written switch', () => {
    const h: HistYear = {
      year: '2024', days: 366,
      occupancyPct: 0.75, adr: 250, revpar: 187.5,
      rooms: 9_000_000, fb: 3_000_000, misc: 1_000_000,
      rooms_dept_expense: 2_000_000, fb_dept_expense: 2_400_000,
      other_dept_expense: 300_000, undistributed: 3_100_000,
      gop: 5_200_000, fixed_expenses: 1_900_000, noi: 1_950_000,
      mgmt_fee: 650_000, property_tax: 900_000, insurance: 350_000,
      populated: true,
    };
    const expected: Record<string, number | null> = {
      occ: 0.75, adr: 250, revpar: 187.5,
      rooms_rev: 9_000_000, fb_rev: 3_000_000, other_rev: 1_000_000,
      total_rev: 13_000_000,
      rooms_dept: 2_000_000, fb_dept: 2_400_000, other_dept: 300_000,
      undist_total: 3_100_000, gop: 5_200_000,
      mgmt: 650_000, taxes: 900_000, insurance: 350_000,
      fixed_total: 1_900_000, noi: 1_950_000,
    };
    for (const [rowId, value] of Object.entries(expected)) {
      expect(histValue(rowId, h), rowId).toBe(value);
    }
    // Rows the P&Ls don't break out still render "—".
    for (const rowId of ['ag', 'sm', 'pom', 'util', 'it', 'ffe', 's_rev', 'nope']) {
      expect(histValue(rowId, h), rowId).toBeNull();
    }
  });
});

describe('ontology adapters — provenance labels', () => {
  const p = pinned.provenance as {
    source_label: Record<string, string>;
    source_kind: Record<string, string>;
    source_explanation: Record<string, string>;
    grounded_sources: string[];
    override_sources: string[];
  };

  it('every pinned source label still maps to the same text', () => {
    for (const [id, label] of Object.entries(p.source_label)) {
      expect(SOURCE_LABEL[id], id).toBe(label);
      expect(sourceLabel(id), id).toBe(label);
    }
  });

  it('every pinned source explanation still maps to the same text', () => {
    for (const [id, text] of Object.entries(p.source_explanation)) {
      expect(sourceExplanation(id), id).toBe(text);
    }
    // Unknown ids still fall back to the seed copy.
    expect(sourceExplanation('not_a_source')).toBe(p.source_explanation.seed);
  });

  it('every pinned source still classifies to the same colour kind', () => {
    for (const [id, kind] of Object.entries(p.source_kind)) {
      expect(sourceKind(id), id).toBe(kind);
    }
    for (const id of p.grounded_sources) expect(sourceKind(id), id).toBe('grounded');
    for (const id of p.override_sources) expect(sourceKind(id), id).toBe('override');
  });

  it('keeps the six previously-unknown labels on the colour they already had', () => {
    // DRIFT PIN — the registry would call pip_om / partnership_doc grounded
    // (green) and pip_user / roi_user override, which would move the
    // Provenance Ledger's counts. See DRIFT_NOTES.web.md → "kind disagreements".
    for (const id of [
      'str_segmentation_default', 'pip_om', 'pip_user',
      'capex_ffe_default', 'roi_user', 'partnership_doc',
    ]) {
      expect(sourceKind(id), id).toBe('benchmark');
    }
  });

  it('every pinned AssumptionBadge label still maps to the same badge text', () => {
    const badges = pinned.assumption_badge as Record<string, { label: string; tooltip: string }>;
    for (const [id, meta] of Object.entries(badges)) {
      expect(SOURCE_BADGE_FROM_REGISTRY[id], id).toBe(meta.label);
    }
  });
});

describe('ontology adapters — the widening', () => {
  const WIDENED = [
    'str_segmentation_default', 'pip_om', 'pip_user',
    'capex_ffe_default', 'roi_user', 'partnership_doc',
  ];

  it('AssumptionSource now covers all 18 worker labels, not 12', () => {
    const before = pinned.assumption_source_union as string[];
    expect(before).toHaveLength(12);
    expect(SOURCE_IDS).toHaveLength(18);
    for (const id of before) expect(SOURCE_IDS, id).toContain(id);
    for (const id of WIDENED) expect(SOURCE_IDS, id).toContain(id);
  });

  it('the six that badged as "Seed" now badge with their real label', () => {
    const seedBadge = (pinned.assumption_badge as Record<string, { label: string }>).seed.label;
    for (const id of WIDENED) {
      expect(SOURCE_BADGE_FROM_REGISTRY[id], id).not.toBe(seedBadge);
      expect(SOURCE_BADGE_FROM_REGISTRY[id]?.length, id).toBeGreaterThan(0);
      // …and a real long label instead of the underscore-stripped id.
      expect(sourceLabel(id), id).not.toBe(id.replace(/_/g, ' '));
      // …and their own explanation instead of the seed default.
      expect(sourceExplanation(id), id).not.toBe(
        (pinned.provenance as { source_explanation: Record<string, string> }).source_explanation.seed,
      );
    }
  });
});
