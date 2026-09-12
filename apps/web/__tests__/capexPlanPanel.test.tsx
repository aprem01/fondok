/**
 * CapexPlanPanel — FON-44 §3, "two things are called PIP".
 *
 * Sam (9/11) found two sections both called PIP, one at $5.676M and one at $0,
 * and could not tell whether the second double-counted the first.
 *
 * FOUNDER DECISION: they are genuinely different buckets.
 *   · The INITIAL renovation / PIP funds AT CLOSE and is a line in Sources &
 *     Uses (`capital.py` -> the `Renovation` use).
 *   · This panel phases capital OUT OF OPERATIONS across the hold and never
 *     enters Sources & Uses.
 *
 * So the fix is naming and copy — no number moves. This file pins the naming,
 * because the regression it prevents is a silent one: rename this section back
 * to "PIP" and the ambiguity returns with no test going red anywhere else.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

vi.mock('@/components/help/AssumptionBadge', () => ({ AssumptionBadge: () => null }));

import CapexPlanPanel, { DEFAULT_CAPEX_PLAN } from '@/components/project/CapexPlanPanel';

function renderPanel() {
  return render(
    <CapexPlanPanel
      keys={132}
      revenueByYear={[10_000_000, 11_000_000, 12_000_000, 13_000_000, 14_000_000]}
      holdYears={5}
      state={DEFAULT_CAPEX_PLAN}
      onChange={() => {}}
    />,
  );
}

describe('CapexPlanPanel — the hold-period bucket says which PIP it is', () => {
  it('names the section for the hold period, not just "PIP"', () => {
    renderPanel();
    expect(screen.getByText('Additional / Hold-Period PIP')).toBeInTheDocument();
    expect(screen.getByText('Hold-Period Capex Plan')).toBeInTheDocument();
    // The two ambiguous labels Sam read as the same bucket are gone.
    expect(screen.queryByText('PIP (Property Improvement Plan)')).toBeNull();
    expect(screen.queryByText('Total PIP')).toBeNull();
    expect(screen.getByText('Total hold-period PIP')).toBeInTheDocument();
  });

  it('states that none of it enters Sources & Uses, and that the day-one bucket does', () => {
    renderPanel();
    expect(
      screen.getByText(/None of it enters Sources & Uses — the day-one Initial Renovation \/ PIP above does, once\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/separate from, and not a re-statement of,\s*the initial renovation funded at close/i),
    ).toBeInTheDocument();
  });

  it('moves no number — the default plan still totals zero capex it did not have', () => {
    renderPanel();
    // DEFAULT_CAPEX_PLAN carries no PIP and no ROI; the only capex is the
    // non-PIP FF&E floor, exactly as before this rename.
    expect(DEFAULT_CAPEX_PLAN.pip.total_usd).toBe(0);
    expect(DEFAULT_CAPEX_PLAN.pip.enabled).toBe(false);
    expect(DEFAULT_CAPEX_PLAN.roi_projects).toEqual([]);
  });
});
