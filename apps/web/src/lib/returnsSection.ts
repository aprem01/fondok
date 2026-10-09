/**
 * R-061 — Returns lives on Overview, not in its own tab.
 *
 * The former Returns tab (`?tab=returns`) is now the "Returns" section at the
 * foot of Overview. Its three views keep their URL slugs (`?sub=…`), so a
 * bookmark like `?tab=returns&sub=pricing` is rewritten to
 * `?tab=overview&sub=pricing` and Overview scrolls to the section with the
 * Pricing view open. Every cross-tab "View Returns →" link builds its URL here.
 */

/** DOM id of the Overview Returns section (scroll / `#` anchor target). */
export const RETURNS_SECTION_ID = 'overview-returns';

/** The Returns section's views. Ids are URL slugs (`?sub=`); labels display. */
export const RETURNS_VIEWS = [
  { id: 'returns-summary', label: 'Returns Summary' },
  { id: 'sensitivities', label: 'Sensitivities' },
  { id: 'pricing', label: 'Pricing' },
] as const;

export type ReturnsViewId = (typeof RETURNS_VIEWS)[number]['id'];
export const RETURNS_VIEW_IDS = RETURNS_VIEWS.map((v) => v.id) as readonly ReturnsViewId[];

export function isReturnsView(sub: string | null | undefined): sub is ReturnsViewId {
  return sub != null && (RETURNS_VIEW_IDS as readonly string[]).includes(sub);
}

/** Overview's Returns section, on one of its views. */
export function returnsHref(dealId: string | number, sub: ReturnsViewId = 'returns-summary'): string {
  return `/projects/${dealId}?tab=overview&sub=${sub}`;
}

/**
 * The URL a legacy `?tab=returns[&sub=…]` link is redirected to: same params,
 * `tab=overview`, and a valid Returns view in `sub` (unknown → Summary).
 */
export function legacyReturnsRedirect(dealId: string | number, query: string): string {
  const qp = new URLSearchParams(query);
  qp.set('tab', 'overview');
  if (!isReturnsView(qp.get('sub'))) qp.set('sub', 'returns-summary');
  return `/projects/${dealId}?${qp.toString()}`;
}
