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
import { REASONS, REASON_CODES, REFUSAL_GLYPH, type ReasonCode } from '@/lib/ontology/reasons.generated';

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

  it('does not renumber Sections 1–6 and keeps the dash vocabulary at 7', () => {
    render(<MethodologyPage />);
    for (const n of ['1', '2', '3', '4', '5', '6', '7']) {
      expect(screen.getByText(`Section ${n}`)).toBeInTheDocument();
    }
    // Sections are appended as the wave lands (8 = Concept registry,
    // 9 = Every number traces back). Asserting "nothing beyond N" made this
    // test fail on every append while guarding nothing, so the guard is now
    // on the numbering that matters: 1-6 keep their titles and the dash
    // vocabulary stays at 7.
    expect(screen.getByText('Section 8')).toBeInTheDocument();
    // Anchors external "Learn more →" links jump to stay put.
    for (const id of ['extraction', 'projection', 'sources', 'engines', 'pricing', 'ic-memo', 'reasons']) {
      expect(document.getElementById(id)).not.toBeNull();
    }
  });
});


describe('Methodology - Sections 3 and 6 name their reason codes (Phase 4.5)', () => {
  /** Every tag on the page, as `code -> rendered text`. */
  function tags(): Map<string, string> {
    const out = new Map<string, string>();
    for (const el of Array.from(document.querySelectorAll('[data-reason-tag]'))) {
      const code = el.getAttribute('data-reason-tag') as string;
      out.set(code, (el.textContent || '').trim());
    }
    return out;
  }

  it('renders each tag label from the generated module, never a literal', () => {
    render(<MethodologyPage />);
    const found = tags();
    expect(found.size).toBeGreaterThan(0);

    for (const [code, text] of found) {
      // The code is real vocabulary...
      expect(REASON_CODES).toContain(code as ReasonCode);
      // ...and the label beside it is REASONS[code].label, verbatim.
      expect(text).toContain(code);
      expect(text).toContain(REASONS[code as ReasonCode].label);
    }
  });

  it('names the refusal codes Sections 3 and 6 describe', () => {
    render(<MethodologyPage />);
    const found = tags();

    // Section 3 - market-data assumptions.
    for (const code of ['str_unavailable', 'not_knowable_as_of', 'as_of_unknown', 'pin_active', 'no_document']) {
      expect(found.has(code)).toBe(true);
    }
    // Section 6 - IC memo: the decision and the variance refusals.
    for (const code of ['awaiting_analyst', 'basis_excluded', 'unit_unknown', 'period_mismatch', 'basis_mismatch']) {
      expect(found.has(code)).toBe(true);
    }
  });

  it('leaves the existing refusal sentences intact', () => {
    render(<MethodologyPage />);
    // The user-visible strings an external tester is mid-QA on do not move.
    // (`getAllByText` because Section 7's table restates some of them.)
    for (const sentence of [
      /STR rates were requested but could not populate/,
      /The IC recommendation reads/,
      /two numbers that far apart are on different bases/,
      /it shows .* until the OM is extracted/,
      /A value whose unit cannot be established/,
      /Fondok never compares a month against a year/,
    ]) {
      expect(screen.getAllByText(sentence).length).toBeGreaterThan(0);
    }
    // ...and the section numbering is untouched.
    for (const n of ['1', '2', '3', '4', '5', '6', '7', '8', '9']) {
      expect(screen.getByText(`Section ${n}`)).toBeInTheDocument();
    }
  });

  it('keeps the Section 7 table as the one place every code is listed', () => {
    render(<MethodologyPage />);
    const table = screen.getByTestId('reason-code-table');
    const rows = within(table)
      .getAllByRole('row')
      .filter((r) => r.hasAttribute('data-reason-code'));
    expect(rows).toHaveLength(REASON_CODES.length);
    // A tag never lives inside the table - the table has its own Code column.
    expect(table.querySelectorAll('[data-reason-tag]')).toHaveLength(0);
  });
});


// ───────────── The two NOI definitions (FON-59 #1 / FON-67 #2) ─────────────

describe('Methodology — the expense waterfall states BOTH NOI definitions', () => {
  it('names each basis, says which engine field carries it, and what consumes it', () => {
    render(<MethodologyPage />);

    // Both display names, spelled exactly as every tab prints them.
    expect(screen.getAllByText(/NOI \(before FF&E reserve\)/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Cash NOI \(after FF&E reserve\)/).length).toBeGreaterThan(0);

    // Both engine fields, named — so a reader can go check the number.
    expect(screen.getByText('expense.years[].noi_institutional')).toBeInTheDocument();
    expect(screen.getByText('expense.years[].noi')).toBeInTheDocument();
    // …and the registry concepts behind them (the Section-8 registry table
    // lists them too, hence getAllByText).
    expect(screen.getAllByText('ebitda').length).toBeGreaterThan(0);
    expect(screen.getAllByText('noi').length).toBeGreaterThan(0);

    // Which figure does what: before-reserve is the headline / entry-cap basis;
    // Cash NOI drives DSCR, debt yield and the exit-cap reversion.
    expect(
      screen.getAllByText(/entry cap rate/i).length + screen.getAllByText(/cap-rate convention/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/DSCR and debt yield/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/exit cap rate/i).length).toBeGreaterThan(0);

    // The old single-definition sentence is gone — it asserted that "NOI"
    // excludes the FF&E reserve without ever naming the other figure.
    expect(
      screen.queryByText(/NOI is computed as GOP minus management fee minus fixed charges/),
    ).not.toBeInTheDocument();
  });
});
