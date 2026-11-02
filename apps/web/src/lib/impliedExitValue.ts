/**
 * The Projections statement's "Implied Value" row — on the basis the Returns
 * engine actually sells at (Wave 5, Sam #1).
 *
 * The Returns engine prices the reversion as
 * ``gross_sale_price = terminal_noi ÷ exit_cap_rate``, where ``terminal_noi``
 * is a Cash NOI (after property taxes, insurance and the FF&E reserve). The row
 * used to capitalise EBITDA (GOP − management fee), which sits above fixed
 * charges and the reserve and overstated the exit by roughly half. Now:
 *
 *  • every operating year capitalises THAT year's Cash NOI at the exit cap
 *    rate the Returns engine ran on — what the hotel would be worth on that
 *    year's earnings, labelled as such;
 *  • the Exit Year column shows the engine's own ``gross_sale_price`` (the
 *    forward-year Cash NOI ÷ exit cap) — the one number IRR is computed from;
 *  • a missing input is ``null`` with the reason, never a 0 and never a
 *    hard-coded default cap rate.
 */

export const IMPLIED_VALUE_LABEL = 'Implied Value (Cash NOI ÷ exit cap)';

/** One cell: a figure, or null with the reason; ``note`` is the tooltip. */
export interface ImpliedValueCell {
  value: number | null;
  note: string;
}

const isPos = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0;
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

export const NO_EXIT_CAP_NOTE =
  'Not available: no exit cap rate — the Returns engine has not run on this deal';

/**
 * The exit cap rate the Returns engine ran on (``returns.exit_cap_rate``), or
 * the resolved assumption the run used (provenance value). Never a hard-coded
 * default: with neither, the row dashes.
 */
export function resolveEngineExitCap(
  engineCap: number | null | undefined,
  resolvedAssumption: number | null | undefined,
): number | null {
  if (isPos(engineCap)) return engineCap;
  if (isPos(resolvedAssumption)) return resolvedAssumption;
  return null;
}

/** Each operating year's Cash NOI ÷ exit cap. */
export function impliedValueByYear(
  cashNoi: ReadonlyArray<number | null | undefined>,
  exitCapRate: number | null,
): ImpliedValueCell[] {
  return cashNoi.map((noi) => {
    if (!isPos(exitCapRate)) return { value: null, note: NO_EXIT_CAP_NOTE };
    if (!isNum(noi)) {
      return { value: null, note: 'Not available: the expense engine published no Cash NOI for this year' };
    }
    return {
      value: noi / exitCapRate,
      note:
        `Implied value = this year's Cash NOI (after property taxes, insurance and FF&E reserve) ÷ exit cap rate ${pct(exitCapRate)}. ` +
        'What the hotel would be worth on this year’s earnings — the sale itself is priced in the Exit Year column.',
    };
  });
}

/** The Exit Year column: the Returns engine's gross sale price, as published. */
export function exitSaleCell(args: {
  grossSalePrice: number | null;
  terminalNoi: number | null;
  exitCapRate: number | null;
  exitNoiPeriodLabel: string | null;
}): ImpliedValueCell {
  const { grossSalePrice, terminalNoi, exitCapRate, exitNoiPeriodLabel } = args;
  if (!isNum(grossSalePrice)) {
    return { value: null, note: 'Not available: the Returns engine has not published a gross sale price' };
  }
  const basis = exitNoiPeriodLabel ?? 'Forward 12-month Cash NOI';
  const inputs =
    isNum(terminalNoi) && isPos(exitCapRate)
      ? ` = ${basis} ÷ exit cap rate ${pct(exitCapRate)}`
      : ` (${basis} ÷ exit cap rate)`;
  return {
    value: grossSalePrice,
    note: `Returns engine gross sale price${inputs} — the reversion every IRR and the equity multiple are computed from.`,
  };
}
