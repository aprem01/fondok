"""E-016 — per-line projection methodology on the Expense engine.

Each departmental / undistributed USALI line can be re-driven by the analyst:

* ``growth``       Year-1 anchor * (1 + g)^(t-1); value None → model expense growth
* ``pct_revenue``  value * department revenue (departmental) / total revenue
* ``por``          value * occupied rooms (keys * 365 * occupancy)
* ``par``          value * available rooms (keys * 365)

A line with no entry must run exactly as before — the Kimpton goldens depend on
that, and this file pins it directly as well.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-projection-methods.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

from app.engines.expense import ExpenseEngine, ExpenseEngineInput  # noqa: E402
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear  # noqa: E402

KEYS = 120
OCC = [0.70, 0.72, 0.74, 0.75, 0.75]


def _revenue() -> FBRevenueOutput:
    years = []
    for y in range(1, 6):
        rooms = 10_000_000.0 * (1.05 ** (y - 1))
        fb = 3_000_000.0 * (1.04 ** (y - 1))
        other = 600_000.0 * (1.03 ** (y - 1))
        years.append(
            FBRevenueYear(
                year=y,
                rooms_revenue=rooms,
                fb_revenue=fb,
                other_revenue=other,
                total_revenue=rooms + fb + other,
            )
        )
    return FBRevenueOutput(
        deal_id=uuid4(), years=years, fb_ratio_used=0.3, other_ratio_used=0.06
    )


T12 = {
    "rooms_dept_expense": 2_500_000.0,
    "fb_dept_expense": 2_100_000.0,
    "other_dept_expense": 250_000.0,
    "administrative_general": 1_100_000.0,
    "information_telecom": 200_000.0,
    "sales_marketing": 900_000.0,
    "property_operations": 600_000.0,
    "utilities": 450_000.0,
    "property_taxes": 500_000.0,
    "insurance": 300_000.0,
}


def _run(methods=None, *, keys=KEYS, grow=True, **kw):
    payload = ExpenseEngineInput(
        deal_id=uuid4(),
        revenue=_revenue(),
        hotel_type="full",
        expense_growth=0.03,
        grow_opex_independently=grow,
        t12_actuals=T12,
        occupancy_by_year=OCC,
        projection_methods=methods or {},
        keys=keys if methods else None,
        **kw,
    )
    return ExpenseEngine().run(payload)


def test_default_is_unchanged_when_no_methods() -> None:
    base = ExpenseEngine().run(
        ExpenseEngineInput(
            deal_id=uuid4(),
            revenue=_revenue(),
            hotel_type="full",
            expense_growth=0.03,
            grow_opex_independently=True,
            t12_actuals=T12,
            occupancy_by_year=OCC,
        )
    )
    out = _run()
    assert [y.model_dump() for y in out.years] == [y.model_dump() for y in base.years]
    assert out.provenance.keys() == base.provenance.keys()
    # The active method is still published — growth at the model expense growth.
    lm = out.line_methods["rooms_dept_expense"]
    assert (lm.method, lm.value, lm.source) == ("growth", 0.03, "default")
    assert set(out.line_methods) == {
        "rooms_dept_expense", "fb_dept_expense", "other_dept_expense",
        "administrative_general", "information_telecom", "sales_marketing",
        "property_operations", "utilities",
    }


def test_default_label_is_pct_revenue_when_not_growing_independently() -> None:
    out = _run(grow=False)
    lm = out.line_methods["fb_dept_expense"]
    assert lm.method == "pct_revenue" and lm.source == "default"
    assert lm.value == pytest.approx(0.75)  # HOTEL_TYPE_DEFAULTS['full'].fb_dept_pct


def test_growth_method_explicit_rate() -> None:
    out = _run({"administrative_general": {"method": "growth", "value": 0.05}})
    for t, yr in enumerate(out.years):
        assert yr.undistributed.administrative_general == pytest.approx(
            1_100_000.0 * 1.05**t
        )
    # Total recomputed from lines.
    y3 = out.years[2].undistributed
    assert y3.total == pytest.approx(
        y3.administrative_general + y3.information_telecom + y3.sales_marketing
        + y3.property_operations + y3.utilities
    )
    lm = out.line_methods["administrative_general"]
    assert (lm.method, lm.value, lm.source) == ("growth", 0.05, "override")


def test_growth_method_null_value_uses_model_expense_growth() -> None:
    out = _run({"utilities": {"method": "growth", "value": None}})
    base = _run()
    for a, b in zip(out.years, base.years, strict=True):
        assert a.undistributed.utilities == pytest.approx(b.undistributed.utilities)
    assert out.line_methods["utilities"].value == 0.03


def test_pct_revenue_departmental_uses_department_revenue() -> None:
    out = _run({"rooms_dept_expense": {"method": "pct_revenue", "value": 0.22}})
    rev = _revenue()
    for yr, ry in zip(out.years, rev.years, strict=True):
        assert yr.dept_expenses.rooms == pytest.approx(0.22 * ry.rooms_revenue)
        assert yr.dept_expenses.total == pytest.approx(
            yr.dept_expenses.rooms + yr.dept_expenses.food_beverage
            + yr.dept_expenses.other_operated
        )


def test_pct_revenue_undistributed_uses_total_revenue() -> None:
    out = _run({"sales_marketing": {"method": "pct_revenue", "value": 0.065}})
    rev = _revenue()
    for yr, ry in zip(out.years, rev.years, strict=True):
        assert yr.undistributed.sales_marketing == pytest.approx(0.065 * ry.total_revenue)


def test_pct_revenue_null_value_holds_year1_ratio() -> None:
    out = _run({"fb_dept_expense": {"method": "pct_revenue", "value": None}})
    rev = _revenue()
    ratio = 2_100_000.0 / 3_000_000.0
    assert out.years[0].dept_expenses.food_beverage == pytest.approx(2_100_000.0)
    assert out.years[4].dept_expenses.food_beverage == pytest.approx(
        ratio * rev.years[4].fb_revenue
    )
    assert out.line_methods["fb_dept_expense"].value == pytest.approx(ratio)


def test_por_uses_occupied_rooms() -> None:
    out = _run({"rooms_dept_expense": {"method": "por", "value": 40.0}})
    for t, yr in enumerate(out.years):
        assert yr.dept_expenses.rooms == pytest.approx(40.0 * KEYS * 365 * OCC[t])


def test_par_uses_available_rooms() -> None:
    out = _run({"property_operations": {"method": "par", "value": 12.5}})
    for yr in out.years:
        assert yr.undistributed.property_operations == pytest.approx(12.5 * KEYS * 365)


def test_por_without_keys_keeps_default_and_says_why() -> None:
    out = _run({"rooms_dept_expense": {"method": "por", "value": 40.0}}, keys=None)
    base = _run()
    assert [y.dept_expenses.rooms for y in out.years] == [
        y.dept_expenses.rooms for y in base.years
    ]
    lm = out.line_methods["rooms_dept_expense"]
    assert lm.source == "default" and "unavailable" in (lm.note or "")


def test_override_moves_gop_and_noi() -> None:
    base = _run()
    out = _run({"rooms_dept_expense": {"method": "pct_revenue", "value": 0.30}})
    delta = out.years[1].dept_expenses.rooms - base.years[1].dept_expenses.rooms
    assert out.years[1].gop == pytest.approx(base.years[1].gop - delta)
    assert out.years[1].noi == pytest.approx(base.years[1].noi - delta)


def test_provenance_names_the_method_and_value() -> None:
    out = _run({"rooms_dept_expense": {"method": "por", "value": 40.0}})
    trace = out.provenance["years[2].dept_expenses.rooms"]
    assert trace.assumption_key == "projection_methods.rooms_dept_expense.method"
    assert "por" in (trace.formula or "") and "occupied_rooms" in (trace.formula or "")
    assert trace.inputs[0].value == 40.0
    assert trace.inputs[0].assumption_key == "projection_methods.rooms_dept_expense.value"
    assert "'por'" in (trace.note or "")


def test_mgmt_fee_and_fixed_unaffected() -> None:
    base = _run()
    out = _run({
        line: {"method": "pct_revenue", "value": 0.1}
        for line in ("rooms_dept_expense", "utilities")
    })
    for a, b in zip(out.years, base.years, strict=True):
        assert a.mgmt_fee == b.mgmt_fee
        assert a.fixed_charges == b.fixed_charges


# ─────────────────────── runner routing ───────────────────────


def test_runner_routes_projection_method_paths() -> None:
    from app.services.engine_runner import (
        _coerce_projection_methods,
        _parse_projection_method_path,
        _projection_method_kwargs,
    )

    assert _parse_projection_method_path("projection_methods.utilities.method") == (
        "utilities", "method",
    )
    assert _parse_projection_method_path("projection_methods.mgmt_fee.method") is None
    assert _parse_projection_method_path("projection_methods.utilities.bogus") is None
    base = {
        "keys": 120,
        "projection_methods": {"utilities": {"method": "par", "value": "9.5"}},
        "projection_methods.sales_marketing.method": "growth",
        "projection_methods.rooms_dept_expense.method": "nonsense",
    }
    assert _coerce_projection_methods(base) == {
        "utilities": {"method": "par", "value": 9.5},
        "sales_marketing": {"method": "growth", "value": None},
    }
    kw = _projection_method_kwargs(base)
    assert kw["keys"] == 120
    assert _projection_method_kwargs({"keys": 120}) == {}


def test_override_requires_note() -> None:
    from app.api.deals import _override_needs_note
    from app.services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS

    for k in (
        "projection_methods.rooms_dept_expense.method",
        "projection_methods.rooms_dept_expense.value",
    ):
        assert _override_needs_note(k, _OVERRIDE_NON_ENGINE_KEYS)


# ─────────────── through the API + runner (field_overrides) ───────────────


@pytest.fixture
async def _db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(text("DELETE FROM engine_outputs"))
            await session.commit()
        except Exception:
            pass
    yield


async def _run_deal(overrides: dict) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    async with factory() as session:
        deal_id, tenant_id = str(uuid4()), str(uuid4())
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, keys, status, field_overrides,
                                   created_at, updated_at)
                VALUES (:id, :tenant, :name, 150, 'Draft', :ov, :now, :now)
                """
            ),
            {
                "id": deal_id, "tenant": tenant_id, "name": "Method Hotel",
                "ov": json.dumps(overrides), "now": datetime.now(UTC),
            },
        )
        await session.commit()
        return await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db")
async def test_field_override_reprojects_the_line_through_the_runner() -> None:
    plain = await _run_deal({})
    par = await _run_deal({
        "projection_methods.utilities.method": {"value": "par", "note": "PAR driver"},
        "projection_methods.utilities.value": {"value": 4.25, "note": "PAR driver"},
    })
    p_exp = plain["expense"]["outputs"]
    o_exp = par["expense"]["outputs"]
    assert p_exp["line_methods"]["utilities"]["source"] == "default"
    lm = o_exp["line_methods"]["utilities"]
    assert (lm["method"], lm["value"], lm["source"]) == ("par", 4.25, "override")
    keys = o_exp["provenance"]["years[0].undistributed.utilities"]
    assert keys["assumption_key"] == "projection_methods.utilities.method"
    for yr in o_exp["years"]:
        assert yr["undistributed"]["utilities"] == pytest.approx(4.25 * 150 * 365)
    # Other lines untouched.
    for a, b in zip(o_exp["years"], p_exp["years"], strict=True):
        assert a["undistributed"]["sales_marketing"] == b["undistributed"]["sales_marketing"]
        assert a["dept_expenses"] == b["dept_expenses"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db")
async def test_patch_without_note_is_refused() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        deal_id = (await c.post("/deals", json={"name": "Gate"})).json()["id"]
        r = await c.patch(
            f"/deals/{deal_id}",
            json={"field_overrides": {
                "projection_methods.rooms_dept_expense.method": {"value": "por"},
            }},
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["keys"] == [
            "projection_methods.rooms_dept_expense.method"
        ]
        r = await c.patch(
            f"/deals/{deal_id}",
            json={"field_overrides": {
                "projection_methods.rooms_dept_expense.method": {
                    "value": "por", "note": "Brand standard cost per occupied room",
                },
                "projection_methods.rooms_dept_expense.value": {
                    "value": 38, "note": "Brand standard cost per occupied room",
                },
            }},
        )
        assert r.status_code == 200, r.text
        stored = r.json()["field_overrides"]
        assert stored["projection_methods.rooms_dept_expense.method"]["note"]
