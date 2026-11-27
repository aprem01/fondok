/**
 * Sam QA 2026-11-27 (deal b67bfdee) — Index Analysis: "the 2019 Available
 * Rooms cell is clipped to ',180' at the left edge" (48,180 = 365 days ×
 * 132 keys, its first digits hidden).
 *
 * Cause (IndexAnalysisSection.tsx `stickyL`): the label column is
 * `position: sticky`; once the table is wider than its card — the STR TTM
 * column (e3294633) added a column to the `180 + n × 82` width floor — a
 * horizontal scroll slides the first data column under the frozen labels,
 * and with macOS overlay scrollbars nothing says the pane is scrolled.
 *
 * Contracts locked here, for the 12-column shape Sam saw (6 historical
 * years, the TTM column, 5 forecast years):
 *
 *  1. the first data cell carries its FULL text ("48,180"), beside — never
 *     under — the label cell;
 *  2. the label column has an explicit width (one constant for the width,
 *     the table floor and the snap padding) and a sticky, opaque background;
 *  3. the scroller snaps to column boundaries with the frozen width as its
 *     padding, every data column header being a snap area, so a partial
 *     column can never rest beside the labels;
 *  4. a label the column cannot hold wraps inside it (no `nowrap`) instead of
 *     running under the first data column.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import React from 'react';
import {
  IndexTable,
  FrozenLabelScroller,
  LABEL_COL_PX,
  PENETRATION_LABEL_COL_PX,
  DATA_COL_MIN_PX,
} from '@/components/project/pl/IndexAnalysisSection';
import { deriveIndexColumns, emptySeries, type ForecastBoundary } from '@/components/project/pl/indexAnalysisModel';
import type { DealYearSpan } from '@/lib/dealYears';

afterEach(cleanup);

// Five deal years from Jan 2025 → six historical columns 2019…2024, the STR
// TTM window ending Oct 2025 gets its own column, then Year 1…5: 12 columns.
const DEAL_YEARS: DealYearSpan[] = Array.from({ length: 5 }, (_, i) => ({
  year: i + 1,
  start: `${2025 + i}-01-01`,
  end: `${2025 + i}-12-31`,
  label: `Jan–Dec ${2025 + i}`,
})) as unknown as DealYearSpan[];

const COLS = deriveIndexColumns({ dealYears: DEAL_YEARS, forecastCount: 5, documentYears: [], hasStrTtm: true, strTtmPeriodEnd: '2025-10' });
const BOUNDARY = { comparable: true, note: null } as unknown as ForecastBoundary;

function renderSubject(keys = 132) {
  const n = COLS.columns.length;
  return render(
    <IndexTable
      title="Subject Property"
      cols={COLS}
      keys={keys}
      series={emptySeries(n)}
      sources={Array.from({ length: n }, () => null)}
      subject
      forecastBand="Fondok underwriting (revenue engine)"
      boundary={BOUNDARY}
    />,
  );
}

const rowByLabel = (label: string): HTMLTableRowElement => {
  const cell = screen.getAllByText(label).find((el) => el.tagName === 'TD');
  if (!cell) throw new Error(`no label cell "${label}"`);
  return cell.closest('tr') as HTMLTableRowElement;
};

describe('Index Analysis — frozen label column never covers the first data column', () => {
  it('is the 12-column shape Sam saw: 2019…2024, TTM Oct 2025, Year 1…5', () => {
    expect(COLS.columns.map((c) => c.label)).toEqual([
      '2019', '2020', '2021', '2022', '2023', '2024', 'TTM Oct 2025',
      'Jan–Dec 2025', 'Jan–Dec 2026', 'Jan–Dec 2027', 'Jan–Dec 2028', 'Jan–Dec 2029',
    ]);
    expect(COLS.columns).toHaveLength(12);
    expect(COLS.firstForecastIndex).toBe(7);
  });

  it('the 2019 Available Rooms cell carries "48,180" in full, as the cell right after the label', () => {
    renderSubject(132);
    const row = rowByLabel('Available Rooms');
    const cells = Array.from(row.querySelectorAll('td'));
    expect(cells).toHaveLength(1 + COLS.columns.length);
    expect(cells[0]).toHaveTextContent('Available Rooms');
    expect(cells[1].textContent).toBe('48,180');
    expect(cells[1].previousElementSibling).toBe(cells[0]);
    // The 365-day projection years at 132 keys, for the eye: 48,180 too.
    expect(cells[1 + COLS.firstForecastIndex].textContent).toBe('48,180');
  });

  it('every label cell is sticky with an opaque background and the one explicit width', () => {
    renderSubject();
    const labels = Array.from(document.querySelectorAll<HTMLElement>('td[data-ia-label]'));
    expect(labels.length).toBeGreaterThanOrEqual(10);
    for (const td of labels) {
      expect(td.classList.contains('sticky')).toBe(true);
      expect(td.classList.contains('left-0')).toBe(true);
      expect(td.classList.contains('bg-card')).toBe(true);
      expect(td.style.width).toBe(`${LABEL_COL_PX}px`);
      expect(td.style.minWidth).toBe(`${LABEL_COL_PX}px`);
      expect(td.style.maxWidth).toBe(`${LABEL_COL_PX}px`);
      // A long label wraps inside the frozen width rather than running under the data.
      expect(td.classList.contains('whitespace-nowrap')).toBe(false);
    }
    // The header label cells (band title + "Metric") carry the same width.
    const ths = Array.from(document.querySelectorAll<HTMLElement>('thead th.sticky'));
    expect(ths).toHaveLength(2);
    for (const th of ths) expect(th.style.width).toBe(`${LABEL_COL_PX}px`);
  });

  it('the scroller snaps to column boundaries with the frozen width as its padding; each data column header is a snap area', () => {
    renderSubject();
    const scroller = screen.getByTestId('index-table-subject-property');
    expect(scroller.classList.contains('overflow-x-auto')).toBe(true);
    expect(scroller.classList.contains('snap-x')).toBe(true);
    expect(scroller.classList.contains('snap-mandatory')).toBe(true);
    expect(scroller.getAttribute('data-frozen-px')).toBe(String(LABEL_COL_PX));
    expect(scroller.style.scrollPaddingLeft).toBe(`${LABEL_COL_PX}px`);
    const heads = Array.from(scroller.querySelectorAll<HTMLElement>('th[data-ia-col]'));
    expect(heads).toHaveLength(COLS.columns.length);
    expect(heads[0]).toHaveTextContent('2019');
    for (const th of heads) expect(th.classList.contains('snap-start')).toBe(true);
    // The table's width floor is the frozen width plus the per-column floor.
    const table = scroller.querySelector('table') as HTMLTableElement;
    expect(table.style.minWidth).toBe(`${Math.max(900, LABEL_COL_PX + COLS.columns.length * DATA_COL_MIN_PX)}px`);
  });

  it('shows the frozen-edge shadow only while scrolled', () => {
    renderSubject();
    const scroller = screen.getByTestId('index-table-subject-property');
    expect(screen.queryByTestId('frozen-edge-shadow')).toBeNull();
    Object.defineProperty(scroller, 'scrollLeft', { value: 24, configurable: true });
    fireEvent.scroll(scroller);
    expect(screen.getByTestId('frozen-edge-shadow')).toBeInTheDocument();
    expect(screen.getByTestId('frozen-edge-shadow').style.left).toBe(`${LABEL_COL_PX}px`);
    Object.defineProperty(scroller, 'scrollLeft', { value: 0, configurable: true });
    fireEvent.scroll(scroller);
    expect(screen.queryByTestId('frozen-edge-shadow')).toBeNull();
  });

  it('the penetration table gets the wider frozen column its labels need', () => {
    render(
      <FrozenLabelScroller labelWidth={PENETRATION_LABEL_COL_PX} testId="pen">
        <table><tbody><tr><td>x</td></tr></tbody></table>
      </FrozenLabelScroller>,
    );
    expect(PENETRATION_LABEL_COL_PX).toBeGreaterThan(LABEL_COL_PX);
    expect(screen.getByTestId('pen').style.scrollPaddingLeft).toBe(`${PENETRATION_LABEL_COL_PX}px`);
  });
});
