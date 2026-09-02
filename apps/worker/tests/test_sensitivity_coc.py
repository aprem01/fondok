"""FON-68 step 5 — the Returns tab's Year-1 Cash-on-Cash sensitivity grid.

Year-1 CoC = Year-1 cash-flow-after-debt ÷ equity. It is flat against exit
cap / RevPAR growth (both bite in later years), so the only meaningful grid
is a financing one — loan amount (rows) × loan rate (cols), which the
SensitivityEngine runs in ``mode="financing"`` (re-sizing the loan + re-
pricing debt per cell, backing equity out of the fixed total capital).

These tests lock the mechanism the tab reads:
  * a ``year_one_coc`` matrix is emitted in ``matrices``;
  * its values actually VARY across the two axes (not a flat grid);
  * the two legacy top-level grids (IRR + EM) are untouched.
"""

from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.debt import DebtEngineInputExt  # noqa: E402
from app.engines.returns import ReturnsEngineInputExt  # noqa: E402
from app.engines.sensitivity import (  # noqa: E402
    SensitivityEngine,
    SensitivityInput,
    SensitivitySpec,
)


def _assumptions() -> ModelAssumptions:
    return ModelAssumptions(
        purchase_price=50_000_000,
        ltv=0.60,
        interest_rate=0.065,
        hold_years=5,
        exit_cap_rate=0.07,
        revpar_growth=0.03,
    )


def _coc_input() -> SensitivityInput:
    deal_id = uuid4()
    a = _assumptions()
    total_capital = 60_000_000.0
    base_loan = 36_000_000.0
    returns_input = ReturnsEngineInputExt(
        deal_id=deal_id,
        assumptions=a,
        year_one_noi=4_000_000,
        annual_debt_service=base_loan * a.interest_rate,
        loan_amount=base_loan,
        equity=total_capital - base_loan,
    )
    debt_input = DebtEngineInputExt(
        deal_id=deal_id,
        loan_amount=base_loan,
        ltv=a.ltv,
        interest_rate=a.interest_rate,
        term_years=5,
        amortization_years=0,  # interest-only senior
        noi_by_year=[4_000_000 * (1.03 ** y) for y in range(5)],
    )
    loan_v = [base_loan * m for m in (0.9, 1.0, 1.1)]
    rate_v = [0.055, 0.065, 0.075]
    specs = [
        SensitivitySpec(
            key="coc_loan_rate",
            label="Year-1 Cash-on-Cash — Loan Amount × Loan Rate",
            row_variable="loan_amount",
            row_values=loan_v,
            col_variable="interest_rate",
            col_values=rate_v,
            metric="year_one_coc",
            mode="financing",
        ),
    ]
    # The top-level primary matrix always runs in returns mode, so it keeps the
    # canonical exit-cap × RevPAR axes; the CoC financing grid rides in ``specs``.
    return SensitivityInput(
        deal_id=deal_id,
        base_returns_input=returns_input,
        row_variable="exit_cap_rate",
        row_values=[0.065, 0.07, 0.075],
        col_variable="revpar_growth",
        col_values=[0.02, 0.03, 0.04],
        metric="levered_irr",
        specs=specs,
        base_debt_input=debt_input,
        total_capital=total_capital,
    )


def test_year_one_coc_matrix_emitted() -> None:
    out = SensitivityEngine().run(_coc_input())
    coc = next((m for m in out.matrices if m.key == "coc_loan_rate"), None)
    assert coc is not None, "year_one_coc matrix missing from sensitivity output"
    assert coc.metric == "year_one_coc"
    assert len(coc.cells) == 9  # 3 x 3


def test_year_one_coc_varies_across_axes() -> None:
    out = SensitivityEngine().run(_coc_input())
    coc = next(m for m in out.matrices if m.key == "coc_loan_rate")
    values = [c.value for c in coc.cells]
    # The grid must genuinely vary — a flat grid would mean the metric is
    # insensitive to the axes and the tab renders a dead panel.
    assert max(values) - min(values) > 1e-4
    # And it must vary along BOTH axes independently.
    by_row = {c.row_value for c in coc.cells if abs(c.col_value - 0.065) < 1e-9}
    row_vals = [
        next(c.value for c in coc.cells if c.row_value == r and abs(c.col_value - 0.065) < 1e-9)
        for r in sorted(by_row)
    ]
    assert len(set(round(v, 6) for v in row_vals)) > 1  # varies along loan axis
    col_vals = [
        next(c.value for c in coc.cells if abs(c.row_value - 36_000_000.0) < 1.0 and abs(c.col_value - rate) < 1e-9)
        for rate in (0.055, 0.065, 0.075)
    ]
    assert len(set(round(v, 6) for v in col_vals)) > 1  # varies along rate axis
