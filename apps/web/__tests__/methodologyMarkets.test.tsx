/**
 * Methodology — Sam #6: how a deal's market / submarket is chosen and what
 * the STR id means. Shipped with the behaviour so the claim cannot drift.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/components/methodology/EngineeringMethodology';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

describe('Methodology — market / submarket (Sam #6)', () => {
  it('states the selection rule, the fallback, and what str_market_id means', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('market-submarket-methodology');
    expect(p).toHaveTextContent('These are three separate deal fields');
    expect(p).toHaveTextContent('Until it is loaded — which is the case today');
    expect(p).toHaveTextContent('the Submarket field suggests nothing');
    expect(p).toHaveTextContent('str_market_id');
    expect(p).toHaveTextContent("matches exactly one market in STR's loaded list");
    expect(p).toHaveTextContent('the worker refuses any id that is not in the loaded list');
    expect(p).toHaveTextContent('Fondok never invents an STR identifier or a submarket name');
    expect(p).toHaveTextContent('never re-runs the model');
  });
});
