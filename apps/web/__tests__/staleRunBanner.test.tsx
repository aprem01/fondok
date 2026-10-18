/**
 * The page-level stale-run banner.
 *
 * The defect it exists for: a healthy deal opened with the whole Stabilization
 * section of Overview rendering five dashes, because the deal's persisted
 * engine output had been written before `expense.stabilization` existed.
 * Nothing on screen said so.
 *
 * Locked here:
 *   1. a current run shows nothing — a false positive telling an analyst to
 *      re-run an up-to-date model is the only way this feature can do harm;
 *   2. the reader sees ONE line naming product tabs, never a dotted engine
 *      path or a snake_case token — not in the headline, not in the section
 *      list (a Brookfield reader saw 54 raw paths above the fold);
 *   3. every block the engines publish today maps to a human section name,
 *      and an unknown block still reads as "<Engine> model";
 *   4. the Re-run button actually posts a run.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { REASONS } from '@/lib/ontology/reasons.generated';
import { ENGINE_SECTION_LABEL, engineSectionLabel, engineTabLabel } from '@/lib/fieldLabels';

const runAll = vi.hoisted(() => vi.fn(async () => ({
  deal_id: 'deal-uuid-1',
  run_id: 'run-1',
  started_at: new Date().toISOString(),
  engines: [],
})));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: { ...actual.api.engines, runAll, runStatus: vi.fn(async () => ({
        deal_id: 'deal-uuid-1', run_id: 'run-1', engines: [],
      })) },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import { StaleRunBanner, staleRunHeadline, staleSections, staleTabs } from '@/components/project/StaleRunBanner';

/** The engines map is irrelevant to this banner — only `stale_run` drives it. */
function outputs(staleRun: EngineOutputsResponse['stale_run']): EngineOutputsResponse {
  return {
    deal_id: 'deal-uuid-1',
    engines: {} as EngineOutputsResponse['engines'],
    stale_run: staleRun,
  };
}

/** Sam's deal on 2026-10-17 — 54 blocks across six engines. */
const SAM_MISSING: Record<string, string[]> = {
  revenue: ['projection_start_year', 'renovation_displacement_by_year', 'index_drivers_by_year'],
  fb: ['provenance'],
  expense: ['line_methods', 'stabilization'],
  capital: ['renovation_base_usd', 'senior_loan_fee_usd', 'renovation_breakdown'],
  debt: ['refi_fee_pct', 'refi_debt_yield_min', 'stabilized_dscr'],
  returns: ['levered_monthly', 'noi_by_year', 'exit_noi_period_label'],
  partnership: ['catch_up_amount', 'gp_additional_contributions'],
};

const DEVELOPER_TOKEN = /[a-z]_[a-z]|\w\.\w/;

beforeEach(() => { runAll.mockClear(); });
afterEach(cleanup);

describe('StaleRunBanner', () => {
  it('renders nothing when the run is current', () => {
    const { container } = render(
      <StaleRunBanner outputs={outputs(null)} dealId="deal-uuid-1" />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('stale-run-banner')).toBeNull();
  });

  it('renders nothing when there are no outputs at all', () => {
    const { container } = render(<StaleRunBanner outputs={null} dealId="deal-uuid-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('one block → one line naming the tab, never the dotted path', () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { revenue: ['renovation_displacement_by_year'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    const headline = screen.getByTestId('stale-run-headline').textContent ?? '';
    expect(headline).toBe(
      'This deal’s saved model is older than the current version — 1 figure on P&L show “—” until you re-run.',
    );
    expect(headline).not.toMatch(DEVELOPER_TOKEN);
    const banner = screen.getByTestId('stale-run-banner');
    expect(banner.textContent).toContain(REASONS.stale_run.explanation);
    // The dotted path lives only in the data attribute / collapsed technical detail.
    expect(banner.getAttribute('data-stale-paths')).toBe('revenue.renovation_displacement_by_year');
    expect(screen.getByTestId('stale-run-paths').textContent).toContain('revenue.renovation_displacement_by_year');
  });

  it('many blocks collapse to one sentence across tabs, with the count', () => {
    render(
      <StaleRunBanner
        outputs={outputs({ reason: 'stale_run', missing_blocks: SAM_MISSING })}
        dealId="deal-uuid-1"
      />,
    );
    const headline = screen.getByTestId('stale-run-headline').textContent ?? '';
    expect(headline).toBe(
      'This deal’s saved model is older than the current version — 17 figures across P&L, CAPEX, Financing, Returns and Partnership show “—” until you re-run.',
    );
    expect(headline).not.toMatch(DEVELOPER_TOKEN);
  });

  it('no rendered section label contains a "." or "_" — for every block the engines publish', () => {
    render(
      <StaleRunBanner
        outputs={outputs({ reason: 'stale_run', missing_blocks: SAM_MISSING })}
        dealId="deal-uuid-1"
      />,
    );
    const items = Array.from(screen.getByTestId('stale-run-sections').querySelectorAll('li'));
    expect(items.length).toBeGreaterThan(0);
    for (const li of items) {
      expect(li.textContent).not.toMatch(/[._]/);
    }
    // Every registry entry is a human label.
    for (const [dotted, label] of Object.entries(ENGINE_SECTION_LABEL)) {
      expect(label, dotted).not.toMatch(/[._]/);
      const [engine, ...rest] = dotted.split('.');
      expect(engineSectionLabel(engine, rest.join('.'))).toBe(label);
    }
  });

  it('an unknown block reads as "<Engine> model", never the raw path', () => {
    expect(engineSectionLabel('revenue', 'some_future_block')).toBe('Revenue model');
    expect(engineSectionLabel('debt', 'schedule.0.balance')).toBe('Debt schedule');
    expect(engineTabLabel('capital')).toBe('CAPEX');
    expect(engineTabLabel('sensitivity')).toBe('Scenario Analysis');
    expect(staleSections({ revenue: ['some_future_block'] })).toEqual(['Revenue model']);
    expect(staleTabs({ revenue: ['a'], expense: ['b'], debt: ['c'] })).toEqual(['P&L', 'Financing']);
    expect(staleRunHeadline(undefined)).toBeNull();
  });

  it('collapses several missing paths onto one section name', () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { capital: ['sources', 'uses'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    const items = screen.getByTestId('stale-run-sections').querySelectorAll('li');
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toBe('Sources & Uses');
  });

  it('posts a run when the Re-run button is clicked', async () => {
    render(
      <StaleRunBanner
        outputs={outputs({
          reason: 'stale_run',
          missing_blocks: { expense: ['stabilization'] },
        })}
        dealId="deal-uuid-1"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /re-run models/i }));
    await waitFor(() => expect(runAll).toHaveBeenCalledWith('deal-uuid-1'));
  });
});
