/**
 * lib/mockData — reference catalogs only (standing rule: no invented data).
 *
 * The numeric Kimpton Angler / Miami Beach fixtures (overview, documents,
 * market, analysis, scenarios, critic findings, dashboard stats) were removed
 * on 2026-10-14; nothing imported them and nothing may again. The non-numeric
 * reference lists the wizard / Overview / Data Room read are kept.
 */
import { describe, it, expect } from 'vitest';
import * as mock from '@/lib/mockData';

const REMOVED_NUMERIC_FIXTURES = [
  'kimptonAnglerOverview',
  'kimptonDocuments',
  'miamiMarket',
  'kimptonAnalysis',
  'dealScenarios',
  'kimptonCriticFindings',
  'kimptonCriticSummary',
  'dashboardStats',
  'templates',
  'compSets',
  'marketDataLib',
  'teamMembers',
  'notificationDefaults',
  'integrations',
];

const KEPT_CATALOGS = [
  'brandFamilies',
  'returnProfiles',
  'positioningTiers',
  'dealStages',
  'sourcingChannels',
  'documentChecklist',
  'projectStatuses',
  'engines',
];

describe('lib/mockData carries no numeric deal / market / analysis fixtures', () => {
  it.each(REMOVED_NUMERIC_FIXTURES)('does not export %s', (name) => {
    expect(name in mock).toBe(false);
  });

  it.each(KEPT_CATALOGS)('still exports the %s reference list', (name) => {
    const v = (mock as Record<string, unknown>)[name];
    expect(Array.isArray(v)).toBe(true);
    expect((v as unknown[]).length).toBeGreaterThan(0);
  });

  it('keeps the critic finding shape (types only) usable by AnalysisTab', () => {
    // Type-level check: assigning a worker-shaped finding compiles.
    const f: mock.KimptonCriticFinding = {
      id: 'x', ruleId: 'R', title: 't', narrative: 'n', severity: 'WARN', citedFields: [], citedPages: [],
    };
    expect(f.severity).toBe('WARN');
  });
});
