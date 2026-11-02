"""Every consumer reads the one debt-service series (refi review P-2).

The Kimpton fixture with a 2-year interest-only stub, run through the whole
chain: the debt schedule, Returns' levered flows, the Cash Flow statement,
the sensitivity grid's base cells and the partnership waterfall must all
charge the same per-year payments (IO, then amortizing).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-io-stub-consumers.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


async def _run(io_years: int) -> dict:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import run_all_engines

    await run_startup_migrations()
    a = kimpton_seed()
    a["interest_only_years"] = io_years
    async with get_session_factory()() as session:
        return await run_all_engines(
            session,
            deal_id=KIMPTON_FIXTURE_DEAL_ID,
            tenant_id=str(uuid4()),
            run_id=str(uuid4()),
            assumptions=a,
        )


@pytest.mark.asyncio
async def test_io_stub_one_series_everywhere() -> None:
    r = await _run(2)
    for name, payload in r.items():
        assert payload["status"] == "complete", name
    debt = r["debt"]["outputs"]
    sched = [y["debt_service"] for y in debt["schedule"][:5]]
    assert sched[0] == pytest.approx(sched[1])
    assert sched[2] > sched[1] * 1.1  # amortization starts in Year 3
    assert debt["debt_service_by_year"] == pytest.approx(sched)

    # Returns: each operating year's levered flow is Cash NOI − that year's
    # scheduled debt service.
    ret = r["returns"]["outputs"]
    noi = [y["noi"] for y in r["expense"]["outputs"]["years"]]
    for i in range(4):
        assert ret["cash_flows"][i + 1] == pytest.approx(noi[i] - sched[i], rel=1e-9)

    # Cash Flow statement: interest + principal is the schedule, and no
    # phantom "Refinance / Junior Debt Service" line papers over a gap.
    lev = {ln["label"]: ln["values"] for ln in r["cash_flow"]["outputs"]["levered"]}
    assert "Refinance / Junior Debt Service" not in lev
    for y in range(1, 6):
        assert -(lev["Interest Expense"][y] + lev["Principal Amortization"][y]) == pytest.approx(
            sched[y - 1], rel=1e-9
        )
    net = lev["Net Cash Flow to Equity"]
    for y in range(1, 5):
        assert net[y] == pytest.approx(ret["cash_flows"][y], rel=1e-9)

    # Sensitivity: every base cell reproduces the headline it varies around.
    sens = r["sensitivity"]["outputs"]
    for m in sens["matrices"]:
        base_cells = [c for c in m["cells"] if c["is_base"]]
        if not base_cells or m["metric"] not in ret:
            continue
        assert base_cells[0]["value"] == pytest.approx(ret[m["metric"]], rel=1e-9), m["key"]

    # Partnership: LP + GP distributions are the levered distributions.
    part = r["partnership"]["outputs"]
    lp = sum(x for x in part["lp_cash_flows"] if x > 0)
    gp = sum(x for x in part["gp_cash_flows"] if x > 0)
    assert lp + gp == pytest.approx(sum(x for x in ret["cash_flows"][1:] if x > 0), rel=1e-6)


@pytest.mark.asyncio
async def test_io_stub_is_charged_more_than_flat_io() -> None:
    """The amortizing years cost more than the IO payment, so the IO-stub
    deal returns less than a run that charged the IO payment all hold."""
    io = await _run(2)
    ret = io["returns"]["outputs"]
    debt = io["debt"]["outputs"]
    flat_io = debt["schedule"][0]["debt_service"]
    noi = [y["noi"] for y in io["expense"]["outputs"]["years"]]
    assert ret["cash_flows"][3] < noi[2] - flat_io
