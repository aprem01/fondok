"""Statement-line arithmetic for historical P&Ls (Wave 5, Sam #3).

Mirrored by ``apps/web/src/lib/statementLines.ts`` (the Historical P&L
worksheet) — same rules, pinned on the same real Kimpton statements by
``tests/test_historical_baseline_footing.py`` and the web's
``__tests__/historicalsFooting.test.ts``.

* :func:`half_unit` — the precision the extractor read a figure at. Cells are
  transcribed as printed, often in 6-significant-figure exponent form
  ("1.29402e+07" = 12,940,200 ± 50), so an identity over such figures holds
  only to the sum of their half-units.
* :func:`normalize_nonop_income` — non-operating INCOME signed by the
  statement's own convention. It is printed either income-positive (total
  non-operating = expenses - income; the Kimpton T-12) or inside an
  expense-signed section where income is a negative deduction (total =
  expenses + printed value; the Kimpton FY2023 P&L prints -9,638.44). The
  extractor is faithful to the print, so the reader settles the sign against
  the stated total; with no total that agrees, the sign is unresolved and
  nothing is guessed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

NonopIncomeBasis = Literal["income_positive", "expense_signed", "zero", "unresolved"]

_NUMBER_TOKEN = re.compile(
    r"\(?-?\$?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?\)?|\(?-?\$?\.\d+(?:[eE][-+]?\d+)?\)?"
)


def _parse_token(tok: str) -> float | None:
    s = tok.replace("$", "").replace(",", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    if not math.isfinite(n):
        return None
    return -n if neg else n


def _unit_of_token(tok: str) -> float:
    """The unit of the last digit a token states: "1.29402e+07" → 100."""
    s = re.sub(r"[$,()\-+]", "", tok)
    parts = re.split(r"[eE]", s)
    mantissa = parts[0]
    exp = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    decimals = len(mantissa) - mantissa.index(".") - 1 if "." in mantissa else 0
    return 10.0 ** (exp - decimals)


def half_unit_of_value(value: float) -> float:
    """Half a unit of the value's own last digit (no raw text to read)."""
    if not math.isfinite(value) or value == 0:
        return 0.0
    if float(value).is_integer():
        return 0.5
    return _unit_of_token(repr(float(value))) / 2


def half_unit(value: float | None, raw_text: str | None) -> float:
    """Half a unit in the last significant figure ``value`` was read at.

    The LAST token in ``raw_text`` equal to the value wins (a row of monthly
    figures ends with the period total); without one, the value's own digits.
    """
    if value is None or not math.isfinite(value) or value == 0:
        return 0.0
    tokens = _NUMBER_TOKEN.findall(raw_text or "")
    for tok in reversed(tokens):
        parsed = _parse_token(tok)
        if parsed is not None and abs(parsed - value) <= 1e-9 * max(1.0, abs(value)):
            return _unit_of_token(tok) / 2
    return half_unit_of_value(value)


@dataclass(frozen=True)
class ReadFigure:
    """A figure with the half-unit it was read at."""

    value: float
    half_unit: float


def normalize_nonop_income(
    income: ReadFigure | None,
    expenses: Sequence[ReadFigure],
    totals: Sequence[ReadFigure],
) -> tuple[float | None, NonopIncomeBasis | None]:
    """Sign non-operating income by the statement's own convention.

    ``expenses`` are the other non-operating lines the statement states (rent,
    property taxes, insurance, other); ``totals`` every stated "total
    non-operating" candidate. The convention whose identity reproduces a stated
    total within the precision of the figures wins; otherwise
    ``(None, "unresolved")``.
    """
    if income is None:
        return None, None
    if income.value == 0:
        return 0.0, "zero"
    e_sum = sum(e.value for e in expenses)
    e_tol = sum(e.half_unit for e in expenses)
    for total in totals:
        tol = e_tol + income.half_unit + total.half_unit
        as_income = abs(e_sum - income.value - total.value)
        as_deduction = abs(e_sum + income.value - total.value)
        if as_income <= tol and as_income < as_deduction:
            return income.value, "income_positive"
        if as_deduction <= tol and as_deduction < as_income:
            return -income.value, "expense_signed"
    return None, "unresolved"


__all__ = [
    "NonopIncomeBasis",
    "ReadFigure",
    "half_unit",
    "half_unit_of_value",
    "normalize_nonop_income",
]
