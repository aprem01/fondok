import { test, expect, type Locator, type Page } from '@playwright/test';
import { kimptonDealId } from './guard';

/**
 * Shared helpers for the Kimpton QA regression suite.
 *
 * `figureBeside` / `expectFigure` / `expectSomeFigure` / `openDeal` /
 * `expectNoEngineFailures` are adapted from `e2e/prod/01-live-deal-smoke.spec.ts`
 * (not imported — that file's helpers are module-private, and duplicating a
 * few small functions is cheaper than exporting across the prod/kimpton-qa
 * boundary this suite is deliberately keeping clean). Behavior is identical;
 * only `openDeal` differs, pointed at `kimptonDealId()` instead of
 * `smokeDealId()`.
 *
 * The NEW helpers below (`expectExactFigure`, `expectFigureInRange`,
 * `worksheetRow`, `expectHistoricalMatches`) exist because, unlike `prod`,
 * this suite is allowed — and needs — to check an actual number, not just
 * that one rendered.
 */

const DASH = '—';

const norm = (s: string): string => s.replace(/\s+/g, ' ').trim();
const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Strips currency/percent formatting to a plain number. '$12,940,123' -> 12940123; '247.08%'->247.08. Returns null if nothing parseable. */
export function parseNumber(text: string): number | null {
  const cleaned = text.replace(/[,$%\s]/g, '');
  if (cleaned === '' || cleaned === DASH) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** The figure rendered beside `label` — see prod spec for full rationale. */
export async function figureBeside(scope: Locator, label: string): Promise<string> {
  const labelEl = scope.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first();
  if ((await labelEl.count()) === 0) return '';
  for (const from of ['self::*', 'ancestor::*[1]', 'ancestor::*[2]']) {
    const sibling = labelEl.locator(`xpath=${from}/following-sibling::*[1]`);
    if ((await sibling.count()) === 0) continue;
    const text = norm(await sibling.first().innerText());
    if (text.length > 0) return text;
  }
  return '';
}

/** `label` renders SOME figure (not a dash). Structure check, like prod's. */
export async function expectFigure(scope: Locator, label: string): Promise<void> {
  await expect(
    scope.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first(),
    `"${label}" is on the page`,
  ).toBeVisible();
  await expect
    .poll(() => figureBeside(scope, label), {
      message: `"${label}" renders a figure, not "${DASH}"`,
      timeout: 45_000,
    })
    .toMatch(/\d/);
}

/**
 * `label` renders EXACTLY `expectedText` (after normalizing commas/whitespace).
 * This is the pinned-value check `prod` forbids itself and this suite exists
 * to do. On failure the error names both the expected and actual text.
 */
export async function expectExactFigure(scope: Locator, label: string, expectedText: string): Promise<void> {
  await expectFigure(scope, label);
  await expect
    .poll(() => figureBeside(scope, label), {
      message: `"${label}" should read exactly "${expectedText}"`,
      timeout: 45_000,
    })
    .toBe(expectedText);
}

/**
 * `label` renders a figure that PARSES into [min, max] inclusive. Use for
 * tolerance/range pass-criteria (NOI $2.5–4M, Year 1 ADR $275–290).
 */
export async function expectFigureInRange(
  scope: Locator,
  label: string,
  min: number,
  max: number,
): Promise<number> {
  await expectFigure(scope, label);
  const text = await figureBeside(scope, label);
  const n = parseNumber(text);
  expect(n, `"${label}" ("${text}") should parse to a number`).not.toBeNull();
  expect(
    n,
    `"${label}" is ${text} (parsed ${n}) — expected between ${min} and ${max}`,
  ).toBeGreaterThanOrEqual(min);
  expect(
    n,
    `"${label}" is ${text} (parsed ${n}) — expected between ${min} and ${max}`,
  ).toBeLessThanOrEqual(max);
  return n as number;
}

/** First of `labels` actually on the page, asserted to render a figure. */
export async function expectSomeFigure(page: Page, labels: string[]): Promise<string> {
  const body = page.locator('body');
  let found = '';
  await expect
    .poll(
      async () => {
        for (const label of labels) {
          const el = body.getByText(new RegExp(`^${escapeRe(label)}$`, 'i')).first();
          if ((await el.count()) > 0) {
            found = label;
            return label;
          }
        }
        return '';
      },
      {
        message: `none of these headline rows rendered: ${labels.join(', ')}`,
        timeout: 45_000,
      },
    )
    .not.toBe('');
  await expectFigure(body, found);
  return found;
}

/** Open a tab of the DUPLICATE Kimpton deal (never the original, never the reference model). */
export async function openDeal(page: Page, query: string): Promise<void> {
  const dealId = kimptonDealId();
  expect(dealId, 'FONDOK_KIMPTON_DEAL_ID must be set').not.toBe('');
  await page.goto(`/projects/${dealId}${query}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('deal-load-skeleton')).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByTestId('deal-load-error'), 'the deal loaded').toHaveCount(0);
}

export async function expectNoEngineFailures(page: Page): Promise<void> {
  await expect(
    page.getByRole('alert').filter({ hasText: /didn.t finish|model errored/i }),
    'no engine in the canonical run failed',
  ).toHaveCount(0);
}

/**
 * Reads one row of the Historicals/Projections worksheet table
 * (`GroundedWorksheet.tsx`: a real `<table>`, first `<td>` is the row label,
 * one `<td>` per period column, `<th>` headers carry the period label e.g.
 * "T12 Mar 2025" or "Year 1 · Model"). Returns the header labels zipped with
 * that row's cell texts. Row is matched by its exact label in the first cell.
 */
export async function worksheetRow(
  page: Page,
  rowLabel: string,
): Promise<Array<{ column: string; value: string }>> {
  const row = page.locator('tr', { hasText: new RegExp(`^${escapeRe(rowLabel)}`, 'i') }).filter({
    has: page.locator(`td:text-is("${rowLabel}")`),
  }).first();
  await expect(row, `a worksheet row labeled "${rowLabel}" exists`).toBeVisible({ timeout: 45_000 });

  const table = row.locator('xpath=ancestor::table[1]');
  const headers = await table.locator('thead th').allInnerTexts();
  const cells = await row.locator('td').allInnerTexts();
  // cells[0] is the label cell itself; headers[0] is the "Line item" header.
  const out: Array<{ column: string; value: string }> = [];
  for (let i = 1; i < cells.length; i += 1) {
    out.push({ column: norm(headers[i] ?? `col ${i}`), value: norm(cells[i]) });
  }
  return out;
}

/**
 * At least one column of worksheet row `rowLabel` reads exactly `expectedText`
 * (after normalization). On failure, lists every column/value actually found
 * so Sam can see which historical column the source data landed in.
 */
export async function expectHistoricalMatches(
  page: Page,
  rowLabel: string,
  expectedText: string,
): Promise<void> {
  const cols = await worksheetRow(page, rowLabel);
  const match = cols.find((c) => c.value === expectedText);
  expect(
    match,
    `row "${rowLabel}" should show "${expectedText}" in some column — actual columns/values: `
      + cols.map((c) => `${c.column}=${c.value}`).join(', '),
  ).toBeTruthy();
}


/**
 * At least one column of worksheet row `rowLabel` parses to a number within
 * [min, max]. Use this instead of `expectHistoricalMatches` whenever the
 * pass-criterion is a dollar figure that could legitimately render with
 * slightly different rounding/formatting than the number Sam quoted (e.g.
 * "Revenue $12.94M" — the UI may show "$12,940,118"). Reports every
 * column/value found on failure.
 */
export async function expectHistoricalInRange(
  page: Page,
  rowLabel: string,
  min: number,
  max: number,
): Promise<void> {
  const cols = await worksheetRow(page, rowLabel);
  const parsed = cols.map((c) => ({ ...c, n: parseNumber(c.value) }));
  const match = parsed.find((c) => c.n != null && c.n >= min && c.n <= max);
  expect(
    match,
    `row "${rowLabel}" should show a value between ${min} and ${max} in some column — actual columns/values: `
      + parsed.map((c) => `${c.column}=${c.value}`).join(', '),
  ).toBeTruthy();
}


/**
 * The column of worksheet row `rowLabel` whose HEADER matches `columnMatch`
 * (e.g. /Year ?1/i) parses to a number within [min, max]. Use for a
 * pass-criterion scoped to one specific period (Year 1 ADR $275–290),
 * where "any column" (as `expectHistoricalInRange` allows) would be too
 * loose. Reports every column/value on failure, same as the others.
 */
export async function expectWorksheetColumnInRange(
  page: Page,
  rowLabel: string,
  columnMatch: RegExp,
  min: number,
  max: number,
): Promise<void> {
  const cols = await worksheetRow(page, rowLabel);
  const target = cols.find((c) => columnMatch.test(c.column));
  expect(
    target,
    `row "${rowLabel}" has no column matching ${columnMatch} — actual columns: `
      + cols.map((c) => c.column).join(', '),
  ).toBeTruthy();
  const n = target ? parseNumber(target.value) : null;
  expect(n, `"${rowLabel}" / "${target?.column}" ("${target?.value}") should parse to a number`).not.toBeNull();
  expect(
    n,
    `"${rowLabel}" / "${target?.column}" is ${target?.value} (parsed ${n}) — expected between ${min} and ${max}`,
  ).toBeGreaterThanOrEqual(min);
  expect(
    n,
    `"${rowLabel}" / "${target?.column}" is ${target?.value} (parsed ${n}) — expected between ${min} and ${max}`,
  ).toBeLessThanOrEqual(max);
}

export { test, expect };
