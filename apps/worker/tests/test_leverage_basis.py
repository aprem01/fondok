"""One leverage rule, one basis per figure (review R2-I-1 / R2-M-2).

A tranche counts in leverage (Total Debt, LTV, LTC, debt yield) exactly when
the cash flows carry it: the senior always; a junior tranche (PACE) once it is
funded AND priced — the same rule that puts it in Sources & Uses. An unpriced
junior principal is published on its own (``pending_debt``), never inside the
leverage totals.

Every figure that says "LTC" names its basis: capital publishes ``ltc`` on all
debt the deal is funded with (senior + priced junior) and ``senior_ltc`` on
the senior alone; the Debt tab's stack LTC equals ``capital.ltc``; the export
splits a Senior block (senior amount / LTV / LTC / terms) from an All-debt
block (total debt / LTV / LTC / debt service / DSCR / debt yield).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-leverage-basis.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.engines.capital import CapitalEngine, CapitalEngineInput, JuniorDebtFunding  # noqa: E402
from app.engines.debt import DebtEngine, DebtEngineInputExt  # noqa: E402
from app.engines.tranche_stack import LoanTranche, compute_debt_stack  # noqa: E402
from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed  # noqa: E402

PACE_AMT = 5_000_000.0
PRICED = {"tranches": {"1": {"principal_usd": PACE_AMT, "rate_pct": 0.08, "amortization_months": 0}}}
PENDING = {"tranches": {"1": {"principal_usd": PACE_AMT}}}


def _debt(ov: dict | None = None):
    return DebtEngine().run(
        DebtEngineInputExt(
            deal_id=uuid4(), loan_amount=23_700_000, ltv=0.65, interest_rate=0.068,
            term_years=5, amortization_years=30, interest_only_years=0,
            noi_by_year=[1_300_000.0] * 5, purchase_price_usd=36_400_000,
            total_capital_usd=44_300_000, debt_stack_overrides=ov,
        )
    )


def test_a_pending_junior_is_out_of_leverage_and_published_on_its_own() -> None:
    r = compute_debt_stack(
        [
            LoanTranche(kind="senior", loan_amount=35e6, fixed_rate=0.08),
            LoanTranche(kind="pace", label="PACE Loan", loan_amount=30e6, terms_pending=True),
        ],
        year_one_noi=5e6, property_value=50e6, total_cost=60e6,
    )
    assert r.total_debt == pytest.approx(35e6)
    assert r.pending_debt == pytest.approx(30e6)
    assert r.ltv == pytest.approx(35e6 / 50e6)
    assert r.ltc == pytest.approx(35e6 / 60e6)
    assert r.debt_yield == pytest.approx(5e6 / 35e6)
    assert any("not in leverage" in w for w in r.warnings)


def test_a_pending_senior_still_counts_its_schedule_runs() -> None:
    r = compute_debt_stack(
        [LoanTranche(kind="senior", loan_amount=35e6, rate_type="floating")],
        year_one_noi=5e6, property_value=50e6, total_cost=60e6,
    )
    assert r.total_debt == pytest.approx(35e6) and r.pending_debt == 0.0


def test_debt_engine_leverage_counts_a_pace_only_once_priced() -> None:
    base, pend, priced = _debt(), _debt(PENDING), _debt(PRICED)
    assert pend.loan_amount == pytest.approx(base.loan_amount)
    assert pend.debt_stack.total_debt == pytest.approx(23_700_000)
    assert pend.debt_stack.pending_debt == pytest.approx(PACE_AMT)
    assert pend.year_one_debt_yield == pytest.approx(base.year_one_debt_yield)
    ltv = {c.name: c.current for c in pend.covenants}["ltv"]
    assert ltv == pytest.approx(23_700_000 / 36_400_000)
    assert priced.debt_stack.total_debt == pytest.approx(23_700_000 + PACE_AMT)
    assert priced.debt_stack.pending_debt == 0.0


def test_capital_publishes_ltc_on_all_debt_and_on_the_senior() -> None:
    base = dict(deal_id=uuid4(), purchase_price=60e6, keys=100, ltv=0.6, loan_costs_pct=0.0)
    plain = CapitalEngine().run(CapitalEngineInput(**base))  # type: ignore[arg-type]
    assert plain.senior_ltc == plain.ltc  # no junior: one number
    out = CapitalEngine().run(
        CapitalEngineInput(
            **base,  # type: ignore[arg-type]
            junior_debt=[JuniorDebtFunding(label="PACE Loan", amount=PACE_AMT)],
        )
    )
    assert out.ltc == pytest.approx((36e6 + PACE_AMT) / out.total_capital)
    assert out.senior_ltc == pytest.approx(36e6 / out.total_capital)
    assert "junior" in (out.provenance["ltc"].formula or "")


def test_run_compare_sources_add_up_with_a_junior() -> None:
    from app.services.run_compare import extract_headline_fields

    cap = {
        "total_capital": 62e6, "equity_amount": 21e6, "debt_amount": 36e6,
        "junior_debt_amount": PACE_AMT, "ltc": 0.66, "senior_ltc": 0.58,
    }
    f = {x["key"]: x for x in extract_headline_fields({"capital": cap})}
    assert f["capital.junior_debt_amount"]["value"] == PACE_AMT
    assert f["capital.debt_amount"]["label"] == "Senior debt (sources)"
    assert f["capital.ltc"]["label"] == "LTC (all debt)"
    assert f["capital.senior_ltc"]["label"] == "Senior LTC"
    total = sum(
        f[k]["value"] for k in ("capital.equity_amount", "capital.debt_amount", "capital.junior_debt_amount")
    )
    assert total == pytest.approx(cap["total_capital"])


async def _run_kimpton(**overrides: object) -> dict:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import get_run_scoped_outputs, run_all_engines

    await run_startup_migrations()
    seed = kimpton_seed()
    seed.update(overrides)
    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        await run_all_engines(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=tenant_id,
            run_id=str(uuid4()), assumptions=seed,
        )
        return await get_run_scoped_outputs(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=tenant_id
        )


@pytest.mark.asyncio
async def test_kimpton_with_a_priced_pace_every_ltc_agrees_and_the_export_splits_bases() -> None:
    from app.export.live_payload import _build_debt

    rows = await _run_kimpton(debt_stack_overrides=dict(PRICED))
    cap = rows["capital"]["outputs"]
    debt = rows["debt"]["outputs"]
    assert cap["junior_debt_amount"] == PACE_AMT
    # The Debt tab's stack LTC IS capital's LTC (all debt ÷ total uses).
    assert debt["debt_stack"]["ltc"] == pytest.approx(cap["ltc"], rel=1e-12)
    ltc_cov = next(c for c in debt["covenants"] if c["name"] == "ltc")
    assert ltc_cov["current"] == pytest.approx(cap["ltc"], rel=1e-12)
    blk = _build_debt(debt, cap)
    senior = cap["debt_amount"]
    price = rows["capital"]["inputs"]["purchase_price"]
    # Senior block: one basis.
    assert blk["loan_amount_usd"] == pytest.approx(senior)
    assert blk["ltv_value"] == pytest.approx(senior / price)
    assert blk["ltc"] == pytest.approx(cap["senior_ltc"])
    # All-debt block: one basis.
    assert blk["total_debt_usd"] == pytest.approx(senior + PACE_AMT)
    assert blk["total_ltv"] == pytest.approx((senior + PACE_AMT) / price)
    assert blk["total_ltc"] == pytest.approx(cap["ltc"])


@pytest.mark.asyncio
async def test_kimpton_with_a_pending_pace_reports_it_apart_from_leverage() -> None:
    from app.export.live_payload import _build_debt

    base = await _run_kimpton()
    rows = await _run_kimpton(debt_stack_overrides=dict(PENDING))
    d0, d = base["debt"]["outputs"], rows["debt"]["outputs"]
    assert d["loan_amount"] == d0["loan_amount"]
    assert d["debt_stack"]["ltv"] == d0["debt_stack"]["ltv"]
    assert d["debt_stack"]["pending_debt"] == PACE_AMT
    blk = _build_debt(d, rows["capital"]["outputs"])
    assert blk["pending_debt_usd"] == PACE_AMT
    assert blk["total_debt_usd"] == pytest.approx(rows["capital"]["outputs"]["debt_amount"])


def test_excel_splits_the_senior_and_all_debt_blocks(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    from app.export import build_excel
    from tests.fixtures.kimpton_export_payload import kimpton_model

    model = kimpton_model()
    model["debt_engine"] = {
        **model["debt_engine"],
        "total_debt_usd": 28_683_922, "total_ltv": 0.79, "total_ltc": 0.66,
        "pending_debt_usd": None,
    }
    out = build_excel("x", model, tmp_path / "lev.xlsx")
    wb = load_workbook(out, read_only=True)
    rows = [[c.value for c in r] for r in wb["Assumptions"].iter_rows()]
    wb.close()
    labels = [str(r[0]) for r in rows if r and r[0] is not None]
    i_sen = labels.index("Senior Debt")
    i_all = labels.index("All Debt (senior + priced junior tranches)")
    senior = labels[i_sen:i_all]
    alld = labels[i_all:]
    assert "Annual Debt Service" not in senior and "Annual Debt Service" in alld
    by = {str(r[0]): r for r in rows[[str(r[0]) for r in rows].index("All Debt (senior + priced junior tranches)"):] if r and r[0]}
    assert by["Total Debt"][1] == 28_683_922
    assert by["LTC"][1] == 0.66
    assert str(by["Pending Junior Principal"][1]).startswith("not available")


# ─────────────── round 3: fee dollars + the senior's pending warning ───────────────

PENDING_FEES = {"tranches": {"1": {"principal_usd": PACE_AMT, "exit_fee_pct": 1.0, "upfront_fee_pct": 2.0}}}
PRICED_FEES = {"tranches": {"1": {**PENDING_FEES["tranches"]["1"], "rate_pct": 0.08, "amortization_months": 0}}}
SENIOR_FEES = {"tranches": {"0": {"exit_fee_pct": 0.5, "upfront_fee_pct": 1.0}}}


def test_fee_dollars_are_senior_beside_the_senior_pct_and_totals_follow_the_rule() -> None:
    sen = 23_700_000.0
    pend = _debt({"tranches": {**SENIOR_FEES["tranches"], **PENDING_FEES["tranches"]}})
    assert pend.origination_fee_usd == pytest.approx(sen * 0.01)
    assert pend.exit_fee_usd == pytest.approx(sen * 0.005)
    # The unpriced PACE is in neither total (one rule).
    assert pend.total_origination_fee_usd == pytest.approx(sen * 0.01)
    assert pend.total_exit_fee_usd == pytest.approx(sen * 0.005)
    pr = _debt({"tranches": {**SENIOR_FEES["tranches"], **PRICED_FEES["tranches"]}})
    assert pr.origination_fee_usd == pytest.approx(sen * 0.01)
    assert pr.total_origination_fee_usd == pytest.approx(sen * 0.01 + PACE_AMT * 0.02)
    assert pr.total_exit_fee_usd == pytest.approx(sen * 0.005 + PACE_AMT * 0.01)
    assert pr.total_exit_fee_usd == pytest.approx(pr.exit_fee_at_sale_usd)


def test_a_senior_with_pending_floating_terms_is_carried_at_the_rate_on_file() -> None:
    out = _debt({"tranches": {0: {"rate_type": "floating"}}})
    sr = out.debt_stack.tranches[0]
    # The floating terms are pending, but the schedule services the senior at
    # the rate on file — and the stack carries that SAME debt service.
    assert sr.terms_pending is True
    assert sr.annual_debt_service == pytest.approx(out.schedule[0].debt_service)
    assert out.debt_stack.total_annual_debt_service == pytest.approx(out.annual_debt_service)
    assert out.debt_stack.year_one_dscr == pytest.approx(out.year_one_dscr)
    w = " ".join(out.debt_stack.warnings)
    assert "floating terms pending" in w and "rate on file" in w
    assert "excluded from debt service" not in w
