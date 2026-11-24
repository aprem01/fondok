/**
 * R-061 — Returns moved into Overview; legacy `?tab=returns` links redirect.
 */
import { describe, it, expect } from 'vitest';
import {
  legacyReturnsRedirect,
  returnsHref,
  returnsAnchorFromHash,
  returnsAnchorFromUrl,
  isReturnsView,
  RETURNS_VIEW_IDS,
  EXIT_SCHEDULE_ANCHOR_ID,
  RETURNS_ANCHOR_PARAM,
} from '@/lib/returnsSection';

describe('FON-67 — a Returns link may land on one card inside the section', () => {
  it('returnsHref carries the card id as a fragment only when asked', () => {
    expect(returnsHref('d', 'returns-summary', EXIT_SCHEDULE_ANCHOR_ID)).toBe(
      '/projects/d?tab=overview&sub=returns-summary#returns-by-exit-year',
    );
    expect(returnsHref('d')).toBe('/projects/d?tab=overview&sub=returns-summary');
  });

  it('returnsAnchorFromHash recognises the exit-year card and nothing else', () => {
    expect(returnsAnchorFromHash('#returns-by-exit-year')).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromHash('returns-by-exit-year')).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromHash('#overview-returns')).toBeNull();
    expect(returnsAnchorFromHash('')).toBeNull();
    expect(returnsAnchorFromHash(null)).toBeNull();
  });

  it('returnsAnchorFromUrl reads the fragment first, then ?anchor= (a redirect drops fragments)', () => {
    expect(RETURNS_ANCHOR_PARAM).toBe('anchor');
    const asParam = new URLSearchParams(`sub=returns-summary&${RETURNS_ANCHOR_PARAM}=${EXIT_SCHEDULE_ANCHOR_ID}`);
    expect(returnsAnchorFromUrl('#returns-by-exit-year', null)).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromUrl('#returns-by-exit-year', new URLSearchParams('anchor=bogus'))).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromUrl('', asParam)).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromUrl(null, asParam)).toBe(EXIT_SCHEDULE_ANCHOR_ID);
    expect(returnsAnchorFromUrl('#overview-returns', new URLSearchParams('anchor=bogus'))).toBeNull();
    expect(returnsAnchorFromUrl('', new URLSearchParams('sub=returns-summary'))).toBeNull();
    expect(returnsAnchorFromUrl('', null)).toBeNull();
    expect(returnsAnchorFromUrl(undefined, undefined)).toBeNull();
  });
});

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
