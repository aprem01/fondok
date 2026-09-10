/**
 * Methodology — Section 7 "What a dash means" (Phase 0.3).
 *
 * The section must render its rows FROM the ReasonCode module
 * (`@/lib/ontology/reasons.generated`), one per code, so the page cannot
 * drift from the vocabulary the engines / exports read. Also pins that the
 * existing Sections 1–6 are still numbered as they were — Section 7 is an
 * append, not a restructure.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/app/methodology/page';
import { REASONS, REASON_CODES, REFUSAL_GLYPH } from '@/lib/ontology/reasons.generated';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

describe('Methodology — Section 7 "What a dash means"', () => {
  it('lists exactly one row per reason code, rendered from the TS module', () => {
    render(<MethodologyPage />);
    expect(screen.getByText('What a dash means')).toBeInTheDocument();
    expect(screen.getByText('Section 7')).toBeInTheDocument();

    const table = screen.getByTestId('reason-code-table');
    const rows = within(table)
      .getAllByRole('row')
      .filter((r) => r.hasAttribute('data-reason-code'));
    expect(rows).toHaveLength(Object.keys(REASONS).length);

    const codesInOrder = rows.map((r) => r.getAttribute('data-reason-code'));
    expect(codesInOrder).toEqual(Object.keys(REASONS));

    for (const code of REASON_CODES) {
      const row = within(table).getByText(code).closest('tr');
      expect(row).not.toBeNull();
      expect(within(row as HTMLElement).getByText(REASONS[code].label)).toBeInTheDocument();
      expect(within(row as HTMLElement).getByText(REASONS[code].explanation)).toBeInTheDocument();
      expect(within(row as HTMLElement).getByText(REASONS[code].ui)).toBeInTheDocument();
    }
  });

  it('keeps the vocabulary at 16 codes with one glyph today', () => {
    expect(REASON_CODES).toHaveLength(16);
    expect(Object.keys(REASONS)).toHaveLength(16);
    expect(new Set(Object.keys(REASONS))).toEqual(new Set(REASON_CODES));
    for (const code of REASON_CODES) expect(REASONS[code].ui).toBe(REFUSAL_GLYPH);
  });

  it('does not renumber Sections 1–6 and keeps 7 and 8 in place', () => {
    render(<MethodologyPage />);
    for (const n of ['1', '2', '3', '4', '5', '6', '7']) {
      expect(screen.getByText(`Section ${n}`)).toBeInTheDocument();
    }
    // Section 8 (Concept registry) landed with Phase 1; nothing beyond it yet.
    expect(screen.getByText('Section 8')).toBeInTheDocument();
    expect(screen.queryByText('Section 9')).toBeNull();
    // Anchors external "Learn more →" links jump to stay put.
    for (const id of ['extraction', 'projection', 'sources', 'engines', 'pricing', 'ic-memo', 'reasons']) {
      expect(document.getElementById(id)).not.toBeNull();
    }
  });
});
