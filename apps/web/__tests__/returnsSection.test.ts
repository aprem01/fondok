/**
 * R-061 — Returns moved into Overview; legacy `?tab=returns` links redirect.
 */
import { describe, it, expect } from 'vitest';
import { legacyReturnsRedirect, returnsHref, isReturnsView, RETURNS_VIEW_IDS } from '@/lib/returnsSection';

describe('legacy ?tab=returns redirect', () => {
  it('keeps the view and every other param, switching tab to overview', () => {
    const url = legacyReturnsRedirect('d-1', 'tab=returns&sub=pricing&doc=7&focus=x');
    const qp = new URLSearchParams(url.split('?')[1]);
    expect(url.startsWith('/projects/d-1?')).toBe(true);
    expect(qp.get('tab')).toBe('overview');
    expect(qp.get('sub')).toBe('pricing');
    expect(qp.get('doc')).toBe('7');
    expect(qp.get('focus')).toBe('x');
  });

  it('defaults to Returns Summary when no / an unknown view is given', () => {
    expect(new URLSearchParams(legacyReturnsRedirect('d', 'tab=returns').split('?')[1]).get('sub')).toBe('returns-summary');
    expect(new URLSearchParams(legacyReturnsRedirect('d', 'tab=returns&sub=bogus').split('?')[1]).get('sub')).toBe('returns-summary');
  });

  it('every old Returns view slug is still a valid view', () => {
    for (const v of ['returns-summary', 'sensitivities', 'pricing']) expect(isReturnsView(v)).toBe(true);
    expect(RETURNS_VIEW_IDS).toHaveLength(3);
    expect(returnsHref('d', 'sensitivities')).toBe('/projects/d?tab=overview&sub=sensitivities');
  });
});
