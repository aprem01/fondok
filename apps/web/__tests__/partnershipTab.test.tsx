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
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
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
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
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

beforeEach(() => {
  cleanup();
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

    // One row per typed tier (labels straight from the engine envelope).
    expect(screen.getAllByText('Return of Capital').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Preferred Return').length).toBeGreaterThan(0);
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
    // Summary allocation row.
    expect(screen.getAllByText('GP Catch-Up').length).toBeGreaterThan(0);

    // Waterfall sub-tab: the typed Promote Waterfall carries the Catch-Up tier.
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    expect(screen.getByText('Allocation of Projected Proceeds')).toBeInTheDocument();
    expect(screen.getByText('Promote Waterfall')).toBeInTheDocument();
    expect(screen.getByText('Tier I — Return of Capital')).toBeInTheDocument();
    expect(screen.getByText('Tier II — Preferred Return')).toBeInTheDocument();
    expect(screen.getByText('Tier III — GP Catch-Up')).toBeInTheDocument();
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

  it('puts the → Investment link on the INITIAL row only', () => {
    activeOutputs = withAdditional();
    render(<PartnershipTab />);

    const link = within(summaryRow('Initial Equity Required')).getByRole('link', { name: '→ Investment' });
    expect(link.getAttribute('href')).toBe('?tab=investment&sub=sources-and-uses');
    // Exactly one — neither the additional nor the total row claims Investment.
    expect(screen.getAllByRole('link', { name: '→ Investment' })).toHaveLength(1);
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
  function openWaterfall() {
    render(<PartnershipTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
  }
  const removeBtn = () => screen.getByRole('button', { name: 'Remove promote tier 1' });

  it('Remove is an icon with an accessible label, hidden until the row is hovered', () => {
    openWaterfall();
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

  it('keyboard focus reveals it too', () => {
    openWaterfall();
    fireEvent.focus(removeBtn());
    expect(removeBtn().style.opacity).toBe('1');
  });

  it('"Add tier" is a row of the table, on the same grid as the tiers', () => {
    openWaterfall();
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

  it('keeps GP split and LP split as two independently editable columns', () => {
    // FOUNDER DECISION — Sam's optional "consider presenting each economic
    // split together (20% GP / 80% LP)" is DECLINED: two right-aligned numeric
    // columns scan down a table better than a combined string, and both halves
    // are independently editable, so collapsing them would cost an edit target
    // to save a column.
    openWaterfall();
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
  it('the "→ Investment" link deep-links to Sources & Uses (FON-66)', () => {
    render(<PartnershipTab />);
    const a = screen.getByRole('link', { name: '→ Investment' });
    expect(a.getAttribute('href')).toBe('?tab=investment&sub=sources-and-uses');
  });
});
