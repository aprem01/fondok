/**
 * Partnership tab — canonical rebuild (FON-72, design-match).
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED DOLLAR WATERFALL — the "Allocation of Projected Proceeds"
 *     table renders one row per `tier_allocations[]` entry (Return of Capital /
 *     Preferred / GP Catch-Up / Promote) with LP $, GP $ and total, straight
 *     from a single mocked worker `partnership` envelope (the real
 *     ``getEngineField`` is exercised). No fixtures, no prototype numbers.
 *
 *  2. RECONCILES BADGE — when the envelope's `reconciles` flag is true, the
 *     green "Reconciles" badge renders with the LP + GP = total sentence.
 *
 *  3. CATCH-UP TIER — the `catch_up` tier from `tier_allocations` (and the
 *     `catch_up_amount`) surfaces both as a dollar-waterfall row and as the
 *     typed "GP Catch-Up" tier in the Promote Waterfall.
 *
 *  4. UNGATED — the tab renders its content (no "unavailable" dead-end) whenever
 *     the engine output is present.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import type { EngineOutputsResponse } from '@/lib/api';

// Mutable routing state (FON-59 #4) - `params` is a REAL URLSearchParams, what
// Next's ReadonlyURLSearchParams behaves like, so `useSubTab`'s toString()
// round-trip is exercised rather than stubbed.
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-uuid-1',
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    partnership: {
      deal_id: 'deal-uuid-1',
      engine: 'partnership',
      status: 'complete',
      summary: '',
      outputs: {
        gp: { partner: 'GP', contributed_equity: 1_883_668, distributions: 4_583_668, irr: 0.284, equity_multiple: 2.43 },
        lp: { partner: 'LP', contributed_equity: 16_953_008, distributions: 26_753_008, irr: 0.176, equity_multiple: 1.58 },
        promote_amount: 3_000_000,
        promote_earned: 3_000_000,
        gp_cash_flows: [100_000, 200_000, 300_000, 400_000, 3_583_668],
        lp_cash_flows: [500_000, 600_000, 700_000, 800_000, 24_153_008],
        tier_allocations: [
          { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
          { label: 'Preferred Return', kind: 'preferred', gp_amount: 200_000, lp_amount: 1_800_000, total_amount: 2_000_000 },
          { label: 'GP Catch-Up', kind: 'catch_up', gp_amount: 500_000, lp_amount: 0, total_amount: 500_000 },
          { label: 'Promote — above 15% LP IRR', kind: 'promote', gp_amount: 2_000_000, lp_amount: 8_000_000, total_amount: 10_000_000 },
        ],
        total_distributable: 31_336_676,
        reconciles: true,
        catch_up_amount: 500_000,
      },
      inputs: {},
      error: null,
      runtime_ms: 12,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: { levered_irr: 0.198, equity_multiple: 1.66, hold_years: 5 },
      inputs: {},
      error: null,
      runtime_ms: 9,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    // FON-66 §1 — the SAME run's capital engine, so the Summary's "Initial
    // Equity Required → Investment" row can be tied out against the very
    // Sources & Uses equity line the link lands on. Sam's complaint was that
    // those two numbers disagreed ($20,401,403 vs $19,998,900).
    capital: {
      deal_id: 'deal-uuid-1',
      engine: 'capital',
      status: 'complete',
      summary: '',
      outputs: {
        equity_amount: 17_836_676,
        sources: [
          { label: 'Senior Loan', amount: 26_000_000 },
          { label: 'Equity', amount: 17_836_676 },
          { label: 'Total Sources', amount: 43_836_676, is_total: true },
        ],
      },
      inputs: {},
      error: null,
      runtime_ms: 7,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

/** The equity figure Investment › Sources & Uses shows for THIS fixture — read
 *  out of the capital envelope above rather than restated as a literal. */
const INVESTMENT_SU_EQUITY = (
  (OUTPUTS as unknown as {
    engines: { capital: { outputs: { sources: { label: string; amount: number }[] } } };
  }).engines.capital.outputs.sources.find((l) => l.label === 'Equity') as { amount: number }
).amount;

// Mutable handle so one test can serve an enriched envelope (the FON-67
// additional-contribution fields) while the default fixture stays a run that
// PREDATES them — the "—, never $0" contract.
let activeOutputs: EngineOutputsResponse = OUTPUTS;
function withPartnershipOutputs(patch: Record<string, unknown>): EngineOutputsResponse {
  const base = OUTPUTS as unknown as {
    engines: Record<string, unknown> & { partnership: { outputs: Record<string, unknown> } };
  };
  return {
    ...base,
    engines: {
      ...base.engines,
      partnership: {
        ...base.engines.partnership,
        outputs: { ...base.engines.partnership.outputs, ...patch },
      },
    },
  } as unknown as EngineOutputsResponse;
}

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: activeOutputs,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
// STABLE identity — see debtTab.test for the full rationale: PartnershipTab has
// `useEffect(() => setOverrides(deal?.field_overrides ?? {}), [deal?.field_overrides])`,
// so a fresh `{}` per render loops the passive effect forever and hangs the test.
const mockDeal = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal,
    status: null,
    loading: false,
    error: null,
    workerConfigured: true,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
// C3 (Wave 3) — the partnership terms the deal carries are served through
// GET /deals/{id}/assumption_sources as `partnership.*` keys; an uncarried
// term is absent. The web carries no copy and the platform suggests none.
const sources = vi.hoisted(() => ({
  payload: null as null | {
    id: string;
    sources: Record<string, string>;
    values: Record<string, number | string | boolean | null>;
  },
}));
const assumptionSourcesSpy = vi.fn(async () => {
  if (!sources.payload) throw new Error('no payload');
  return sources.payload;
});
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        update: (...a: unknown[]) => updateSpy(...(a as [])),
        assumptionSources: () => assumptionSourcesSpy(),
      },
    },
  };
});

// Trim the heavy chrome to nothing — the test only cares about the sub-tab bodies.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import PartnershipTab from '@/components/project/PartnershipTab';
import { ProvenanceProvider } from '@/lib/hooks/useDealProvenance';
import { resetRouter } from './helpers/mockRouter';

/** Partnership terms the deal CARRIES (a partnership document), as the worker
 *  publishes them on assumption_sources. The worker suggests none. */
const CARRIED_TERMS_PAYLOAD = {
  id: 'deal-uuid-1',
  sources: {
    'partnership.pref_rate': 'partnership_doc',
    'partnership.gp_equity_pct': 'partnership_doc',
    'partnership.waterfall.tier_count': 'partnership_doc',
    'partnership.waterfall.0.hurdle_rate': 'partnership_doc',
    'partnership.waterfall.0.gp_split': 'partnership_doc',
    'partnership.waterfall.1.hurdle_rate': 'partnership_doc',
    'partnership.waterfall.1.gp_split': 'partnership_doc',
  },
  values: {
    'partnership.pref_rate': 0.09,
    'partnership.gp_equity_pct': 0.12,
    'partnership.waterfall.tier_count': 2,
    'partnership.waterfall.0.hurdle_rate': 0.13,
    'partnership.waterfall.0.gp_split': 0.2,
    'partnership.waterfall.1.hurdle_rate': 0.18,
    'partnership.waterfall.1.gp_split': 0.35,
  },
};

/** Mount the tab the way the deal page does — inside the deal's provenance. */
function renderWithSources() {
  return render(
    <ProvenanceProvider dealId="deal-uuid-1">
      <PartnershipTab />
    </ProvenanceProvider>,
  );
}

beforeEach(() => {
  cleanup();
  resetRouter(nav);
  sources.payload = CARRIED_TERMS_PAYLOAD;
  assumptionSourcesSpy.mockClear();
  activeOutputs = OUTPUTS;
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('PartnershipTab — canonical structure', () => {
  it('renders the three canonical sub-tabs (no "unavailable" dead-end)', () => {
    render(<PartnershipTab />);
    expect(screen.getByRole('tab', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Waterfall' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Cash Flows' })).toBeInTheDocument();
    // Ungated: the old "Partnership Engine unavailable" card must be gone.
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument();
    // Partnership-terms banner (FON-66 D6): one truthful line — manual entry
    // always available AND JV extraction live. The old contradictory pair
    // ("manual only" + "LIVE · document extraction") must be gone.
    expect(screen.getByText(/Manual entry always available/i)).toBeInTheDocument();
    expect(screen.queryByText(/Manual inputs · current release/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Live · document extraction/i)).not.toBeInTheDocument();
  });
});

describe('PartnershipTab — dollar waterfall from tier_allocations', () => {
  it('renders a row per tier_allocation with the engine dollar amounts', () => {
    render(<PartnershipTab />);
    // Summary shows the "Waterfall Allocation Preview" dollar waterfall.
    expect(screen.getByText('Waterfall Allocation Preview')).toBeInTheDocument();

    // One row per typed tier. FON-66 (Sam, 2026-11-23): structural rows are
    // shown under the tier table's names; a promote row no configured band
    // accounts for keeps the engine envelope's label.
    expect(screen.getAllByText('Tier I — Return of Capital').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Tier II — Preferred Return').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Promote — above 15% LP IRR').length).toBeGreaterThan(0);

    // Engine dollar amounts, not prototype placeholders.
    expect(screen.getAllByText('$18,836,676').length).toBeGreaterThan(0); // ROC total
    expect(screen.getAllByText('$31,336,676').length).toBeGreaterThan(0); // total distributable
  });

  it('renders the "Reconciles" badge with the LP + GP = total sentence', () => {
    render(<PartnershipTab />);
    expect(screen.getByText('Reconciles')).toBeInTheDocument();
    expect(
      screen.getByText(/LP \$26,753,008 \+ GP \$4,583,668 = \$31,336,676 total deal distributions ✓/),
    ).toBeInTheDocument();
  });

  it('surfaces the catch-up tier both as a dollar row and a typed waterfall tier', () => {
    render(<PartnershipTab />);
    // Summary allocation row (under the tier table's name for the step).
    expect(screen.getAllByText('Tier III — GP Catch-Up').length).toBeGreaterThan(0);

    // Waterfall sub-tab: the typed Promote Waterfall carries the Catch-Up tier.
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    expect(screen.getByText('Allocation of Projected Proceeds')).toBeInTheDocument();
    expect(screen.getByText('Promote Waterfall')).toBeInTheDocument();
    // FON-66 (Sam, 2026-11-23) — the tier table AND the allocation preview on
    // this sub-tab both carry the tier table's names for the structural steps.
    expect(screen.getAllByText('Tier I — Return of Capital').length).toBeGreaterThan(1);
    expect(screen.getAllByText('Tier II — Preferred Return').length).toBeGreaterThan(1);
    expect(screen.getAllByText('Tier III — GP Catch-Up').length).toBeGreaterThan(1);
  });
});

describe('PartnershipTab — partner returns + cash flows', () => {
  it('renders the Deal / LP / GP partner-returns cards with promote', () => {
    render(<PartnershipTab />);
    expect(screen.getByText('Partner Returns')).toBeInTheDocument();
    expect(screen.getByText('Deal level')).toBeInTheDocument();
    expect(screen.getByText('LP investors')).toBeInTheDocument();
    expect(screen.getByText('GP / sponsor')).toBeInTheDocument();
    expect(screen.getByText('Promote / carry earned')).toBeInTheDocument();
    // Deal-level IRR comes from the Returns engine (levered_irr 0.198 → 19.8%).
    expect(screen.getByText('19.8%')).toBeInTheDocument();
  });

  it('renders the contributions-vs-distributions cash-flow grid', () => {
    render(<PartnershipTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Cash Flows' }));
    expect(screen.getByText('Partner Cash Flows')).toBeInTheDocument();
    expect(screen.getByText('GP contribution')).toBeInTheDocument();
    expect(screen.getByText('LP distribution')).toBeInTheDocument();
    // Reconciliation cards.
    expect(screen.getByText('Contributions, distributions and profit')).toBeInTheDocument();
    expect(screen.getByText('Invested equity')).toBeInTheDocument();
  });
});

// FON-67 (D3) — "Additional contributions" is READ from the partnership engine
// (gp_/lp_additional_contributions + total_contributions), never a hardcoded $0.
describe('PartnershipTab — FON-67 additional contributions read from the engine', () => {
  const row = (label: string) => screen.getByText(label).parentElement as HTMLElement;

  it('renders "—" (never $0) when the run predates the additional-contribution fields', () => {
    render(<PartnershipTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Cash Flows' }));

    for (const label of ['Additional contributions — GP', 'Additional contributions — LP', 'Additional contributions', 'Total invested equity']) {
      expect(within(row(label)).getByText('—')).toBeInTheDocument();
      expect(within(row(label)).queryByText('$0')).not.toBeInTheDocument();
    }
    // Pre-FON-67 ``contributed_equity`` was the close draw only — it is still
    // the honest "Initial equity required".
    expect(within(row('Initial equity required')).getByText('$18,836,676')).toBeInTheDocument();
  });

  it('shows the GP/LP split, the total, and dates a deficit-year draw as a contribution', () => {
    activeOutputs = withPartnershipOutputs({
      // contributed_equity now INCLUDES the draws (worker contract), so the
      // close draw is total − additional.
      gp_additional_contributions: 100_000,
      lp_additional_contributions: 900_000,
      total_contributions: 18_836_676,
      // Year 2 is a deficit year — a pro-rata capital call, NOT a negative
      // distribution.
      gp_cash_flows: [100_000, -100_000, 300_000, 400_000, 3_583_668],
      lp_cash_flows: [500_000, -900_000, 700_000, 800_000, 24_153_008],
    });
    render(<PartnershipTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Cash Flows' }));

    expect(within(row('Additional contributions — GP')).getByText('$100,000')).toBeInTheDocument();
    expect(within(row('Additional contributions — LP')).getByText('$900,000')).toBeInTheDocument();
    expect(within(row('Additional contributions')).getByText('$1,000,000')).toBeInTheDocument();
    expect(within(row('Total invested equity')).getByText('$18,836,676')).toBeInTheDocument();
    expect(within(row('Initial equity required')).getByText('$17,836,676')).toBeInTheDocument();
    // Close row carries the initial draw only (total − additional).
    expect(screen.getByText('$1,783,668')).toBeInTheDocument();
    expect(screen.getByText('$16,053,008')).toBeInTheDocument();
    // The draw never renders as a negative partner distribution.
    expect(screen.queryByText('-$100,000')).not.toBeInTheDocument();
    expect(screen.queryByText('-$900,000')).not.toBeInTheDocument();
    // Stated on both the Invested-equity card and the grid footnote.
    expect(screen.getAllByText(/dated pro-rata GP\/LP capital call/i).length).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 §1 — the Summary stops conflating initial with total invested equity
//
// Sam (9/11): "Summary currently shows Total Equity → Investment = $20,401,403.
// At first glance this looks inconsistent with Investment S&U, which shows
// Equity = $19,998,900 … the Summary label is simply conflating initial equity
// with subsequent capital calls."
// ─────────────────────────────────────────────────────────────────────────

describe('PartnershipTab — Summary equity bridge (FON-66 §1)', () => {
  const summaryRow = (label: string) =>
    screen.getByText(label).parentElement!.parentElement as HTMLElement;
  /** The FON-67 fields a modern run reports — the default fixture predates them. */
  const withAdditional = () =>
    withPartnershipOutputs({
      gp_additional_contributions: 100_000,
      lp_additional_contributions: 900_000,
      total_contributions: 18_836_676,
    });

  it('splits the one row into initial, additional, and total invested', () => {
    activeOutputs = withAdditional();
    render(<PartnershipTab />);

    // The conflating single row is gone.
    expect(screen.queryByText('Total Equity')).not.toBeInTheDocument();

    expect(within(summaryRow('Initial Equity Required')).getByText('$17,836,676')).toBeInTheDocument();
    expect(within(summaryRow('Additional Contributions')).getByText('$1,000,000')).toBeInTheDocument();
    expect(within(summaryRow('Total Invested Equity')).getByText('$18,836,676')).toBeInTheDocument();
  });

  // THE complaint, pinned: the row that cites Investment shows the number
  // Investment owns — the Sources & Uses equity line of the SAME run.
  it('Initial Equity Required equals the Investment Sources & Uses equity figure', () => {
    activeOutputs = withAdditional();
    render(<PartnershipTab />);

    const shown = within(summaryRow('Initial Equity Required')).getByText(/^\$/).textContent;
    expect(shown).toBe(`$${INVESTMENT_SU_EQUITY.toLocaleString('en-US')}`);
    // …and it is NOT the total invested, which is what used to be shown here.
    expect(shown).not.toBe('$18,836,676');
  });

  it('puts the → CAPEX link on the INITIAL row only', () => {
    activeOutputs = withAdditional();
    render(<PartnershipTab />);

    const link = within(summaryRow('Initial Equity Required')).getByRole('link', { name: '→ CAPEX' });
    expect(link.getAttribute('href')).toBe('?tab=investment&sub=sources-and-uses');
    // Exactly one — neither the additional nor the total row claims Investment.
    expect(screen.getAllByRole('link', { name: '→ CAPEX' })).toHaveLength(1);
    // Additional contributions points at the sub-tab that dates them instead.
    expect(
      within(summaryRow('Additional Contributions'))
        .getByRole('link', { name: '→ Partner Cash Flows' })
        .getAttribute('href'),
    ).toBe('?tab=partnership&sub=cash-flows');
  });

  it('GP / LP Contribution name the close draw so the split reconciles both ways', () => {
    activeOutputs = withAdditional();
    render(<PartnershipTab />);
    // Totals on the row, the close draw on its sub-line.
    expect(within(summaryRow('GP Contribution')).getByText('$1,883,668')).toBeInTheDocument();
    expect(screen.getByText('$1,783,668 at close')).toBeInTheDocument();
    expect(within(summaryRow('LP Contribution')).getByText('$16,953,008')).toBeInTheDocument();
    expect(screen.getByText('$16,053,008 at close')).toBeInTheDocument();
  });

  it('on a run predating the tracking it carries the Cash Flows sentence, not a fabricated $0', () => {
    render(<PartnershipTab />); // default fixture = the legacy run
    const additional = summaryRow('Additional Contributions');
    expect(within(additional).getByText('—')).toBeInTheDocument();
    expect(within(additional).queryByText('$0')).not.toBeInTheDocument();
    expect(within(summaryRow('Total Invested Equity')).getByText('—')).toBeInTheDocument();
    // The SAME sentence the Invested-equity card on Cash Flows already used.
    expect(
      screen.getAllByText(/This run predates additional-contribution tracking — re-run the Partnership engine/i).length,
    ).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 §2 — waterfall table polish (the three items still open)
// ─────────────────────────────────────────────────────────────────────────

describe('PartnershipTab — waterfall table polish (FON-66 §2)', () => {
  // The worker's two-band reference ladder (C3) gives the table removable tiers.
  async function openWaterfall() {
    renderWithSources();
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    await screen.findByRole('button', { name: 'Remove promote tier 1' });
  }
  const removeBtn = () => screen.getByRole('button', { name: 'Remove promote tier 1' });

  it('Remove is an icon with an accessible label, hidden until the row is hovered', async () => {
    await openWaterfall();
    // Not a bordered "Remove" word any more — an icon carrying its own label.
    expect(removeBtn().textContent).toBe('');
    expect(screen.queryByText('Remove')).toBeNull();
    expect(removeBtn().querySelector('svg')).toBeTruthy();
    // Present in the DOM and the tab order, but invisible until the row is
    // hovered or the control takes focus — so the keyboard never loses it.
    expect(removeBtn().style.opacity).toBe('0');

    const row = removeBtn().closest('div[style*="grid"]') as HTMLElement;
    fireEvent.mouseEnter(row);
    expect(removeBtn().style.opacity).toBe('1');
    fireEvent.mouseLeave(row);
    expect(removeBtn().style.opacity).toBe('0');
  });

  it('keyboard focus reveals it too', async () => {
    await openWaterfall();
    fireEvent.focus(removeBtn());
    expect(removeBtn().style.opacity).toBe('1');
  });

  it('"Add tier" is a row of the table, on the same grid as the tiers', async () => {
    await openWaterfall();
    const add = screen.getByRole('button', { name: /Add tier/ });
    // Laid out on the tier grid, under the table's own hairline.
    expect(add.style.display).toBe('grid');
    expect(add.style.width).toBe('100%');
    expect(add.style.borderTop).toContain('1px solid');
    expect(add.style.borderBottom).toContain('dashed');
    // Still opens the FON-74 justification row rather than adding silently.
    fireEvent.click(add);
    expect(screen.getByTestId('add-tier-note')).toBeInTheDocument();
  });

  it('keeps GP split and LP split as two independently editable columns', async () => {
    // FOUNDER DECISION — Sam's optional "consider presenting each economic
    // split together (20% GP / 80% LP)" is DECLINED: two right-aligned numeric
    // columns scan down a table better than a combined string, and both halves
    // are independently editable, so collapsing them would cost an edit target
    // to save a column.
    await openWaterfall();
    expect(screen.getByText('GP split')).toBeInTheDocument();
    expect(screen.getByText('LP split')).toBeInTheDocument();
    expect(screen.queryByText(/\d+%\s*GP\s*\/\s*\d+%\s*LP/)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Sub-tab routing convention (FON-59 #4 / FON-61 §3)
//
// Every sub-tab is now a URL slug on the shared `useSubTab` hook, so a deep
// link lands where it says, the back button works, and `setSub` keeps every
// other query param (`doc`, `focus`, `reviewField`) intact.
// ─────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────
// FON-66 (Sam's QA, 2026-11-16) — on a one-tier stack the promote row read
// "Promote — above 12% LP IRR" while its hurdle cell read "Until 12% LP IRR".
// The engine pays a band until the LP IRR reaches its hurdle, so the name and
// the hurdle cell must carry the same number; only the top band of a
// multi-tier stack is open-ended, and then above the PREVIOUS hurdle, which is
// how the worker labels it ("Tier 2 (>13%)").
// ─────────────────────────────────────────────────────────────────────────

/** Sam's QA entry on d0bfe816: one promote tier, 12% hurdle, 20% GP. */
const ONE_TIER_PAYLOAD = {
  id: 'deal-uuid-1',
  sources: {
    'partnership.pref_rate': 'analyst_override',
    'partnership.gp_equity_pct': 'analyst_override',
    'partnership.waterfall.tier_count': 'analyst_override',
    'partnership.waterfall.0.hurdle_rate': 'analyst_override',
    'partnership.waterfall.0.gp_split': 'analyst_override',
  },
  values: {
    'partnership.pref_rate': 0.08,
    'partnership.gp_equity_pct': 0.10,
    'partnership.waterfall.tier_count': 1,
    'partnership.waterfall.0.hurdle_rate': 0.12,
    'partnership.waterfall.0.gp_split': 0.2,
  },
};

describe('PartnershipTab — promote tier name agrees with its hurdle cell (FON-66, Sam 2026-11-16)', () => {
  it('a one-tier stack reads "to 12%" beside "Until 12%" — never "above" its own hurdle', async () => {
    sources.payload = ONE_TIER_PAYLOAD;
    renderWithSources();
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    expect(await within(tiers).findByText('Promote — to 12% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getByText('Until 12% LP IRR')).toBeInTheDocument();
    expect(within(tiers).queryByText(/Promote — above/)).toBeNull();
    // The dollar waterfall keys the same band by the worker's label.
    expect(within(tiers).getByText('Residual split — a single tier takes all remaining proceeds')).toBeInTheDocument();
  });

  it('every capped band names the hurdle its cell shows; the open-ended top band names the previous one', async () => {
    renderWithSources(); // the two-tier carried stack: 13% then 18%
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    expect(await within(tiers).findByText('Promote — to 13% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getByText('Until 13% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getByText('Promote — above 13% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getByText('Until 18% LP IRR')).toBeInTheDocument();
    expect(within(tiers).queryByText('Promote — above 18% LP IRR')).toBeNull();
    expect(within(tiers).getByText('All remaining proceeds above the previous hurdle')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 (Sam's QA, 2026-11-16) — "Clear override". Clearing the input and
// saving only closed the editor (an empty draft is a no-op), so the 10% stuck
// and Sam found no way back to the deal's own terms. The editor now carries
// a Clear override button whenever the field has an analyst override; it
// drops the key(s) from `field_overrides` in the same PATCH a save uses.
// ─────────────────────────────────────────────────────────────────────────

describe('PartnershipTab — Clear override on the waterfall editors (FON-66, Sam 2026-11-16)', () => {
  const QA_NOTE = 'QA test FON-66';
  afterEach(() => { mockDeal.field_overrides = {}; });

  it('a hurdle with an override offers Clear override, and clearing drops the key from field_overrides', async () => {
    mockDeal.field_overrides = {
      'partnership.waterfall.0.hurdle_rate': { value: 0.12, note: QA_NOTE },
      pref_rate: { value: 0.08, note: QA_NOTE },
    };
    renderWithSources();
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    // The override (12%) wins over the carried 13%.
    fireEvent.click(await screen.findByText('Until 12% LP IRR'));
    const clear = await screen.findByRole('button', { name: 'Clear override' });
    fireEvent.click(clear);
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = (updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }]);
    expect(body.field_overrides).not.toHaveProperty('partnership.waterfall.0.hurdle_rate');
    // Only the cleared key leaves; the other override stays.
    expect(body.field_overrides).toHaveProperty('pref_rate');
    // Back to the carried term, with no editor left open.
    expect(await screen.findByText('Until 13% LP IRR')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear override' })).toBeNull();
  });

  it('GP ownership clears GP and LP together; a field with no override offers no Clear', async () => {
    mockDeal.field_overrides = {
      gp_equity_pct: { value: 0.1, note: QA_NOTE },
      lp_equity_pct: { value: 0.9, note: QA_NOTE },
    };
    renderWithSources();
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    // The hurdle carries no override → no Clear on its editor.
    fireEvent.click(await screen.findByText('Until 13% LP IRR'));
    expect(screen.queryByRole('button', { name: 'Clear override' })).toBeNull();
    fireEvent.keyDown(screen.getByLabelText('percent'), { key: 'Escape' });

    // The ownership row does.
    const own = screen.getAllByText('10%').find((el) => el.closest('div')?.textContent?.includes('GP / Sponsor Ownership'));
    expect(own).toBeTruthy();
    fireEvent.click(own as HTMLElement);
    fireEvent.click(await screen.findByRole('button', { name: 'Clear override' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = (updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }]);
    expect(body.field_overrides).toEqual({});
  });
});

describe('PartnershipTab — `?tab=partnership&sub=<slug>` routing', () => {
  const tabEl = (name: string) => screen.getByRole('tab', { name });

  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
    nav.replace.mockClear();
  });

  it('opens Waterfall on ?sub=waterfall', () => {
    nav.params = new URLSearchParams('tab=partnership&sub=waterfall');
    render(<PartnershipTab />);
    expect(tabEl('Waterfall')).toHaveAttribute('aria-selected', 'true');
    expect(tabEl('Summary')).toHaveAttribute('aria-selected', 'false');
  });

  it('opens Cash Flows on ?sub=cash-flows', () => {
    nav.params = new URLSearchParams('tab=partnership&sub=cash-flows');
    render(<PartnershipTab />);
    expect(tabEl('Cash Flows')).toHaveAttribute('aria-selected', 'true');
  });

  it('falls back to Summary on an unknown sub value', () => {
    nav.params = new URLSearchParams('tab=partnership&sub=not-a-sub-tab');
    render(<PartnershipTab />);
    expect(tabEl('Summary')).toHaveAttribute('aria-selected', 'true');
  });

  it('follows a param change while already mounted', () => {
    nav.params = new URLSearchParams('tab=partnership&sub=waterfall');
    const { rerender } = render(<PartnershipTab />);
    expect(tabEl('Waterfall')).toHaveAttribute('aria-selected', 'true');

    nav.params = new URLSearchParams('tab=partnership&sub=cash-flows');
    rerender(<PartnershipTab />);
    expect(tabEl('Cash Flows')).toHaveAttribute('aria-selected', 'true');
  });

  it('setSub writes sub= and preserves doc / focus / reviewField', () => {
    nav.params = new URLSearchParams('tab=partnership&doc=doc-9&focus=promote&reviewField=noi_usd');
    render(<PartnershipTab />);
    fireEvent.click(tabEl('Cash Flows'));

    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const written = new URLSearchParams(url.split('?')[1]);
    expect(written.get('sub')).toBe('cash-flows');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('promote');
    expect(written.get('reviewField')).toBe('noi_usd');
  });

  // FON-66 (Sam, 09-11) — Total Equity cites Investment; the initial equity
  // requirement lives on Investment → Sources & Uses, so land there.
  it('the "→ CAPEX" link deep-links to Sources & Uses (FON-66)', () => {
    render(<PartnershipTab />);
    const a = screen.getByRole('link', { name: '→ CAPEX' });
    expect(a.getAttribute('href')).toBe('?tab=investment&sub=sources-and-uses');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 (R-071) — "Partnership should address the equity distribution
// waterfall between the equity partners and the sponsor"
//
// The Summary now says plainly that this is the EQUITY partnership and shows
// the tier table (hurdle, LP / GP split per tier) ABOVE the partner-return
// KPIs, so the waterfall is visible without switching sub-tabs. Layout and
// copy only: the same PromoteWaterfall component, mounted read-only, over the
// same overrides and seed — no calculation or override path changes.
// ─────────────────────────────────────────────────────────────────────────

describe('PartnershipTab — Summary shows the distribution waterfall (FON-66 / R-071)', () => {
  beforeEach(() => {
    cleanup();
    nav.params = new URLSearchParams('');
  });

  it('states plainly that this is the equity partnership between the partners and the sponsor', () => {
    render(<PartnershipTab />);
    const header = screen.getByTestId('summary-equity-partnership-header');
    expect(header).toHaveTextContent(/equity partnership/i);
    expect(header).toHaveTextContent(/between the equity partners \(the LP investors\) and the sponsor \(the GP\)/);
    expect(header).toHaveTextContent(/LP \/ GP equity split, the preferred return, the promote tiers and the distribution waterfall/);
    // The sub-tab caption says it too.
    expect(
      screen.getAllByText(/distribution waterfall between the equity partners and the sponsor/i).length,
    ).toBeGreaterThan(0);
  });

  it('renders the tier table (hurdle, LP / GP split) on the Summary, above the partner-return KPIs', async () => {
    renderWithSources();
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    // The promote bands — the worker's reference terms, the same ones the
    // Waterfall sub-tab edits.
    expect(await within(tiers).findByText('Promote — to 13% LP IRR')).toBeInTheDocument();

    // Column heads + the typed structural tiers.
    expect(within(tiers).getByText('Hurdle')).toBeInTheDocument();
    expect(within(tiers).getByText('GP split')).toBeInTheDocument();
    expect(within(tiers).getByText('LP split')).toBeInTheDocument();
    expect(within(tiers).getByText('Tier I — Return of Capital')).toBeInTheDocument();
    expect(within(tiers).getByText('Tier II — Preferred Return')).toBeInTheDocument();
    expect(within(tiers).getByText('Tier III — GP Catch-Up')).toBeInTheDocument();
    // FON-66 (Sam, 2026-11-16) — the top band is open-ended above the PREVIOUS
    // hurdle (the worker's "Tier 2 (>13%)"), not above its own entered hurdle.
    expect(within(tiers).getByText('Promote — above 13% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getByText('Until 13% LP IRR')).toBeInTheDocument();
    expect(within(tiers).getAllByText('35%').length).toBeGreaterThan(0); // GP split
    expect(within(tiers).getAllByText('65%').length).toBeGreaterThan(0); // LP split = 100% − GP

    // ABOVE the partner-return KPIs.
    const partnerReturns = screen.getByText('Partner Returns');
    // eslint-disable-next-line no-bitwise
    expect(tiers.compareDocumentPosition(partnerReturns) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('is read-only on the Summary — editing stays on the Waterfall sub-tab', async () => {
    renderWithSources();
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    await within(tiers).findByText('Until 13% LP IRR');
    expect(within(tiers).queryByRole('button', { name: /Add tier/ })).toBeNull();
    expect(within(tiers).queryByRole('button', { name: /Remove promote tier/ })).toBeNull();
    expect(within(tiers).getByText(/Hurdles and splits are edited on the Waterfall sub-tab/)).toBeInTheDocument();
    // Clicking a hurdle on the Summary opens no editor and PATCHes nothing.
    fireEvent.click(within(tiers).getByText('Until 13% LP IRR'));
    expect(tiers.querySelector('input')).toBeNull();
    expect(updateSpy).not.toHaveBeenCalled();

    // The Waterfall sub-tab keeps its editors and controls.
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    expect(screen.getByRole('button', { name: /Add tier/ })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Remove promote tier/ }).length).toBeGreaterThan(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// C3 (Wave 3) — the web holds no partnership terms and the platform suggests
// none. The tab reads the terms the deal carries off assumption_sources
// (`partnership.*`); a term the deal does not carry is absent there and reads
// "—" (awaiting data), never a browser-side 8% / 10% / promote ladder and
// never a "Platform default".
// ─────────────────────────────────────────────────────────────────────────
/** The KeyRow (label + value) for a label — not the dollar-waterfall row that
 *  may share its text. */
function rowFor(label: string): HTMLElement {
  const rows = screen
    .getAllByText(label)
    .map((el) => el.parentElement!.parentElement as HTMLElement)
    .filter((r) => r.children.length === 2);
  expect(rows.length, `no key row "${label}"`).toBeGreaterThan(0);
  return rows[0];
}

describe('PartnershipTab — partnership terms are the deal\u2019s own or absent (C3)', () => {
  /** The engine envelope WITHOUT a pref or a GP/LP equity split. */
  function bareOutputs() {
    return withPartnershipOutputs({ gp: null, lp: null });
  }

  it('absent from the payload → Preferred Return, GP ownership and the promote bands are dashes, not literals', async () => {
    sources.payload = { id: 'deal-uuid-1', sources: {}, values: {} };
    activeOutputs = bareOutputs();
    renderWithSources();
    await waitFor(() => expect(assumptionSourcesSpy).toHaveBeenCalled());
    const pref = rowFor('Preferred Return');
    expect(pref).toHaveTextContent('—');
    expect(pref).not.toHaveTextContent('8%');
    expect(rowFor('GP / Sponsor Ownership')).toHaveTextContent('—');
    expect(rowFor('GP / Sponsor Ownership')).not.toHaveTextContent('10%');
    expect(rowFor('LP Investor Ownership')).toHaveTextContent('—');
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    expect(within(tiers).queryByText(/Promote — to 10% LP IRR/)).toBeNull();
    expect(within(tiers).getByText('— preferred return')).toBeInTheDocument();
  });

  it('carried terms render as the deal\u2019s own values — no Platform default badge anywhere', async () => {
    activeOutputs = bareOutputs();
    renderWithSources();
    await waitFor(() => expect(rowFor('Preferred Return')).toHaveTextContent('9%'));
    expect(rowFor('GP / Sponsor Ownership')).toHaveTextContent('12%');
    expect(rowFor('LP Investor Ownership')).toHaveTextContent('88%');
    expect(screen.getByTestId('summary-waterfall-tiers')).toHaveTextContent('Until 13% LP IRR');
    expect(screen.queryByText(/platform default/i)).toBeNull();
  });

  it('GP not carried → GP ownership and LP ownership are both dashes', async () => {
    activeOutputs = bareOutputs();
    const { 'partnership.gp_equity_pct': _gs, ...srcs } = CARRIED_TERMS_PAYLOAD.sources;
    const { 'partnership.gp_equity_pct': _gv, ...vals } = CARRIED_TERMS_PAYLOAD.values;
    sources.payload = { ...CARRIED_TERMS_PAYLOAD, sources: srcs, values: vals };
    renderWithSources();
    await waitFor(() => expect(rowFor('Preferred Return')).toHaveTextContent('9%'));
    expect(rowFor('GP / Sponsor Ownership')).toHaveTextContent('—');
    expect(rowFor('LP Investor Ownership')).toHaveTextContent('—');
    // An unknown LP share is awaiting data, not a calculated figure.
    expect(within(rowFor('LP Investor Ownership')).getByLabelText('Awaiting data')).toBeInTheDocument();
  });

  it('an engine-published pref wins over the carried term', async () => {
    activeOutputs = withPartnershipOutputs({ lp_pref_pct: 0.07 });
    renderWithSources();
    await waitFor(() => expect(assumptionSourcesSpy).toHaveBeenCalled());
    expect(rowFor('Preferred Return')).toHaveTextContent('7%');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Wave 3 (B5) — the worker's `not_applicable` partnership row is a neutral
// state, never a failure: no terms → "No partnership terms on this deal" +
// the worker's message; half-entered terms → the blocked-run notice with the
// worker's sentence naming each tier.
// ─────────────────────────────────────────────────────────────────────────
describe('PartnershipTab — the engine does not apply (not_applicable)', () => {
  function withPartnershipRow(row: Record<string, unknown>) {
    const base = OUTPUTS as unknown as { engines: Record<string, Record<string, unknown>> };
    return {
      ...base,
      engines: { ...base.engines, partnership: { ...base.engines.partnership, outputs: null, ...row } },
    } as unknown as EngineOutputsResponse;
  }

  it('no terms → the neutral empty state with the worker\u2019s message; no failure styling', () => {
    activeOutputs = withPartnershipRow({
      status: 'not_applicable',
      error: null,
      not_applicable: {
        engine: 'partnership', reason: 'not_applicable', message: 'no partnership terms on this deal',
        missing: ['split', 'pref_rate', 'tiers'], incomplete: [],
      },
    });
    render(<PartnershipTab />);
    const card = screen.getByTestId('partnership-not-applicable');
    expect(card).toHaveTextContent('No partnership terms on this deal');
    expect(card).toHaveTextContent('no partnership terms on this deal');
    expect(card.getAttribute('role')).not.toBe('alert');
    expect(screen.queryByTestId('partnership-terms-incomplete')).toBeNull();
    expect(screen.queryByText(/didn\u2019t finish|errored|Failed/)).toBeNull();
    // The sub-tabs stay so the terms can be entered.
    expect(screen.getByRole('tab', { name: 'Waterfall' })).toBeInTheDocument();
  });

  it('half-entered tiers → the blocked-run notice naming each tier, verbatim', () => {
    const message = 'partnership terms incomplete on this deal: Tier 2 has a GP split but no hurdle; Tier 4 has a hurdle but no GP / LP split';
    activeOutputs = withPartnershipRow({
      status: 'not_applicable',
      error: null,
      not_applicable: {
        engine: 'partnership', reason: 'awaiting_analyst', message,
        missing: [], incomplete: ['Tier 2 has a GP split but no hurdle', 'Tier 4 has a hurdle but no GP / LP split'],
      },
    });
    render(<PartnershipTab />);
    const notice = screen.getByTestId('partnership-terms-incomplete');
    expect(notice).toHaveTextContent(message);
    expect(notice.getAttribute('role')).toBe('status');
    expect(screen.queryByTestId('partnership-not-applicable')).toBeNull();
  });

  it('a complete run shows neither state', () => {
    render(<PartnershipTab />);
    expect(screen.queryByTestId('partnership-not-applicable')).toBeNull();
    expect(screen.queryByTestId('partnership-terms-incomplete')).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 (Sam's QA, 2026-11-23, d0bfe816 — one promote tier, 12% hurdle,
// 20 / 80 after an 8% pref). Two nits:
//  1. The Allocation Preview still called the band "Tier 1 (to 12%)" while
//     the tier table above it said "Promote — to 12% LP IRR". The preview now
//     shows the tier table's name; the engine label stays the matching key.
//  2. "Initial Promote Split" read "—" although the tier is 20 / 80: the row
//     read `visibleTierIndices[1]`, assuming index 0 was a pref tier. The pref
//     is the engine's own `pref_rate` step; waterfall[0] IS the first promote
//     band, so a one-tier stack reads "20% GP" and has no additional hurdles.
// ─────────────────────────────────────────────────────────────────────────
describe('PartnershipTab — one-tier stack: preview name + Initial Promote Split (FON-66, Sam 2026-11-23)', () => {
  /** The engine's run on Sam's one-tier stack: it keys the band "Tier 1 (to 12%)". */
  const ONE_TIER_RUN = withPartnershipOutputs({
    tier_allocations: [
      { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
      { label: 'Preferred Return', kind: 'preferred', gp_amount: 200_000, lp_amount: 1_800_000, total_amount: 2_000_000 },
      { label: 'Tier 1 (to 12%)', kind: 'promote', gp_amount: 2_500_000, lp_amount: 8_000_000, total_amount: 10_500_000 },
    ],
    catch_up_amount: 0,
  });

  /** The allocation preview rows on the Summary, by what they display. */
  const previewRowTexts = () => screen.getAllByTestId('allocation-row').map((r) => r.textContent ?? '');

  it('the preview shows the band under the tier table’s name, never the engine’s "Tier 1 (to 12%)"', async () => {
    sources.payload = ONE_TIER_PAYLOAD;
    activeOutputs = ONE_TIER_RUN;
    renderWithSources();
    const tiers = screen.getByTestId('summary-waterfall-tiers');
    expect(await within(tiers).findByText('Promote — to 12% LP IRR')).toBeInTheDocument();

    const rows = previewRowTexts();
    expect(rows.some((t) => t.includes('Tier 1 (to 12%)'))).toBe(false);
    const promoteRow = screen.getAllByTestId('allocation-row').find((r) => r.textContent?.includes('Promote — to 12% LP IRR'));
    expect(promoteRow, 'preview row named as the tier table names it').toBeTruthy();
    // Matched to the engine row: the engine's dollars, reached, no caption.
    expect(promoteRow).toHaveAttribute('data-status', 'reached');
    expect(promoteRow).toHaveTextContent('$10,500,000');
    expect(promoteRow).not.toHaveTextContent('not reached');
    // Structural rows carry the tier table's names too.
    expect(rows.some((t) => t.includes('Tier I — Return of Capital'))).toBe(true);
    expect(rows.some((t) => t.includes('Tier II — Preferred Return'))).toBe(true);
  });

  it('Initial Promote Split reads "20% GP" and Additional Hurdles "None"', async () => {
    sources.payload = ONE_TIER_PAYLOAD;
    activeOutputs = ONE_TIER_RUN;
    renderWithSources();
    await waitFor(() => expect(rowFor('Initial Promote Split')).toHaveTextContent('20% GP'));
    expect(rowFor('Initial Promote Split')).not.toHaveTextContent('—');
    expect(rowFor('Additional Hurdles')).toHaveTextContent('None');
    expect(rowFor('Additional Hurdles')).not.toHaveTextContent('12%');
  });

  it('a two-tier stack: Initial Promote Split is the FIRST band, Additional Hurdles the second band’s hurdle', async () => {
    // The carried two-tier stack (13% / 20% GP, then 18% / 35% GP) on a run
    // the engine keyed "Tier 1 (to 13%)" / "Tier 2 (>13%)".
    activeOutputs = withPartnershipOutputs({
      tier_allocations: [
        { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
        { label: 'Tier 1 (to 13%)', kind: 'promote', gp_amount: 500_000, lp_amount: 2_000_000, total_amount: 2_500_000 },
        { label: 'Tier 2 (>13%)', kind: 'promote', gp_amount: 3_500_000, lp_amount: 6_500_000, total_amount: 10_000_000 },
      ],
      catch_up_amount: 0,
    });
    renderWithSources();
    await waitFor(() => expect(rowFor('Initial Promote Split')).toHaveTextContent('20% GP'));
    expect(rowFor('Additional Hurdles')).toHaveTextContent('18% LP IRR · +1 tier');

    const rows = previewRowTexts();
    expect(rows.some((t) => t.includes('Promote — to 13% LP IRR'))).toBe(true);
    expect(rows.some((t) => t.includes('Promote — above 13% LP IRR'))).toBe(true);
    expect(rows.some((t) => /Tier [12] \(/.test(t))).toBe(false);
  });
});

/**
 * Sam QA 2026-11-27 (deal b67bfdee) — the Allocation Preview footnote said
 * "above the preferred return and catch-up" on a waterfall with no catch-up
 * tier. The sentence names a catch-up only when the structure has one
 * (`hasCatchUp`: a `catch_up` tier allocation or a positive `catch_up_amount`).
 */
describe('PartnershipTab — Allocation Preview footnote names the catch-up only when there is one', () => {
  const footnote = () => screen.getByText(/Allocation of all projected distributions/);

  it('with a catch-up tier: "…above the preferred return and catch-up."', () => {
    render(<PartnershipTab />);
    expect(footnote()).toHaveTextContent(
      'Allocation of all projected distributions across the 5-year hold. The promote applies only to residual proceeds above the preferred return and catch-up.',
    );
  });

  it('without a catch-up (no catch_up tier, catch_up_amount 0): "…above the preferred return."', () => {
    activeOutputs = withPartnershipOutputs({
      catch_up_amount: 0,
      tier_allocations: [
        { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
        { label: 'Preferred Return', kind: 'preferred', gp_amount: 200_000, lp_amount: 1_800_000, total_amount: 2_000_000 },
        { label: 'Promote — above 15% LP IRR', kind: 'promote', gp_amount: 2_000_000, lp_amount: 8_000_000, total_amount: 10_000_000 },
      ],
    });
    render(<PartnershipTab />);
    expect(footnote()).toHaveTextContent(
      'Allocation of all projected distributions across the 5-year hold. The promote applies only to residual proceeds above the preferred return.',
    );
    expect(footnote()).not.toHaveTextContent('catch-up');
  });
});
