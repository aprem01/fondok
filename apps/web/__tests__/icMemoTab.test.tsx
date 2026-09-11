/**
 * IC Memo tab — canonical rebuild (FON-54 / FON-72) + FON-54a (Sam's finding:
 * duplicate machine-named variance flags with implausibly large "NOI impact";
 * the recommendation shown as an inference). These tests lock:
 *
 *  1. IC RECOMMENDATION IS A DECISION — the banner reads "Pending analyst
 *     decision" until a verdict is selected AND confirmed; selecting persists
 *     `memo_recommendation_override` + `memo_recommendation_confirmed: false`,
 *     confirming persists `memo_recommendation_confirmed: true`. The Model
 *     Assessment card keeps the model's inferred verdict, labelled as such. A
 *     persisted confirmed verdict hydrates; a legacy unconfirmed one reads
 *     pending.
 *
 *  2. EDITABLE THESIS — Edit toggles the thesis paragraph into an editable
 *     state ("Done editing"); the "narrative only" guarantee is shown.
 *
 *  3. HIGHLIGHTS — "+ Add point" appends a highlight and persists the list to
 *     `field_overrides.memo_highlights`; the ••• "Move down" action reorders it.
 *
 *  4. DILIGENCE (FON-54a) — one item per concept with a business-readable
 *     title; "Estimated NOI impact $X" ONLY for an NOI-basis flag, a revenue
 *     line says "NOI impact not estimated"; the raw paths + rule ids sit under
 *     "Technical detail"; Resolve persists `memo_diligence[concept]` and a
 *     persisted status hydrates into IC readiness on reload.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import type { VarianceFlag } from '@/lib/varianceData';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// Live Base Case outputs — the whole memo reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        purchase_price: 34_000_000, price_per_key: 257_576, entry_cap_rate: 0.075,
        total_capital: 43_000_000, equity_amount: 17_000_000, debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000 },
          { label: 'Renovation Budget', amount: 4_620_000 },
        ],
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: {
        levered_irr: 0.26, unlevered_irr: 0.13, equity_multiple: 2.5,
        hold_years: 5, gross_sale_price: 52_000_000,
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    revenue: {
      deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, revpar: 204, adr: 280, occupancy: 0.73, total_revenue: 14_000_000 }], total_revenue_cagr: 0.03 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    debt: {
      deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
      outputs: { year_one_dscr: 1.59, year_one_debt_yield: 0.11, interest_rate: 0.0766 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
  },
} as unknown as EngineOutputsResponse;

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});

// Per-test fixtures — hoisted so the mock factories can read the live value.
const fx = vi.hoisted(() => ({
  fieldOverrides: {} as Record<string, unknown>,
  flags: [] as unknown[],
  refreshDealSpy: vi.fn(),
}));

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, brand: 'Kimpton', field_overrides: fx.fieldOverrides },
    status: null, loading: false, error: null, fromMock: false, refresh: fx.refreshDealSpy,
  }),
}));

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({
    flags: fx.flags,
    critical: (fx.flags as { severity: string }[]).filter((f) => f.severity === 'CRITICAL').length,
    warn: 0, info: 0, note: null, loading: false, error: null,
  }),
}));

// A consolidated revenue-line flag exactly as `mapWorkerFlag` produces it from
// the FON-54a worker contract (three raw paths → one concept).
const ROOMS_FLAG = {
  flag_id: 'BROKER_VS_T12_NOI_VARIANCE-0', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'CRITICAL',
  metric: 'rooms_revenue', field_label: 'Rooms revenue',
  broker_value: 12_950_000, t12_value: 12_300_000, variance_abs: -650_000, variance_pct: -0.0528,
  format: 'currency', broker_overstates: true, noi_impact_usd: 0,
  explanation: 'Rooms revenue: broker proforma $12,950,000 vs T-12 actual $12,300,000 — broker overstates the T-12 by 5.3%.',
  recommended_action: 'Review the cited T-12 line and re-underwrite the broker assumption.',
  source_documents: [{ document_id: 'deal-uuid-1', page: 14, field: 'rooms_revenue' }],
  concept: 'rooms_revenue', impact_basis: 'revenue',
  raw_fields: [
    { field: 'broker_proforma.rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Warn', broker: 12_900_000, actual: 12_300_000, source_page: 14 },
    { field: 'broker.rooms_revenue', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Critical', broker: 12_950_000, actual: 12_300_000 },
    { field: 'rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Info', broker: 12_400_000, actual: 12_300_000 },
  ],
} as unknown as VarianceFlag;

const NOI_FLAG = {
  flag_id: 'BROKER_VS_T12_NOI_VARIANCE-1', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'CRITICAL',
  metric: 'noi', field_label: 'NOI',
  broker_value: 5_200_000, t12_value: 4_181_000, variance_abs: -1_019_000, variance_pct: -0.2437,
  format: 'currency', broker_overstates: true, noi_impact_usd: 1_019_000,
  explanation: 'NOI: broker proforma $5,200,000 vs T-12 actual $4,181,000 — broker overstates the T-12 by 24.4%.',
  recommended_action: 'Review the cited T-12 line and re-underwrite the broker assumption.',
  source_documents: [],
  concept: 'noi', impact_basis: 'noi',
  raw_fields: [{ field: 'broker_proforma.noi_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Critical', broker: 5_200_000, actual: 4_181_000 }],
} as unknown as VarianceFlag;

// api surface — spy the field_overrides PATCH; serve empty scenarios.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      scenarios: { ...actual.api.scenarios, list: vi.fn(async () => []), compare: vi.fn(async () => ({ deal_id: 'deal-uuid-1', base_scenario_id: null, scenarios: [] })) },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import { REASONS } from '@/lib/ontology/reasons.generated';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;

function lastOverrides(): Record<string, unknown> {
  const call = updateSpy.mock.calls[updateSpy.mock.calls.length - 1] as unknown[];
  const patch = call?.[1] as { field_overrides?: Record<string, unknown> } | undefined;
  return patch?.field_overrides ?? {};
}

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  fx.refreshDealSpy.mockClear();
  fx.fieldOverrides = {};
  fx.flags = [ROOMS_FLAG];
});

describe('ICMemoTab — IC recommendation is a decision, not an inference', () => {
  it('reads "Pending analyst decision" until a verdict is selected AND confirmed, persisting both steps', async () => {
    render(<ICMemoTab project={PROJECT} />);

    // Strong returns → the MODEL says Proceed, but only on the assessment card.
    expect(screen.getByText('Clears Hurdles')).toBeInTheDocument();
    expect(screen.getByText(/Model-inferred: Proceed — the model.s assessment, not the IC decision/)).toBeInTheDocument();
    // The recommendation itself is pending — the inferred verdict is NOT shown as the decision.
    expect(screen.getByText('Pending analyst decision')).toBeInTheDocument();
    expect(screen.getByText('Select a verdict, then confirm to record the decision')).toBeInTheDocument();
    expect(screen.getByText('IC recommendation pending analyst decision')).toBeInTheDocument();

    // Select a verdict → persisted as selected-but-unconfirmed; still pending.
    fireEvent.click(screen.getByText('Pending analyst decision'));
    fireEvent.click(screen.getByText('Do Not Proceed'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(lastOverrides().memo_recommendation_override).toBe('Do Not Proceed');
    expect(lastOverrides().memo_recommendation_confirmed).toBe(false);
    expect(screen.getByText('Pending analyst decision')).toBeInTheDocument();
    expect(screen.getByText('Do Not Proceed selected — confirm to record the decision')).toBeInTheDocument();

    // Confirm → the decision is recorded and shown.
    fireEvent.click(screen.getByText('Pending analyst decision'));
    fireEvent.click(screen.getByText('Confirm recommendation'));
    await waitFor(() => expect(lastOverrides().memo_recommendation_confirmed).toBe(true));
    expect(lastOverrides().memo_recommendation_override).toBe('Do Not Proceed');
    expect(screen.getByText('Do Not Proceed')).toBeInTheDocument();
    expect(screen.getByText('✓ Analyst confirmed')).toBeInTheDocument();
    expect(screen.getByText('IC recommendation confirmed by analyst')).toBeInTheDocument();
    expect(screen.queryByText('Pending analyst decision')).not.toBeInTheDocument();
  });

  it('cannot confirm before a verdict is selected', () => {
    render(<ICMemoTab project={PROJECT} />);
    fireEvent.click(screen.getByText('Pending analyst decision'));
    fireEvent.click(screen.getByText('Confirm recommendation'));
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByText('Pending analyst decision')).toBeInTheDocument();
  });

  it('hydrates a persisted confirmed verdict, and reads a legacy unconfirmed one as pending', () => {
    fx.fieldOverrides = { memo_recommendation_override: 'Proceed with Conditions', memo_recommendation_confirmed: true };
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('Proceed with Conditions')).toBeInTheDocument();
    expect(screen.getByText('✓ Analyst confirmed')).toBeInTheDocument();
    cleanup();

    // A verdict persisted before the confirm flag existed is not a recorded decision.
    fx.fieldOverrides = { memo_recommendation_override: 'Proceed with Conditions' };
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('Pending analyst decision')).toBeInTheDocument();
    expect(screen.getByText('Proceed with Conditions selected — confirm to record the decision')).toBeInTheDocument();
  });
});

// Phase 4.4 — the pending recommendation is a REFUSAL carrying a machine
// code, not a loose string. The words on screen are unchanged in both
// directions: `REASONS.awaiting_analyst.label` IS "Pending analyst decision".
describe('ICMemoTab — the pending recommendation carries its reason code', () => {
  it('with the code ABSENT, falls back to awaiting_analyst and prints the canonical string', async () => {
    fx.fieldOverrides = {}; // every worker build today
    render(<ICMemoTab project={PROJECT} />);
    const refusal = screen.getByTestId('ic-recommendation-refusal');
    expect(refusal).toHaveTextContent('Pending analyst decision');
    expect(refusal.getAttribute('data-refused')).toBe('awaiting_analyst');
    expect(refusal.getAttribute('aria-label')).toBe(REASONS.awaiting_analyst.label);
    expect(REASONS.awaiting_analyst.label).toBe('Pending analyst decision');

    // The tooltip is the only thing that is new — the copy is the ontology's.
    fireEvent.mouseEnter(refusal);
    const tip = await screen.findByRole('tooltip');
    expect(tip).toHaveTextContent(REASONS.awaiting_analyst.explanation);
  });

  it('mirrors the worker: a selected-but-unconfirmed verdict is still pending', () => {
    // The worker derives `recommendation_reason` on the MEMO ENVELOPE from
    // these same two persisted keys (memo_overrides.ic_recommendation_reason);
    // it is never written into field_overrides, so the web derives it from the
    // same inputs rather than reading a key that will never exist.
    fx.fieldOverrides = { memo_recommendation_override: 'Proceed' };
    render(<ICMemoTab project={PROJECT} />);
    const refusal = screen.getByTestId('ic-recommendation-refusal');
    expect(refusal.getAttribute('data-refused')).toBe('awaiting_analyst');
    expect(refusal).toHaveTextContent('Pending analyst decision');
  });

  it('a stray recommendation_reason in field_overrides is ignored', () => {
    // Guards the contract: the worker confirmed it never persists this key,
    // so a value found there must not be able to relabel the banner.
    fx.fieldOverrides = { recommendation_reason: 'needs_review' };
    render(<ICMemoTab project={PROJECT} />);
    const refusal = screen.getByTestId('ic-recommendation-refusal');
    expect(refusal.getAttribute('data-refused')).toBe('awaiting_analyst');
    expect(refusal).toHaveTextContent('Pending analyst decision');
  });

  it('a confirmed verdict is a decision, not a refusal — no refusal node at all', () => {
    fx.fieldOverrides = { memo_recommendation_override: 'Proceed', memo_recommendation_confirmed: true };
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.queryByTestId('ic-recommendation-refusal')).toBeNull();
    expect(screen.getByText('✓ Analyst confirmed')).toBeInTheDocument();
  });
});

describe('ICMemoTab — editable investment thesis', () => {
  it('toggles into an editable state and shows the narrative-only guarantee', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText(/Narrative only — editing this text never changes an underwriting assumption\./)).toBeInTheDocument();

    // Edit → the affordance flips to "Done editing".
    fireEvent.click(screen.getByText('Edit'));
    expect(screen.getByText('Done editing')).toBeInTheDocument();
    // A Regenerate affordance is present alongside.
    expect(screen.getByText('Regenerate')).toBeInTheDocument();
  });
});

describe('ICMemoTab — highlights: add + reorder', () => {
  it('appends a highlight and persists the list', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // The first "+ Add point" belongs to Key highlights (rendered before risks).
    const addButtons = screen.getAllByText('+ Add point');
    fireEvent.click(addButtons[0]);

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const points = lastOverrides().memo_highlights as { t: string }[];
    expect(Array.isArray(points)).toBe(true);
    // 5 derived highlights + 1 appended.
    expect(points.length).toBe(6);
    expect(points[points.length - 1].t).toMatch(/New highlight/);
  });

  it('reorders a highlight via the ••• Move down action', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // First highlight row's overflow menu.
    const more = screen.getAllByTitle('More')[0];
    fireEvent.click(more);
    fireEvent.click(screen.getByText('Move down'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const points = lastOverrides().memo_highlights as { t: string }[];
    // The original first highlight (levered IRR) moved into slot 2.
    expect(points[1].t).toMatch(/Levered IRR/);
  });
});

describe('ICMemoTab — diligence (FON-54a consolidated flags)', () => {
  it('titles a revenue-line flag by concept and never prints a dollar NOI impact for it', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(screen.getByText('Rooms revenue — broker overstates T-12 by 5.3%')).toBeInTheDocument();
    expect(
      screen.getByText('Rooms revenue: broker materials report $12.9M against $12.3M in the trailing-twelve-month operating statements.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Revenue-line variance — NOI impact not estimated')).toBeInTheDocument();
    expect(screen.queryByText(/Estimated NOI impact/)).not.toBeInTheDocument();
    // No raw extractor path is used as an IC-facing title.
    expect(screen.queryByText(/broker_proforma\./)).not.toBeInTheDocument();
  });

  it('prints "Estimated NOI impact $X" only for an NOI-basis flag', () => {
    fx.flags = [ROOMS_FLAG, NOI_FLAG];
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('NOI — broker overstates T-12 by 24.4%')).toBeInTheDocument();
    expect(screen.getByText(/^Estimated NOI impact \$1\.0M/)).toBeInTheDocument();
    // Exactly one dollar impact on the page — the revenue line still says not estimated.
    expect(screen.getAllByText(/Estimated NOI impact/)).toHaveLength(1);
    expect(screen.getByText('Revenue-line variance — NOI impact not estimated')).toBeInTheDocument();
    expect(screen.getByText(/2 critical diligence items remain open/)).toBeInTheDocument();
  });

  it('lists the raw paths + rule ids under Technical detail', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.queryByText(/broker\.rooms_revenue/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Technical detail'));
    const detail = screen.getByText(/concept rooms_revenue · impact basis revenue · 3 raw fields consolidated/);
    expect(detail.textContent).toContain('rule BROKER_VS_T12_NOI_VARIANCE · field broker_proforma.rooms_revenue_usd · broker $12,900,000 vs T-12 $12,300,000 · p.14');
    expect(detail.textContent).toContain('field broker.rooms_revenue · broker $12,950,000 vs T-12 $12,300,000');
    expect(detail.textContent).toContain('field rooms_revenue_usd · broker $12,400,000 vs T-12 $12,300,000');
    expect(screen.getByText('Hide technical detail')).toBeInTheDocument();
  });

  it('resolves an open critical item and persists the status keyed by concept', async () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText(/1 critical diligence item remain open/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Resolve'));

    // Status flips, the summary clears, and the readiness checklist follows.
    expect(screen.getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText('All critical diligence items resolved')).toBeInTheDocument();
    expect(screen.getByText('Critical diligence items resolved')).toBeInTheDocument();
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const dil = lastOverrides().memo_diligence as Record<string, { status: string; updated_at?: string; details?: unknown }>;
    expect(dil.rooms_revenue.status).toBe('Resolved');
    expect(typeof dil.rooms_revenue.updated_at).toBe('string');
    expect('details' in dil.rooms_revenue).toBe(false); // UI-only state never persisted

    // Reopen is available and persists too.
    fireEvent.click(screen.getByText('Reopen'));
    await waitFor(() => {
      const again = lastOverrides().memo_diligence as Record<string, { status: string }>;
      expect(again.rooms_revenue.status).toBe('Open');
    });
    expect(screen.getByText(/1 critical diligence item remain open/)).toBeInTheDocument();
  });

  it('reports a basis mismatch for review instead of a variance, and discloses excluded rows', () => {
    const MISMATCH_FLAG = {
      ...ROOMS_FLAG,
      flag_id: 'BROKER_VS_T12_OCC_VARIANCE-2', rule_id: 'BROKER_VS_T12_OCC_VARIANCE', severity: 'INFO',
      metric: 'occupancy', field_label: 'Occupancy', format: 'percent',
      broker_value: 83, t12_value: 0.716, variance_abs: -82.284, variance_pct: 82.284,
      broker_overstates: true, noi_impact_usd: 0, concept: 'occupancy', impact_basis: 'revenue',
      basis_mismatch: true,
      explanation: 'Basis mismatch — needs review: broker 83 vs T-12 0.716 on ttm_summary_per_om.occupancy_pct are not on the same basis (8,228% apart). No variance severity assigned.',
      raw_fields: [
        { field: 'ttm_summary_per_om.occupancy_pct', rule_id: 'BROKER_VS_T12_OCC_VARIANCE', severity: 'Info', broker: 83, actual: 0.716, source_doc_type: 'OM', source_document: 'Anglers OM.pdf', basis_mismatch: true },
        { field: 'ttm_performance.segment.luxury_upper_upscale.occupancy_pct', severity: 'Info', broker: 0.741, source_doc_type: 'OM', source_document: 'Anglers OM.pdf', excluded_reason: 'market-segment stat, not the broker\'s claim about the subject' },
        { field: 'ttm_summary_per_om.occupancy_pct', severity: 'Info', broker: 0.72, source_doc_type: 'PNL', source_document: '2023 P&L.xlsx', excluded_reason: 'from an actuals document (PNL) — a T-12 / P&L line, not a broker claim' },
      ],
    } as unknown as VarianceFlag;
    fx.flags = [MISMATCH_FLAG];
    render(<ICMemoTab project={PROJECT} />);

    expect(screen.getByText('Occupancy — basis mismatch, needs review')).toBeInTheDocument();
    expect(screen.getByText(/^Basis mismatch — needs review: broker 83 vs T-12 0\.716/)).toBeInTheDocument();
    expect(screen.getByText('Basis mismatch — the broker and T-12 figures are not on the same basis; no variance severity assigned')).toBeInTheDocument();
    // Info severity → not a critical diligence item, nothing "overstates".
    expect(screen.getByText('Minor')).toBeInTheDocument();
    expect(screen.queryByText(/overstates T-12/)).not.toBeInTheDocument();
    expect(screen.getByText('All critical diligence items resolved')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Technical detail'));
    const detail = screen.getByText(/concept occupancy · impact basis revenue · 1 raw field consolidated · 2 excluded/);
    expect(detail.textContent).toContain('field ttm_summary_per_om.occupancy_pct · broker 8300.0% vs T-12 71.6% · source OM Anglers OM.pdf · basis mismatch');
    expect(detail.textContent).toContain('excluded · field ttm_performance.segment.luxury_upper_upscale.occupancy_pct · value 74.1% · source OM Anglers OM.pdf · market-segment stat');
    expect(detail.textContent).toContain('excluded · field ttm_summary_per_om.occupancy_pct · value 72.0% · source PNL 2023 P&L.xlsx · from an actuals document (PNL)');
  });

  it('reads a persisted diligence status on reload into IC readiness', () => {
    fx.fieldOverrides = { memo_diligence: { rooms_revenue: { status: 'Accepted', updated_at: '2026-09-10T00:00:00Z' } } };
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.getByText('Rooms revenue — broker overstates T-12 by 5.3% — variance accepted')).toBeInTheDocument();
    expect(screen.getByText('All critical diligence items resolved')).toBeInTheDocument();
    expect(screen.getByText('Critical diligence items resolved')).toBeInTheDocument();
    expect(screen.queryByText('Resolve')).not.toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled(); // hydration never writes back
  });
});


/**
 * FON-54 §7 (Sam 09-11) — the Preview Memo / "✓ Preview reviewed" action and the
 * "IC memo previewed and reviewed" checklist item are cut for MVP: clicking
 * Preview rendered no artifact, it only flipped a flag, while IC Memo .pdf and
 * Deal Presentation .pptx are explicitly Coming Soon. Configure IC memo (format +
 * included sections) stays, because it preserves the future export configuration.
 */
describe('ICMemoTab — no Preview Memo step (FON-54 §7)', () => {
  /** The IC-readiness checklist labels, in render order. */
  function readinessChecklist(): string[] {
    const header = screen.getByText('Ready for Investment Committee?');
    const cardEl = header.parentElement!.parentElement!; // span → header row → card
    const grid = (cardEl.children[1] as HTMLElement).children[0] as HTMLElement;
    return Array.from(grid.children).map((el) => (el.textContent ?? '').replace(/^[✓⚠]\s*/, '').trim());
  }

  it('the IC-readiness checklist has exactly 6 items and none mentions preview', () => {
    render(<ICMemoTab project={PROJECT} />);
    const items = readinessChecklist();
    expect(items).toHaveLength(6);
    expect(items).toEqual([
      'Base model run complete',
      'Required underwriting sections complete',
      'Returns calculated',
      'Scenario analysis not yet available',
      '1 critical diligence item unresolved',
      'IC recommendation pending analyst decision',
    ]);
    for (const label of items) expect(label).not.toMatch(/preview/i);
  });

  it('renders no Preview Memo action', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.queryByText(/Preview Memo/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Preview reviewed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/previewed/i)).not.toBeInTheDocument();
    // The export cards are untouched: Excel live, PDF/PPTX still Coming Soon.
    expect(screen.getAllByText(/Coming Soon/).length).toBeGreaterThan(0);
  });

  it('can mark IC Ready with an acknowledged critical without any preview step', () => {
    render(<ICMemoTab project={PROJECT} />);
    const markReady = screen.getByRole('button', { name: 'Mark as IC Ready' });
    expect(markReady).toBeDisabled();

    fireEvent.click(screen.getByText('Acknowledge the unresolved critical items and proceed to committee'));
    expect(markReady).not.toBeDisabled();

    fireEvent.click(markReady);
    expect(screen.getByText('✓ IC Ready · 1 acknowledged critical item')).toBeInTheDocument();
  });

  it('keeps Configure IC memo — the format toggle and all six section toggles', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText('Configure IC memo')).toBeInTheDocument();
    for (const f of ['Condensed', 'Standard', 'Expanded']) {
      expect(screen.getByRole('button', { name: f })).toBeInTheDocument();
    }
    const sections = [
      'Deal Summary', 'Investment Thesis', 'Highlights & Risks',
      'Underwriting Summary', 'Scenario Summary', 'Diligence & Open Items',
    ];
    for (const label of sections) {
      // The accessible name carries the ✓ glyph while the section is included.
      expect(screen.getByRole('checkbox', { name: new RegExp(`${label}$`) })).toHaveAttribute('aria-checked', 'true');
    }
    // Six section toggles and no seventh control in the Configure card.
    const configCard = screen.getByText('Configure IC memo').parentElement!.parentElement!;
    expect(configCard.querySelectorAll('[role="checkbox"]')).toHaveLength(6);
    // Toggling a section still works now that the preview reset is gone.
    const deal = screen.getByRole('checkbox', { name: /Deal Summary$/ });
    fireEvent.click(deal);
    expect(screen.getByRole('checkbox', { name: /Deal Summary$/ })).toHaveAttribute('aria-checked', 'false');
  });
});
