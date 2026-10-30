/**
 * Go-live Wave 0 (C4 / C5 / C6) — the web ships NO deal fixtures.
 *
 * `lib/mockData.ts` (four fictitious hotels + a static engine progress map)
 * and `lib/varianceData.ts` (seven Kimpton variance flags + summary totals)
 * are gone. What remains is reference material only: brand catalog, return
 * profiles, positioning tiers, deal stages, sourcing channels, project
 * statuses (`lib/catalogs`), the variance TYPES and the USALI rule catalog
 * (`lib/variance`). A build with no worker renders "Worker not configured",
 * never a sample — this file is the guard that the fixtures stay deleted.
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import * as catalogs from '@/lib/catalogs';
import * as variance from '@/lib/variance';

const LIB = path.resolve(__dirname, '../src/lib');

describe('deleted fixture modules stay deleted', () => {
  it.each(['mockData.ts', 'varianceData.ts'])('src/lib/%s does not exist', (file) => {
    expect(existsSync(path.join(LIB, file))).toBe(false);
  });
});

describe('lib/catalogs carries reference lists only', () => {
  it.each([
    'projects',
    'engines',
    'workspace',
    'currentUser',
    'documentChecklist',
    'kimptonAnglerOverview',
    'kimptonDocuments',
    'miamiMarket',
    'kimptonAnalysis',
    'dealScenarios',
    'kimptonCriticFindings',
    'dashboardStats',
  ])('does not export %s', (name) => {
    expect(name in catalogs).toBe(false);
  });

  it.each([
    'brandFamilies',
    'returnProfiles',
    'positioningTiers',
    'dealStages',
    'sourcingChannels',
    'projectStatuses',
  ])('still exports the %s reference list', (name) => {
    const v = (catalogs as Record<string, unknown>)[name];
    expect(Array.isArray(v)).toBe(true);
    expect((v as unknown[]).length).toBeGreaterThan(0);
  });
});

describe('lib/variance carries types and the rule catalog only', () => {
  it.each(['varianceFlags', 'varianceSummary', 'criticalCount', 'warnCount', 'infoCount'])(
    'does not export %s',
    (name) => {
      expect(name in variance).toBe(false);
    },
  );

  it('keeps the USALI rule catalog the Variance tab and operating-model guidance read', () => {
    expect(variance.usaliRules.length).toBeGreaterThan(0);
    expect(variance.rulesById.MGMT_FEE_RANGE).toBeDefined();
  });
});
