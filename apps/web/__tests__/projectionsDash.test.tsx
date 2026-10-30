/**
 * Go-live Wave 0 (A12) — the Future P&L never coalesces a missing engine
 * figure to 0.
 *
 * A revenue year the engine published without ADR / occupancy / F&B / OOD
 * revenue used to render "$0" / "0.0%" in that column and write 0 into the
 * exported workbook. Now the cell reads "—", the derived Occupied Rooms is a
 * dash too, and the xlsx cell (plus its % Rev / PAR / POR derivations) is
 * EMPTY. The Misc. Income row, which the engine never carried, is gone rather
 * than hidden-because-zero.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

const HOLD_YEARS = 3;
const TOTAL_REVENUE = 13_600_000;

/** Year index 1 (the second column) is the one with holes. */
const revYear = (i: number) => ({
  year: i + 1,
  occupancy: i === 1 ? null : 0.75,
  adr: i === 1 ? null : 300,
  revpar: i === 1 ? null : 225,
  rooms_revenue: 10_000_000,
  fb_revenue: i === 1 ? null : 3_000_000,
  // No `other_revenue` on ANY year — the OOD line was not published at all.
  total_revenue: TOTAL_REVENUE,
});

function buildOutputs(): EngineOutputsResponse {
  const engine = (name: string, outputs: Record<string, unknown>) => ({
    deal_id: 'deal-uuid-1', engine: name, status: 'complete', summary: '',
    outputs, inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
  });
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      revenue: engine('revenue', {
        years: Array.from({ length: HOLD_YEARS }, (_, i) => revYear(i)),
        projection_calendar_years: [2025, 2026, 2027],
      }),
      // The table mounts only once the expense engine has published its years
      // too; this one is deliberately bare (no waterfall) so the test is about
      // the revenue lines, not the expense block.
      expense: engine('expense', {
        years: Array.from({ length: HOLD_YEARS }, (_, i) => ({ year: i + 1, total_revenue: TOTAL_REVENUE })),
      }),
      returns: engine('returns', { hold_years: HOLD_YEARS, exit_cap_rate: 0.07, terminal_noi: 2_000_000 }),
    },
  } as unknown as EngineOutputsResponse;
}

const fx = vi.hoisted(() => ({ xlsxRows: [] as unknown[][] }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: buildOutputs(), previous: null, loading: false, settled: true, error: null, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Project Harbor', keys: 132, field_overrides: { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } } },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [], extractions: {}, loading: false, settled: true,
    extractionFailures: {}, error: null, uploading: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/lib/exportXlsx', () => ({
  downloadXlsx: vi.fn(async (_name: string, sheets: { rows: unknown[][] }[]) => {
    fx.xlsxRows = sheets[0].rows;
  }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-uuid-1' })) } },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  fx.xlsxRows = [];
});

/** The visible cells of the table row whose first cell is `label`. */
function rowCells(label: string): string[] {
  const rows = Array.from(document.querySelectorAll<HTMLTableRowElement>('table tbody tr'));
  const row = rows.find((r) => (r.cells[0]?.textContent ?? '').trim().startsWith(label));
  expect(row, `row "${label}"`).toBeDefined();
  return Array.from(row!.cells).slice(2).map((c) => (c.textContent ?? '').trim());
}

describe('Future P&L — a year the engine left blank', () => {
  it('Occupancy, Average Rate, RevPAR and Occupied Rooms read "—" in that column, never 0', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    for (const label of ['Occupancy', 'Average Rate', 'RevPAR', 'Occupied Rooms']) {
      const cells = rowCells(label);
      expect(cells[1], label).toBe('—');
      expect(cells[0], label).not.toBe('—');
      expect(cells.join(' '), label).not.toMatch(/(^|\s)\$?0(\.0+)?%?(\s|$)/);
    }
  });

  it('a revenue line the engine never published reads "—" everywhere, and Misc. Income is not a row', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    // A revenue row carries four sub-cells per year (Amount, % Rev, PAR, POR).
    const SUB = 4;
    const ood = rowCells('Other Operated Departments');
    expect(ood.slice(0, HOLD_YEARS * SUB).every((c) => c === '—')).toBe(true);
    expect(screen.queryByText('Misc. Income')).toBeNull();
    // F&B is published on two of three years: a dash exactly where it is missing.
    const fb = rowCells('Food & Beverage');
    expect(fb[0 * SUB]).toBe('$3,000,000');
    expect(fb.slice(1 * SUB, 2 * SUB)).toEqual(['—', '—', '—', '—']);
    expect(fb[2 * SUB]).toBe('$3,000,000');
  });

  it('the exported workbook writes EMPTY cells for the blank year — amount and derivations alike', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByRole('button', { name: /export/i }));
    await waitFor(() => expect(fx.xlsxRows.length).toBeGreaterThan(0));
    const byLabel = (label: string) => fx.xlsxRows.find((r) => String(r[0]) === label)!;
    // Column layout: label, then 4 cells per year (Amount, %Rev, PAR, POR).
    const yearCells = (row: unknown[], i: number) => row.slice(1 + i * 4, 1 + (i + 1) * 4);
    for (const label of ['Occupancy', 'Average Rate', 'RevPAR', 'Food & Beverage Revenue']) {
      const row = byLabel(label);
      expect(row, label).toBeDefined();
      expect(yearCells(row, 1), `${label} · blank year`).toEqual(['', '', '', '']);
      expect(yearCells(row, 0)[0], `${label} · published year`).not.toBe('');
      expect(yearCells(row, 0)[0], `${label} · published year`).not.toBe(0);
    }
    expect(yearCells(byLabel('Other Operated Departments'), 0)).toEqual(['', '', '', '']);
    expect(yearCells(byLabel('Occupied Rooms'), 1)[0]).toBe('');
    expect(fx.xlsxRows.some((r) => String(r[0]) === 'Miscellaneous Income')).toBe(false);
    // No AMOUNT cell is a zero standing in for a missing value. (A derived
    // % Rev of a published ADR may legitimately round to 0 — that is a
    // computed ratio, not a coalesced blank, so only amounts are checked.)
    for (const label of ['Occupancy', 'Average Rate', 'RevPAR', 'Food & Beverage Revenue', 'Other Operated Departments']) {
      const row = byLabel(label);
      for (let i = 0; i < HOLD_YEARS; i++) expect(yearCells(row, i)[0], `${label} · year ${i + 1}`).not.toBe(0);
    }
  });
});
