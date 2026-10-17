/**
 * Methodology — FON-101 renovation budget precedence + Future CapEx reference-only.
 *
 * The page must state the chain the engine-input loader runs (analyst
 * override > latest-dated PIP / CapEx document > OM broker > seed) and that
 * Future CapEx documents are never incorporated — in the same commit as the
 * behaviour, so the claim and the code cannot drift apart.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/app/methodology/page';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

describe('Methodology — renovation budget precedence (FON-101)', () => {
  it('states the precedence chain in order and names the Future CapEx reference-only rule', () => {
    render(<MethodologyPage />);
    const card = screen.getByTestId('methodology-renovation-budget-precedence');
    expect(within(card).getByText('Renovation budget — which document the model reads')).toBeInTheDocument();

    const steps = within(card).getAllByRole('listitem').map((li) => li.textContent ?? '');
    expect(steps[0]).toMatch(/^1Analyst override/);
    expect(steps[1]).toMatch(/^2PIP \/ CapEx document — latest plan date/);
    expect(steps[2]).toMatch(/^3OM broker pro forma/);
    expect(steps[3]).toMatch(/^4Seed/);
    expect(steps[1]).toContain('capex_plan.pip.total_usd');
    expect(steps[1]).toContain('capex_plan.document.plan_date');
    expect(steps[1]).toContain('outranks the OM');

    expect(card).toHaveTextContent('CAPEX → Initial Renovation / PIP → Renovation budget sources');
    expect(card).toHaveTextContent('GET /deals/{id}/renovation-budget-candidates');
    expect(card).toHaveTextContent('more than 2% away from the sum of its own scope items is flagged');

    const future = screen.getByTestId('methodology-future-capex-reference-only');
    expect(future).toHaveTextContent('Historic and Future CapEx are reference only.');
    expect(future).toHaveTextContent('never incorporated into the renovation budget');
    expect(future).toHaveTextContent('Reference only — not in the model');
    expect(future).toHaveTextContent('historic_capex.*');
    expect(future).toHaveTextContent('future_capex.*');
    expect(future).toHaveTextContent('re-tag it as PIP / Renovation Budget');

    // The PIP lane: the one CapEx lane that feeds the model; untagged = classifier-assigned.
    const lanes = screen.getByTestId('methodology-capex-lanes');
    expect(lanes).toHaveTextContent('Three CapEx lanes, one of which feeds the model.');
    expect(lanes).toHaveTextContent('PIP / Renovation Budget');
    expect(lanes).toHaveTextContent('only lane the renovation budget reads');
    expect(lanes).toHaveTextContent('classifier-assigned');
    expect(steps[1]).toContain('doc_subtype = pip');
  });

  it('the Year-1 displacement paragraph says no budget displaces — only explicit assumptions (Sam, 2026-10-15)', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('methodology-displacement-explicit-only');
    expect(p.textContent).toContain('Only explicit assumptions displace Year 1.');
    expect(p.textContent).toContain('never reduces Year-1 occupancy or ADR on its own');
    expect(p.textContent).toContain('y1_occupancy_displacement_pct');
    // The removed heuristic is never described as live behaviour.
    expect(p.textContent).not.toMatch(/depressed 15%|\$5,000 per key/);
    // Sam, 2026-10-15 — the explicit schedule is named as a displacement lever.
    expect(p.textContent).toContain('renovation displacement schedule');
  });

  it('the renovation displacement schedule paragraph states the arithmetic and that the budget alone never moves operations', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('methodology-displacement-schedule');
    expect(p.textContent).toContain('renovation_displacement_schedule');
    expect(p.textContent).toContain('one override with one justification note');
    // Period semantics + the per-year factor arithmetic, matching
    // engines/revenue.py::renovation_displacement_factors.
    expect(p.textContent).toContain('counted from the acquisition close');
    expect(p.textContent).toContain('every month weighs 1/12');
    expect(p.textContent).toContain('occupancy factor');
    expect(p.textContent).toContain('ADR factor');
    expect(p.textContent).toContain('open-room-weighted');
    expect(p.textContent).toContain('occupancy × 0.875 and ADR × 0.957');
    // Never double-applied; off by default; the budget alone moves nothing.
    expect(p.textContent).toContain('not applied on top');
    expect(p.textContent).toContain('every deal runs exactly as before');
    expect(p.textContent).toContain('renovation budget alone never moves');
    // Excel round-trip is mentioned.
    expect(p.textContent).toContain('Excel Assumptions sheet');
  });

  it('the status paragraph quotes the on_schedule sentence shape', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('methodology-displacement-status');
    expect(p.textContent).toContain('analyst-set displacement schedule (months 1–6: 25% rooms offline, 10% rate discount)');
    expect(p.textContent).toContain('the schedule wins');
  });
});
