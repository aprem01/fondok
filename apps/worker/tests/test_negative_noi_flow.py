"""FON-63 — negative NOI flows through the model.

A ramp / PIP year can carry a negative NOI (two external testers hit Year 1 at
−$69,982 and −$4.9M). The model used to stop at the Debt engine's
``noi_by_year ge=0`` validator and skip Returns, Sensitivity, Partnership and
Cash Flow. Now:

* Debt completes; a year whose NOI is ≤ 0 has ``dscr = None`` (N/A) and its
  uncovered debt service is ``shortfall_usd``; the output lists the negative
  years and carries one warning sentence.
* Returns runs on the actual (negative) cash flows and yields a float IRR.
* Cash Flow and Partnership complete.
* The one real stop is a non-positive EXIT-year NOI (no sale value) — Returns
  fails with a sentence that says so.
"""

# ruff: noqa: RUF001, RUF002

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-negative-noi.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.debt import DebtEngine, DebtEngineInputExt  # noqa: E402
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402

# Year 1 NOI from the second tester's deal; positive from Year 2 on.
NEGATIVE_Y1_NOI = [-69_982.0, 2_400_000.0, 3_100_000.0, 3_300_000.0, 3_400_000.0]


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
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


def _debt(noi: list[float]) -> DebtEngineInputExt:
    # $28.6M at 6.5% interest-only → ~$1.86M/yr of debt service.
    return DebtEngineInputExt(
        deal_id=uuid4(),
        loan_amount=28_600_000.0,
        ltv=0.60,
        interest_rate=0.065,
        term_years=5,
        amortization_years=30,
        interest_only_years=5,
        noi_by_year=noi,
    )


def test_debt_completes_on_negative_year_one_noi() -> None:
    out = DebtEngine().run(_debt(NEGATIVE_Y1_NOI))
    ds1 = out.schedule[0].debt_service
    assert 1_800_000 < ds1 < 2_000_000

    y1 = out.schedule[0]
    assert y1.dscr is None
    assert y1.shortfall_usd == pytest.approx(ds1 + 69_982.0)
    # Covered years carry a real ratio and no shortfall.
    assert out.schedule[2].dscr == pytest.approx(3_100_000.0 / out.schedule[2].debt_service)
    assert out.schedule[2].shortfall_usd == 0.0

    assert out.negative_noi_years == [1]
    assert out.total_shortfall_usd == pytest.approx(
        sum(yr.shortfall_usd for yr in out.schedule)
    )
    assert out.noi_warning == (
        f"Year 1 NOI is negative (−$69,982); debt service shortfall "
        f"${round(ds1 + 69_982.0):,}; DSCR N/A for Year 1"
    )
    # Headline Year-1 / entry DSCR are N/A; the average skips the N/A year.
    assert out.year_one_dscr is None
    assert out.entry_dscr is None
    defined = [yr.dscr for yr in out.schedule if yr.dscr is not None]
    assert len(defined) == 4
    assert out.avg_dscr == pytest.approx(sum(defined) / 4)
    # The N/A year's provenance says why.
    note = out.provenance["schedule[0].shortfall_usd"].note or ""
    assert "DSCR is N/A for Year 1" in note
    assert "negative" in note
    assert "schedule[0].dscr" not in out.provenance


def test_zero_noi_year_is_na_but_not_negative() -> None:
    out = DebtEngine().run(_debt([0.0, 2_400_000.0, 3_000_000.0]))
    assert out.schedule[0].dscr is None
    assert out.schedule[0].shortfall_usd == pytest.approx(out.schedule[0].debt_service)
    assert out.negative_noi_years == []
    assert out.noi_warning is None


def test_positive_noi_deal_is_unchanged() -> None:
    out = DebtEngine().run(_debt([2_000_000.0, 2_400_000.0, 3_100_000.0]))
    assert out.negative_noi_years == []
    assert out.noi_warning is None
    assert out.year_one_dscr == pytest.approx(2_000_000.0 / out.schedule[0].debt_service)
    # Year 1 is thin (DSCR > 1 here), so there is no shortfall anywhere.
    assert out.total_shortfall_usd == 0.0


def _assumptions(**kw: float) -> ModelAssumptions:
    base = {
        "purchase_price": 44_000_000.0,
        "ltv": 0.65,
        "interest_rate": 0.065,
        "amortization_years": 30,
        "loan_term_years": 5,
        "hold_years": 5,
        "exit_cap_rate": 0.075,
        "revpar_growth": 0.03,
        "expense_growth": 0.03,
        "selling_costs_pct": 0.02,
        "closing_costs_pct": 0.02,
    }
    base.update(kw)
    return ModelAssumptions(**base)


def _returns(noi: list[float], **kw: object) -> ReturnsEngineInputExt:
    return ReturnsEngineInputExt(
        deal_id=uuid4(),
        assumptions=_assumptions(),
        year_one_noi=noi[0],
        noi_by_year=noi,
        annual_debt_service=1_859_000.0,
        loan_amount=28_600_000.0,
        equity=16_000_000.0,
        **kw,
    )


def test_returns_runs_on_the_actual_negative_cash_flow() -> None:
    out = ReturnsEngine().run(_returns(NEGATIVE_Y1_NOI))
    assert isinstance(out.levered_irr, float)
    assert isinstance(out.unlevered_irr, float)
    # The Year-1 levered flow carries the negative NOI less debt service.
    assert out.cash_flows[1] == pytest.approx(-69_982.0 - 1_859_000.0)
    assert out.noi_by_year[0] == -69_982.0


def test_non_positive_terminal_noi_stops_returns_with_the_new_sentence() -> None:
    from pydantic import ValidationError

    from app.services.engine_runner import _engine_error_text

    with pytest.raises(ValidationError) as info:
        ReturnsEngine().run(_returns([1_000_000.0, 500_000.0, 0.0, -50_000.0, -90_000.0]))
    assert [e["loc"] for e in info.value.errors()] == [("terminal_noi",)]
    text_ = _engine_error_text("returns", info.value)
    assert text_.startswith("Returns: terminal_noi = ")
    assert (
        "Exit-year NOI is not positive, so no exit value can be computed. "
        "Check the hold period, growth or the stabilized exit basis."
    ) in text_
    assert "Sensitivity, Partnership and Cash Flow were not run" in text_


async def _deal(session, overrides: dict) -> tuple[str, str]:
    deal_id, tenant_id = str(uuid4()), str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, field_overrides,
                               created_at, updated_at)
            VALUES (:id, :tenant, :name, 'Draft', :ov, :now, :now)
            """
        ),
        {
            "id": deal_id, "tenant": tenant_id, "name": "Negative NOI Hotel",
            "ov": json.dumps(overrides), "now": datetime.now(UTC),
        },
    )
    await session.commit()
    return deal_id, tenant_id


@pytest.mark.asyncio
async def test_full_chain_completes_on_a_negative_year_one_noi() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import ENGINE_NAMES, run_all_engines

    factory = get_session_factory()
    async with factory() as session:
        deal_id, tenant_id = await _deal(
            session,
            {"noi_override_by_year": [-69_982.0, 2_400_000.0, 3_100_000.0,
                                      3_300_000.0, 3_400_000.0]},
        )
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )

    for name in ENGINE_NAMES:
        assert results[name]["status"] == "complete", (name, results[name])

    debt = results["debt"]["outputs"]
    assert debt["schedule"][0]["dscr"] is None
    assert debt["schedule"][0]["shortfall_usd"] > 0
    assert debt["negative_noi_years"] == [1]
    assert debt["total_shortfall_usd"] >= debt["schedule"][0]["shortfall_usd"]
    assert debt["noi_warning"].startswith("Year 1 NOI is negative (−$69,982)")
    assert "DSCR N/A for Year 1" in debt["noi_warning"]

    returns = results["returns"]["outputs"]
    assert isinstance(returns["levered_irr"], float)
    assert returns["noi_by_year"][0] == -69_982.0
    assert returns["exit_noi_basis"] == "forward_12m"
