/**
 * FON-54a — `mapWorkerFlag` honesty rules.
 *
 *  • the IC-facing label is the worker's business-readable `concept_label`
 *    when present (a raw extractor path is never humanised into the title);
 *  • `noi_impact_usd` is |delta| ONLY for an NOI-basis flag — a revenue-line
 *    delta reads 0 (never dressed up as an NOI impact);
 *  • `concept` / `impact_basis` / `raw_fields` pass through for the memo;
 *  • older workers (no FON-54a fields) still get a sane fallback.
 */
import { describe, it, expect } from 'vitest';
import { mapWorkerFlag } from '@/lib/hooks/useVariance';
import type { VarianceFlagResult } from '@/lib/api';

const ROOMS: VarianceFlagResult = {
  field: 'rooms_revenue',
  rule_id: 'BROKER_VS_T12_NOI_VARIANCE',
  severity: 'Critical',
  actual: 12_300_000,
  broker: 12_950_000,
  delta: -650_000,
  delta_pct: -0.0528,
  source_page: 14,
  note: 'Rooms revenue: broker proforma $12,950,000 vs T-12 actual $12,300,000 — broker overstates the T-12 by 5.3%.',
  concept: 'rooms_revenue',
  concept_label: 'Rooms revenue',
  impact_basis: 'revenue',
  raw_fields: [
    { field: 'broker_proforma.rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Warn', broker: 12_900_000, actual: 12_300_000, source_page: 14 },
    { field: 'broker.rooms_revenue', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Critical', broker: 12_950_000, actual: 12_300_000 },
    { field: 'rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Info', broker: 12_400_000, actual: 12_300_000 },
  ],
};

describe('mapWorkerFlag — FON-54a', () => {
  it('uses the worker concept_label and never a dollar NOI impact for a revenue line', () => {
    const f = mapWorkerFlag(ROOMS, 0, 'deal-1');
    expect(f.field_label).toBe('Rooms revenue');
    expect(f.concept).toBe('rooms_revenue');
    expect(f.impact_basis).toBe('revenue');
    expect(f.noi_impact_usd).toBe(0);
    expect(f.variance_abs).toBe(-650_000);
    expect(f.broker_overstates).toBe(true);
    expect(f.raw_fields?.map((r) => r.field)).toEqual([
      'broker_proforma.rooms_revenue_usd',
      'broker.rooms_revenue',
      'rooms_revenue_usd',
    ]);
    expect(f.explanation).toMatch(/^Rooms revenue: broker proforma/);
  });

  it('carries |delta| as the NOI impact for an NOI-basis flag', () => {
    const f = mapWorkerFlag(
      { ...ROOMS, field: 'noi', concept: 'noi', concept_label: 'NOI', impact_basis: 'noi', actual: 4_181_000, broker: 5_200_000, delta: -1_019_000, delta_pct: -0.2437, raw_fields: [] },
      1,
      'deal-1',
    );
    expect(f.field_label).toBe('NOI');
    expect(f.impact_basis).toBe('noi');
    expect(f.noi_impact_usd).toBe(1_019_000);
  });

  it('falls back sanely for an older worker without FON-54a fields', () => {
    const legacy: VarianceFlagResult = {
      field: 'broker_proforma.rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Warn',
      actual: 12_300_000, broker: 12_900_000, delta: -600_000, delta_pct: -0.0488, source_page: 14, note: null,
    };
    const f = mapWorkerFlag(legacy, 0, 'deal-1');
    // Label from the local catalog (not the raw path); basis inferred as
    // non-NOI so no NOI dollar figure is invented.
    expect(f.field_label).toBe('Rooms Revenue');
    expect(f.concept).toBe('rooms_revenue');
    expect(f.impact_basis).toBe('other');
    expect(f.noi_impact_usd).toBe(0);

    const noiLegacy = mapWorkerFlag({ ...legacy, field: 'broker_proforma.noi_usd', delta: -1_019_000 }, 1, 'deal-1');
    expect(noiLegacy.field_label).toBe('NOI');
    expect(noiLegacy.impact_basis).toBe('noi');
    expect(noiLegacy.noi_impact_usd).toBe(1_019_000);
  });

  it('formats occupancy as a percent flag', () => {
    const f = mapWorkerFlag(
      { ...ROOMS, field: 'occupancy', concept: 'occupancy', concept_label: 'Occupancy', impact_basis: 'revenue', actual: 0.762, broker: 0.8, delta: -0.038, delta_pct: 0.038, raw_fields: [] },
      2,
      'deal-1',
    );
    expect(f.format).toBe('percent');
    expect(f.noi_impact_usd).toBe(0);
  });
});
