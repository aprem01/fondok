"""FON-44 (R-059) — selectable exit NOI basis for the reversion.

``exit_noi_basis`` (a ``field_overrides`` scalar) picks which NOI the exit cap
is applied to:

* ``forward_12m`` (default) — the exit year's NOI grown one more year. Byte-
  identical to the pre-FON-44 reversion (the Kimpton goldens pin that).
* ``stabilized`` — the stabilized year's NOI grown to the exit year at the
  same growth; the exit year's NOI when stabilization lands after the exit.
* ``terminal_noi_override`` beats both and reports ``override``.
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

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-exit-noi-basis.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402

# A ramping deal: stabilizes in Year 3, flat-ish growth after.
NOI = [1_000_000.0, 2_000_000.0, 3_000_000.0, 3_090_000.0, 3_182_700.0]
G = 0.03
CAP = 0.075


def _input(**kw: object) -> ReturnsEngineInputExt:
    return ReturnsEngineInputExt(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=40_000_000.0,
            ltv=0.6,
            interest_rate=0.065,
            amortization_years=30,
            loan_term_years=5,
            hold_years=5,
            exit_cap_rate=CAP,
            revpar_growth=G,
            expense_growth=0.03,
            selling_costs_pct=0.02,
            closing_costs_pct=0.02,
        ),
        year_one_noi=NOI[0],
        noi_by_year=NOI,
        annual_debt_service=1_560_000.0,
        loan_amount=24_000_000.0,
        equity=17_000_000.0,
        **kw,
    )


def test_default_is_forward_12m_and_unchanged() -> None:
    explicit = ReturnsEngine().run(_input(exit_noi_basis="forward_12m"))
    default = ReturnsEngine().run(_input())
    assert default.exit_noi_basis == "forward_12m"
    assert default.exit_noi_period_label == "Forward 12-month NOI (Year 6)"
    assert default.terminal_noi == NOI[-1] * (1 + G)
    assert default.gross_sale_price == NOI[-1] * (1 + G) / CAP
    assert default.model_dump(exclude={"deal_id"}) == explicit.model_dump(
        exclude={"deal_id"}
    )
    assert "Forward 12-month NOI (Year 6)" in (default.provenance["terminal_noi"].note or "")


def test_stabilized_basis_grows_the_stabilized_year_to_the_exit() -> None:
    fwd = ReturnsEngine().run(_input())
    out = ReturnsEngine().run(_input(exit_noi_basis="stabilized", stabilized_year=3))
    expected = NOI[2] * (1 + G) ** 2
    assert out.exit_noi_basis == "stabilized"
    assert out.exit_noi_period_label == "Stabilized NOI (Year 3, grown to Year 5)"
    assert out.terminal_noi == pytest.approx(expected)
    assert out.gross_sale_price == pytest.approx(expected / CAP)
    assert out.gross_sale_price != pytest.approx(fwd.gross_sale_price)
    trace = out.provenance["terminal_noi"]
    assert "Stabilized NOI (Year 3, grown to Year 5)" in (trace.note or "")
    assert "stabilized_year_noi" in (trace.formula or "")
    assert "Stabilized NOI (Year 3" in (out.provenance["gross_sale_price"].note or "")


def test_stabilized_in_the_exit_year() -> None:
    out = ReturnsEngine().run(_input(exit_noi_basis="stabilized", stabilized_year=5))
    assert out.exit_noi_period_label == "Stabilized NOI (Year 5, the exit year)"
    assert out.terminal_noi == pytest.approx(NOI[4])


def test_stabilization_after_the_exit_uses_exit_year_noi_and_says_so() -> None:
    out = ReturnsEngine().run(_input(exit_noi_basis="stabilized", stabilized_year=7))
    assert out.exit_noi_basis == "stabilized"
    assert out.terminal_noi == pytest.approx(NOI[4])
    assert out.exit_noi_period_label == (
        "Exit-year NOI (Year 5; stabilization in Year 7 falls after the exit)"
    )


def test_stabilized_without_a_year_resolves_one() -> None:
    out = ReturnsEngine().run(_input(exit_noi_basis="stabilized"))
    assert out.exit_noi_basis == "stabilized"
    assert out.exit_noi_period_label.startswith("Stabilized NOI (Year ")


def test_override_beats_every_basis() -> None:
    for basis in ("forward_12m", "stabilized"):
        out = ReturnsEngine().run(
            _input(exit_noi_basis=basis, stabilized_year=3, terminal_noi_override=2_500_000.0)
        )
        assert out.exit_noi_basis == "override"
        assert out.exit_noi_period_label == "Reconciliation override"
        assert out.terminal_noi == 2_500_000.0
        assert out.gross_sale_price == pytest.approx(2_500_000.0 / CAP)


def test_unknown_basis_is_rejected_at_the_engine() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _input(exit_noi_basis="trailing_12m")


def test_runner_coerces_the_override_to_the_allow_list() -> None:
    from app.services.engine_runner import _coerce_exit_noi_basis

    assert _coerce_exit_noi_basis(None) == "forward_12m"
    assert _coerce_exit_noi_basis("stabilized") == "stabilized"
    assert _coerce_exit_noi_basis({"value": "stabilized", "note": ""}) == "stabilized"
    assert _coerce_exit_noi_basis("trailing_12m") == "forward_12m"
    assert _coerce_exit_noi_basis(3) == "forward_12m"


# ─────────────── Through the runner (field_overrides) ───────────────


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


async def _run(overrides: dict) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    async with factory() as session:
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
                "id": deal_id, "tenant": tenant_id, "name": "Exit Basis Hotel",
                "ov": json.dumps(overrides), "now": datetime.now(UTC),
            },
        )
        await session.commit()
        return await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db")
async def test_field_override_selects_the_stabilized_basis() -> None:
    plain = await _run({})
    stab = await _run({"exit_noi_basis": "stabilized", "stabilization_year": 2})

    p_ret = plain["returns"]["outputs"]
    s_ret = stab["returns"]["outputs"]
    assert p_ret["exit_noi_basis"] == "forward_12m"
    hold = p_ret["hold_years"]
    assert p_ret["exit_noi_period_label"] == f"Forward 12-month NOI (Year {hold + 1})"

    assert s_ret["exit_noi_basis"] == "stabilized"
    assert s_ret["exit_noi_period_label"] == f"Stabilized NOI (Year 2, grown to Year {hold})"
    inputs = {
        i["name"]: i["value"] for i in s_ret["provenance"]["terminal_noi"]["inputs"]
    }
    assert inputs["source_year_noi"] == s_ret["noi_by_year"][1]
    assert s_ret["terminal_noi"] == pytest.approx(
        s_ret["noi_by_year"][1] * (1 + inputs["revpar_growth"]) ** (hold - 2)
    )
    assert s_ret["gross_sale_price"] == pytest.approx(
        s_ret["terminal_noi"] / s_ret["exit_cap_rate"]
    )
    assert s_ret["terminal_noi"] != pytest.approx(p_ret["terminal_noi"])
    # Operating NOI is untouched — only the reversion moves.
    assert s_ret["noi_by_year"] == p_ret["noi_by_year"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db")
async def test_terminal_noi_override_beats_the_basis_through_the_runner() -> None:
    out = await _run({"exit_noi_basis": "stabilized", "terminal_noi_override": 4_000_000})
    ret = out["returns"]["outputs"]
    assert ret["exit_noi_basis"] == "override"
    assert ret["exit_noi_period_label"] == "Reconciliation override"
    assert ret["terminal_noi"] == 4_000_000
