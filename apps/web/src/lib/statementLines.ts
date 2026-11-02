/**
 * Statement-line arithmetic the Historical P&L foots with (Wave 5, Sam #3).
 * Mirrored by ``apps/worker/app/services/statement_lines.py`` (the historical
 * baseline behind the exports) — same rules, same real-statement tests.
 *
 *  • ``halfUnitOf`` — the precision the extractor read a figure at. The
 *    extractor transcribes spreadsheet cells as printed, often in 6-significant
 *    -figure exponent form ("1.29402e+07" = 12,940,200 ± 50). A footing whose
 *    lines and total were read that way can miss by tens of dollars without
 *    any line missing, so the tolerance is the sum of the half-units of the
 *    figures summed — never a flat $1.
 *  • ``normalizeNonopIncome`` — non-operating INCOME signed by the statement's
 *    own convention. The line is printed either income-positive (total
 *    non-operating = expenses − income; the Kimpton T-12) or inside an
 *    expense-signed section where income is a negative deduction (total =
 *    expenses + printed value; the Kimpton FY2023 P&L prints −9,638.44). The
 *    extractor transcribes the print faithfully, so the reader settles the
 *    sign against the stated total; with no total that agrees, the sign is
 *    unresolved and nothing is guessed.
 */
import type { ExtractionField } from '@/lib/api';

const NUMBER_TOKEN = /\(?-?\$?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?\)?|\(?-?\$?\.\d+(?:[eE][-+]?\d+)?\)?/g;

function parseToken(tok: string): number | null {
  let s = tok.replace(/[$,]/g, '');
  let neg = false;
  if (s.startsWith('(') && s.endsWith(')')) {
    neg = true;
    s = s.slice(1, -1);
  }
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return neg ? -n : n;
}

/** The unit of the last digit a token states: "1.29402e+07" → 100, "17917.7" → 0.1. */
function unitOfToken(tok: string): number {
  const s = tok.replace(/[$,()\-+]/g, '');
  const [mantissa, exp] = s.split(/[eE]/);
  const dot = mantissa.indexOf('.');
  const decimals = dot < 0 ? 0 : mantissa.length - dot - 1;
  const e = exp != null ? Number(exp) : 0;
  return 10 ** (e - decimals);
}

/** The value's own digits, when no raw token states it. */
export function halfUnitOfValue(value: number): number {
  if (!Number.isFinite(value) || value === 0) return 0;
  if (Number.isInteger(value)) return 0.5;
  return unitOfToken(String(value)) / 2;
}

/** Half a unit in the last significant figure the extractor read ``field`` at. */
export function halfUnitOf(field: Pick<ExtractionField, 'value' | 'raw_text'> | undefined | null): number {
  if (!field || typeof field.value !== 'number' || !Number.isFinite(field.value)) return 0;
  const value = field.value;
  if (value === 0) return 0;
  const raw = typeof field.raw_text === 'string' ? field.raw_text : '';
  const tokens = raw.match(NUMBER_TOKEN) ?? [];
  // The LAST matching token: a row of monthly values ends with the period total.
  for (let i = tokens.length - 1; i >= 0; i--) {
    const parsed = parseToken(tokens[i]);
    if (parsed != null && Math.abs(parsed - value) <= 1e-9 * Math.max(1, Math.abs(value))) {
      return unitOfToken(tokens[i]) / 2;
    }
  }
  return halfUnitOfValue(value);
}

/** A figure with the half-unit it was read at. */
export interface ReadFigure {
  value: number;
  halfUnit: number;
}

export type NonopIncomeBasis = 'income_positive' | 'expense_signed' | 'zero' | 'unresolved';

/**
 * Sign non-operating income by the statement's own convention.
 *
 * ``expenses`` are the other lines of the non-operating section the statement
 * states (rent, property taxes, insurance, other); ``totals`` every stated
 * "total non-operating" candidate. The convention whose identity reproduces a
 * stated total within the precision of the figures wins; otherwise the sign is
 * unresolved (``value: null``).
 */
export function normalizeNonopIncome(
  income: ReadFigure | null,
  expenses: readonly ReadFigure[],
  totals: readonly ReadFigure[],
): { value: number | null; basis: NonopIncomeBasis | null } {
  if (income == null) return { value: null, basis: null };
  if (income.value === 0) return { value: 0, basis: 'zero' };
  const sum = expenses.reduce((a, e) => a + e.value, 0);
  const sumTol = expenses.reduce((a, e) => a + e.halfUnit, 0);
  for (const total of totals) {
    const tol = sumTol + income.halfUnit + total.halfUnit;
    const asIncome = Math.abs(sum - income.value - total.value);
    const asDeduction = Math.abs(sum + income.value - total.value);
    if (asIncome <= tol && asIncome < asDeduction) return { value: income.value, basis: 'income_positive' };
    if (asDeduction <= tol && asDeduction < asIncome) return { value: -income.value, basis: 'expense_signed' };
  }
  return { value: null, basis: 'unresolved' };
}
