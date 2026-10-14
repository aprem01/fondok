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
    expect(future).toHaveTextContent('Future CapEx is reference only.');
    expect(future).toHaveTextContent('never incorporated into the renovation budget');
    expect(future).toHaveTextContent('Reference only — not in the model');
  });

  it('the Year-1 displacement paragraph names the PIP document as a deal-specific budget', () => {
    render(<MethodologyPage />);
    expect(screen.getByText(/one read off a PIP \/ CapEx document \(badged "PIP doc"\)/)).toBeInTheDocument();
  });
});
