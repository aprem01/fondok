/**
 * Historicals field picker — subordinate-namespace guard (Task F).
 *
 * Sam QA 2026-06-30 on deal b5f532ad-1c70-4e2a-a07e-ce5c2ccdee7e: after
 * re-extracting the historical P&Ls, the P&L Historicals tab showed
 * 2023 Rooms = $1,086 and 2025 Rooms = $1,086 — exactly the JANUARY
 * monthly slice from the 2023 P&L. ``findField`` matched the last
 * dotted segment of ``p_and_l_usali.monthly.jan.rooms_revenue_usd``
 * (= ``rooms_revenue_usd`` → unit-stripped ``rooms_revenue``) as if it
 * were the annual rollup.
 *
 * This suite locks in the subordinate-namespace guard so a monthly /
 * quarterly / per-page slice can never again leak through as the
 * annual canonical. Must stay in step with the worker-side helper
 * ``_has_subordinate_namespace`` in
 * ``apps/worker/app/services/usali_scorer.py``.
 */
import { describe, it, expect } from 'vitest';
import { findField } from '@/components/project/pl/HistoricalsSection';
import type { ExtractionField } from '@/lib/api';

function mkField(name: string, value: unknown): ExtractionField {
  return {
    field_name: name,
    value,
    unit: null,
    source_page: 1,
    confidence: 0.9,
    raw_text: null,
  };
}

describe('findField — subordinate-namespace guard', () => {
  it('returns undefined when only a monthly slice carries the alias tail', () => {
    const fields = [
      mkField('p_and_l_usali.monthly.jan.rooms_revenue_usd', 1086),
      mkField('p_and_l_usali.monthly.feb.rooms_revenue_usd', 1100),
    ];
    const hit = findField(fields, ['rooms_revenue']);
    expect(hit).toBeUndefined();
  });

  it('prefers the annual rollup when both annual + monthly are present', () => {
    const fields = [
      mkField('p_and_l_usali.monthly.jan.rooms_revenue_usd', 1086),
      mkField('p_and_l_usali.revenues.rooms_usd', 9_807_990),
      mkField('rooms_revenue', 9_807_990),
    ];
    const hit = findField(fields, ['rooms_revenue']);
    expect(hit).toBeDefined();
    expect(hit?.value).toBe(9_807_990);
    expect(hit?.field_name.toLowerCase()).not.toContain('monthly');
  });

  it('rejects quarterly / per_month / page slices as well', () => {
    const cases: string[] = [
      'p_and_l_usali.quarterly.q1.rooms_revenue_usd',
      'p_and_l_usali.q2.rooms_revenue_usd',
      'p_and_l_usali.per_month.jan.rooms_revenue_usd',
      'p_and_l_usali.page5.rooms_revenue_usd',
    ];
    for (const name of cases) {
      const hit = findField([mkField(name, 1)], ['rooms_revenue']);
      expect(hit, `should reject ${name}`).toBeUndefined();
    }
  });

  it('still matches a clean annual canonical', () => {
    const fields = [
      mkField('p_and_l_usali.revenues.rooms_usd', 9_807_990),
    ];
    const hit = findField(fields, ['rooms_revenue']);
    // ``rooms_revenue`` alias normalizes to ``roomsrevenue``; the tail
    // ``rooms_usd`` unit-stripped is ``rooms`` — which is NOT a hit.
    // The full-path normalize ``pandlusalrevenuesroomsusd`` also isn't
    // a hit. So the bare alias only covers explicit slug emissions.
    // This case documents the existing matcher behavior: extraction
    // payloads that go through the worker's alias map land under the
    // bare slug, which findField then matches directly.
    expect(hit).toBeUndefined();

    const fields2 = [mkField('rooms_revenue', 9_807_990)];
    const hit2 = findField(fields2, ['rooms_revenue']);
    expect(hit2?.value).toBe(9_807_990);
  });
});
