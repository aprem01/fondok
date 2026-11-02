"""Wave 5 (Sam #3, review I1/I6) — the historical baseline behind the exports
reads the statement the way the web's Historical P&L does, on the REAL Kimpton
Angler statements (tests/fixtures/real_payloads).

* Other Revenue = Other Operated Departments + Miscellaneous Income (FY2023:
  15,885.6 + 1,004,790 = 1,020,675.6 — the export used to carry 15,885.6).
* Fees & fixed = mgmt fee + FF&E reserve + property tax + insurance + rent +
  other non-operating expense (FY2023 2,788,808.7; was tax + insurance + mgmt).
* Non-operating income is signed by the statement's own convention, settled
  against its stated non-operating total: FY2023 prints -9,638.44 in an
  expense-signed section (= +9,638.44 of income); the T-12 prints +21,923.6
  income-positive.
* ``half_unit`` — the precision the extractor read a figure at.

The same vectors are pinned on the web side
(apps/web/__tests__/historicalsFooting.test.ts), so the two cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "real_payloads"


def _fields(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["fields"]


def _year(name: str, fy: int, doc_type: str):
    from app.engines.historical_baseline import build_baseline_from_pnls

    base = build_baseline_from_pnls(
        [{"fiscal_year": fy, "document_id": f"doc-{fy}", "doc_type": doc_type, "fields": _fields(name)}]
    )
    assert len(base.years) == 1
    return base.years[0]


def test_fy2023_other_revenue_includes_misc_income() -> None:
    y = _year("anglers_annual_pnl_real.json", 2023, "PNL")
    assert y.other_operated_revenue == pytest.approx(15_885.6)
    assert y.misc_income == pytest.approx(1_004_790)
    assert y.other_revenue == pytest.approx(1_020_675.6)


def test_fy2023_fixed_block_carries_ffe_and_rent() -> None:
    y = _year("anglers_annual_pnl_real.json", 2023, "PNL")
    assert y.ffe_reserve == pytest.approx(517_608)
    assert y.rent == pytest.approx(17_917.7)
    assert y.other_nonop_expense == pytest.approx(0)
    assert y.fixed_expenses == pytest.approx(2_788_808.7)


def test_fy2023_nonop_income_printed_negative_in_an_expense_signed_section() -> None:
    y = _year("anglers_annual_pnl_real.json", 2023, "PNL")
    assert y.nonop_income_basis == "expense_signed"
    assert y.nonop_income == pytest.approx(9_638.44)


def test_t12_nonop_income_printed_income_positive() -> None:
    y = _year("anglers_t12_real.json", 2024, "T12")
    assert y.nonop_income_basis == "income_positive"
    assert y.nonop_income == pytest.approx(21_923.6)
    assert y.other_revenue == pytest.approx(1_292_426.6)


def test_unsettled_sign_is_none_never_guessed() -> None:
    from app.engines.historical_baseline import build_baseline_from_pnls

    fields = [
        f
        for f in _fields("anglers_annual_pnl_real.json")
        if "non_operating.total" not in f["field_name"]
        and "total_non_operating" not in f["field_name"]
    ]
    y = build_baseline_from_pnls([{"fiscal_year": 2023, "doc_type": "PNL", "fields": fields}]).years[0]
    assert y.nonop_income_basis == "unresolved"
    assert y.nonop_income is None


def test_half_unit_reads_the_printed_precision() -> None:
    from app.services.statement_lines import half_unit

    assert half_unit(12_940_200, "Total Revenues ... 1.29402e+07") == pytest.approx(50)
    assert half_unit(21_923.6, "Income\t2077.49\t1734.91\t21923.6") == pytest.approx(0.05)
    assert half_unit(11_000_005, None) == pytest.approx(0.5)
    assert half_unit(15_885.6, None) == pytest.approx(0.05)
    assert half_unit(0, "0") == 0


def test_the_api_wire_shape_carries_the_new_lines() -> None:
    from app.api.documents import HistoricalYearOut, _year_dict

    y = _year("anglers_annual_pnl_real.json", 2023, "PNL")
    out = HistoricalYearOut(**_year_dict(y))
    assert out.other_revenue == pytest.approx(1_020_675.6)
    assert out.nonop_income == pytest.approx(9_638.44)
