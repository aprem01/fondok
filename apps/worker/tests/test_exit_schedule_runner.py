"""FON-118 — the Kimpton golden through the runner, with the exit schedule.

The same path ``test_engine_runner.py::test_run_all_engines_kimpton`` and
``scripts/compare_golden_baseline.py`` take — the seed set injected through
``run_all_engines(..., assumptions=kimpton_seed())``. Pins, to the digit, that
the selected row IS the baseline headline (``evals/baselines/kimpton-golden-
2026-10-16.json``), that the interim rows read the debt engine's monthly
balance, and the computed peak year. Any move here is a golden move.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-exit-schedule-runner.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

# The 2026-10-16 baseline's returns block (returns-exit-plan.md §1.6).
BASELINE_LEVERED_IRR = 0.30507763569294777
BASELINE_UNLEVERED_IRR = 0.1966981834769953
BASELINE_EQUITY_MULTIPLE = 3.173073565683279
BASELINE_TERMINAL_NOI = 5133453.812007522
BASELINE_GROSS_SALE = 73335054.45725031
BASELINE_NET_PROCEEDS = 49645085.86905478


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as session:
        try:
            await session.execute(text("DELETE FROM engine_outputs"))
            await session.commit()
        except Exception:
            pass
    yield


async def _kimpton_run() -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    async with get_session_factory()() as session:
        return await run_all_engines(
            session,
            deal_id=KIMPTON_FIXTURE_DEAL_ID,
            tenant_id=str(uuid4()),
            run_id=str(uuid4()),
            assumptions=kimpton_seed(),
        )


@pytest.mark.asyncio
async def test_kimpton_selected_row_is_the_baseline_headline() -> None:
    results = await _kimpton_run()
    ret = results["returns"]["outputs"]
    rows = ret["exit_schedule"]
    assert len(rows) == 5
    assert [r["exit_month"] for r in rows] == [12, 24, 36, 48, 60]
    sel = rows[4]
    assert sel["is_selected"] is True and sel["exit_year"] == 5
    assert ret["selected_exit_month"] == 60 and ret["selected_exit_year"] == 5
    # Exact — the headline and the selected row are the same arithmetic.
    assert sel["levered_irr"] == ret["levered_irr"] == BASELINE_LEVERED_IRR
    assert sel["unlevered_irr"] == ret["unlevered_irr"] == BASELINE_UNLEVERED_IRR
    assert sel["equity_multiple"] == ret["equity_multiple"] == BASELINE_EQUITY_MULTIPLE
    assert sel["terminal_noi"] == ret["terminal_noi"] == BASELINE_TERMINAL_NOI
    assert sel["gross_sale_price"] == ret["gross_sale_price"] == BASELINE_GROSS_SALE
    assert sel["net_proceeds"] == ret["net_proceeds"] == BASELINE_NET_PROCEEDS
    assert sel["exit_noi_basis"] == "forward_12m"
    assert sel["exit_noi_period_label"] == "Forward 12-month NOI (Year 6)"
    assert sel["refi_included"] is False  # no refinance on the seed
    assert sel["status"] == "computed" and sel["reason"] is None


@pytest.mark.asyncio
async def test_kimpton_interim_rows_are_computed_on_the_projected_forward_year() -> None:
    results = await _kimpton_run()
    ret = results["returns"]["outputs"]
    noi = ret["noi_by_year"]
    debt = results["debt"]["outputs"]
    monthly = debt["monthly_schedule"]
    for k, row in enumerate(ret["exit_schedule"][:4], start=1):
        assert row["status"] == "computed", row
        assert row["is_selected"] is False
        assert row["exit_noi_period_label"] == f"Forward 12-month NOI (Year {k + 1}, projected)"
        assert row["terminal_noi"] == noi[k]
        # The payoff of an interim sale is the senior's balance at that month.
        assert row["loan_payoff"] == monthly[12 * k - 1]["ending_balance"]
        assert row["levered_irr"] is not None and row["unlevered_irr"] is not None
    # The selected exit keeps the debt engine's balance_at_exit (the headline's).
    assert ret["exit_schedule"][4]["loan_payoff"] == debt["balance_at_exit"]


@pytest.mark.asyncio
async def test_kimpton_peak_levered_exit_year_is_year_one() -> None:
    """Documented value: on the seed the forward NOI steps up fastest early
    (Year 1 → Year 2 projected NOI), so the levered IRR of a Year-1 sale is
    the schedule's peak. A change here is a golden move — report it."""
    results = await _kimpton_run()
    ret = results["returns"]["outputs"]
    assert ret["peak_levered_exit_year"] == 1
    peaks = [r for r in ret["exit_schedule"] if r["is_peak_levered"]]
    assert len(peaks) == 1 and peaks[0]["exit_year"] == 1
    irrs = [r["levered_irr"] for r in ret["exit_schedule"]]
    assert irrs[0] == max(irrs)
    assert irrs == sorted(irrs, reverse=True)  # monotone on this seed


@pytest.mark.asyncio
async def test_kimpton_row_provenance_keys_match_the_web_contract() -> None:
    results = await _kimpton_run()
    prov = results["returns"]["outputs"]["provenance"]
    for i in range(5):
        for field in ("levered_irr", "unlevered_irr", "equity_multiple",
                      "terminal_noi", "gross_sale_price", "net_proceeds"):
            key = f"exit_schedule[{i}].{field}"
            assert key in prov, key
            assert prov[key]["reason"] is None
            assert prov[key]["formula"]
