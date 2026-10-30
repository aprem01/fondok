/**
 * Methodology — Sam #6 / FON-96: how a deal's market / submarket is chosen,
 * what the stored key is (a Fondok taxonomy key, never a vendor id), the
 * licensing gate and the exact-match fill from the OM. Shipped with the
 * behaviour so the claim cannot drift.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/components/methodology/EngineeringMethodology';
import ClientMethodologyPage from '@/app/methodology/page';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

describe('Methodology — market / submarket (FON-96)', () => {
  it('engineering page: keys, selection rule, licensing gate, OM exact-match fill', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('market-submarket-methodology');
    expect(p).toHaveTextContent('These are three separate deal fields');
    expect(p).toHaveTextContent('CoStar geography export');
    expect(p).toHaveTextContent('costar:<slug of market>');
    expect(p).toHaveTextContent('str_market_id');
    expect(p).toHaveTextContent('holds a Fondok key, not a vendor id');
    expect(p).toHaveTextContent('matches exactly one entry');
    expect(p).toHaveTextContent('submarket_not_in_market');
    expect(p).toHaveTextContent('MARKET_TAXONOMY_SERVE_EXTERNAL');
    expect(p).toHaveTextContent('public 62-name STR market list');
    expect(p).toHaveTextContent('equals exactly one metro market name');
    expect(p).toHaveTextContent('never a regional market, never two candidates');
    expect(p).toHaveTextContent('om_exact_match');
    expect(p).toHaveTextContent('never overwritten');
    expect(p).toHaveTextContent('never shown as a vendor identifier');
    expect(p).toHaveTextContent('never re-runs the model');
    expect(p).not.toHaveTextContent('STR id');
  });

  it('client page: the same rules in plain language, no keys or code', () => {
    render(<ClientMethodologyPage />);
    const p = screen.getByTestId('market-submarket-methodology');
    expect(p).toHaveTextContent('three separate deal fields');
    expect(p).toHaveTextContent('CoStar market list is available to your workspace');
    expect(p).toHaveTextContent('each shown with its market');
    expect(p).toHaveTextContent('recorded as analyst-entered');
    expect(p).toHaveTextContent("depends on CoStar's licence");
    expect(p).toHaveTextContent('public list of STR market names');
    expect(p).toHaveTextContent('match exactly one metro market');
    expect(p).toHaveTextContent('regional market is never inferred from a city');
    expect(p).toHaveTextContent('a value you entered is never replaced');
    expect(p).toHaveTextContent('never shows you a vendor identifier');
    expect(p).not.toHaveTextContent('costar:');
    expect(p).not.toHaveTextContent('str_market_id');
    expect(p).not.toHaveTextContent('STR id');
  });
});
