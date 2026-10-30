/**
 * /methodology is read by clients (an investment committee, a principal, an
 * analyst at an institution). It describes live behaviour in plain language
 * and never leaks engineering vocabulary: no engine field paths, no API
 * routes, no ticket ids, no release dates, no model-vendor names, no
 * repository paths, no raw reason codes. The engineering reference lives on
 * the diagnostics-gated internal route (`EngineeringMethodology`) instead.
 *
 * This test is the lint the sweep asked for: it renders the client page and
 * greps every piece of visible text.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/app/methodology/page';
import { REASONS, REASON_CODES } from '@/lib/ontology/reasons.generated';
import { clientReasonExplanation } from '@/lib/reasonCopy';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

const TICKETS_AND_PHASES = /FON-\d+|\bE-0\d\d|\bR-0\d\d|Wave \d|Phase \d/;
const ISO_DATES = /\b20\d\d-\d\d-\d\d\b/;
const ROUTES = /\b(GET|POST|PATCH|DELETE) \//;
const REPO_PATHS = /apps\/worker|apps\/web|packages\/|\.py\b|\.yaml\b|\.json\b/;
const VENDOR_MODELS = /\bClaude\b|\bOpus\b|\bSonnet\b|\bHaiku\b|LlamaParse|PyMuPDF|openpyxl/;
const SNAKE_CASE = /\b[a-z][a-z0-9]*_[a-z0-9_]+\b/;
// `revenue.years[0]`, `ttm_performance.subject.occupancy_pct`, `p_and_l_usali.x`
const DOTTED_FIELD_PATH = /\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(\.[a-z0-9_\[\]]+)*\b/;
const CLIENT_NAMES = /Kimpton|Brookfield|Eshan|Rani\b|\bSam\b/;

function visibleText(): string {
  return document.body.textContent ?? '';
}

describe('/methodology — client copy discipline', () => {
  it('renders the ten client sections', () => {
    render(<MethodologyPage />);
    expect(screen.getByText('How Fondok underwrites')).toBeInTheDocument();
    for (const n of ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']) {
      expect(screen.getByText(`Section ${n}`)).toBeInTheDocument();
    }
    expect(screen.getByText('What a dash means')).toBeInTheDocument();
  });

  it('carries no ticket ids, phases, release dates, routes, repo paths or vendor model names', () => {
    render(<MethodologyPage />);
    const text = visibleText();
    expect(text).not.toMatch(TICKETS_AND_PHASES);
    expect(text).not.toMatch(ISO_DATES);
    expect(text).not.toMatch(ROUTES);
    expect(text).not.toMatch(REPO_PATHS);
    expect(text).not.toMatch(VENDOR_MODELS);
    expect(text).not.toMatch(CLIENT_NAMES);
  });

  it('prints no engine field path, snake_case token or <code> span', () => {
    render(<MethodologyPage />);
    const text = visibleText();
    const snake = text.match(new RegExp(SNAKE_CASE.source, 'g')) ?? [];
    expect(snake, `snake_case tokens: ${snake.join(', ')}`).toEqual([]);
    const dotted = text.match(new RegExp(DOTTED_FIELD_PATH.source, 'g')) ?? [];
    expect(dotted, `dotted field paths: ${dotted.join(', ')}`).toEqual([]);
    expect(document.querySelectorAll('code')).toHaveLength(0);
  });

  it('states the dash rule in plain words — a figure with no source renders as —', () => {
    render(<MethodologyPage />);
    const rule = screen.getByTestId('dash-rule');
    expect(rule).toHaveTextContent('A figure with no source renders as —.');
    expect(rule).toHaveTextContent(/Nothing is filled in with a zero, an estimate or a sample figure/);
    expect(rule).toHaveTextContent(/exported workbook leaves the same cell empty/);
  });

  it('has no unrecorded-video placeholder and no "coming soon"', () => {
    render(<MethodologyPage />);
    const text = visibleText();
    expect(text).not.toMatch(/Walkthrough video|Loom|coming soon|work in progress/i);
  });

  // FON-118 / FON-44 — returns by exit year, the sale-year basis and the hold
  // defaults are described for the analyst, as labelled defaults they can
  // override — never as facts about the deal.
  it('describes returns by exit year, the sale-year basis and the hold defaults', () => {
    render(<MethodologyPage />);
    const card = screen.getByTestId('methodology-returns-by-exit-year');
    expect(card).toHaveTextContent('Returns by exit year');
    expect(card).toHaveTextContent(/every projection year-end/i);
    expect(card).toHaveTextContent(/refinanc/i);
    expect(card).toHaveTextContent(/capital still deploying/i);
    expect(card).toHaveTextContent(/construction window/i);
    expect(card).toHaveTextContent(/exit NOI (is )?not positive/i);
    expect(card).toHaveTextContent(/Peak levered IRR/);
    expect(card).toHaveTextContent(/Selected exit/);
    expect(card).toHaveTextContent(/LP and GP returns/i);

    const basis = screen.getByTestId('methodology-exit-basis');
    expect(basis).toHaveTextContent('Sale-year NOI');
    expect(basis).toHaveTextContent(/not grown/i);
    expect(basis).toHaveTextContent(/next projected year/i);
    expect(basis).toHaveTextContent(/pinned|analyst-entered terminal NOI/i);

    const hold = screen.getByTestId('methodology-hold-defaults');
    expect(hold).toHaveTextContent(/5 years/);
    expect(hold).toHaveTextContent(/7 years/);
    expect(hold).toHaveTextContent(/6 years/);
    expect(hold).toHaveTextContent('Default for development deals — confirm');
    expect(hold).toHaveTextContent(/override/i);

    const text = visibleText();
    expect(text).toMatch(/returns schedule by exit year/i);
    expect(text).toMatch(/selected exit month/i);
  });

  it('the dash table shows every reason by its label and explanation, never its code', () => {
    render(<MethodologyPage />);
    const table = screen.getByTestId('reason-code-table');
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    expect(rows.map((r) => r.getAttribute('data-reason-code'))).toEqual([...REASON_CODES]);
    for (const code of REASON_CODES) {
      const row = rows.find((r) => r.getAttribute('data-reason-code') === code) as HTMLElement;
      expect(row.textContent).toContain(REASONS[code].label);
      expect(row.textContent).toContain(clientReasonExplanation(code));
      // The code travels only on the data attribute — never as visible text.
      expect(row.textContent).not.toContain(code);
    }
  });
});
