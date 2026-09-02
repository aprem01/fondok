"""Cash Flow Statement engine (Move 2) — the composed view must tie out.

The engine is a *view* over the canonical engine outputs, never a second
source of truth. These tests lock that contract on the Kimpton fixture:

  * the composed unlevered / levered component rows sum back to the returns
    engine's ``cash_flows_unlevered`` / ``cash_flows`` to the cent;
  * the served bottom-line series ARE those canonical arrays;
  * the distribution rows are the partnership waterfall's GP / LP flows
    (not the old hardcoded 10%-pref proxy);
  * every emitted row carries a valid provenance ``state``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-cash-flow-engine.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_CENT = 0.01
_VALID_STATES = {
    "document_sourced",
    "linked",
    "assumption",
    "calculated",
    "awaiting_data",
    "needs_review",
}


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
        except Exception:  # noqa: BLE001
            pass
    yield


def _composed(lines: list[dict], n: int) -> list[float]:
    """Sum the ``linked`` component rows per period (the ``calc`` row is the
    bottom line the components must reconstruct)."""
    totals = [0.0] * n
    for line in lines:
        if line["kind"] != "linked":
            continue
        for i, v in enumerate(line["values"]):
            if v is not None and i < n:
                totals[i] += float(v)
    return totals


async def _run_kimpton():
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
    return results


@pytest.mark.asyncio
async def test_cash_flow_reconciles_to_returns_on_kimpton() -> None:
    results = await _run_kimpton()
    assert results["cash_flow"]["status"] == "complete", results["cash_flow"]
    cf = results["cash_flow"]["outputs"]
    ret = results["returns"]["outputs"]

    unlev_canon = [float(x) for x in ret["cash_flows_unlevered"]]
    lev_canon = [float(x) for x in ret["cash_flows"]]
    n = len(lev_canon)

    # Bottom-line series ARE the canonical returns arrays.
    assert cf["unlevered_cash_flow"] == pytest.approx(unlev_canon, abs=_CENT)
    assert cf["levered_cash_flow"] == pytest.approx(lev_canon, abs=_CENT)

    # Composed component rows sum back to the canonical arrays — per period …
    composed_unlev = _composed(cf["unlevered"], n)
    composed_lev = _composed(cf["levered"], n)
    for i in range(n):
        assert abs(composed_unlev[i] - unlev_canon[i]) <= _CENT, (
            f"unlevered period {i}: {composed_unlev[i]} vs {unlev_canon[i]}"
        )
        assert abs(composed_lev[i] - lev_canon[i]) <= _CENT, (
            f"levered period {i}: {composed_lev[i]} vs {lev_canon[i]}"
        )
    # … and in aggregate.
    assert sum(composed_unlev) == pytest.approx(sum(unlev_canon), abs=_CENT)
    assert sum(composed_lev) == pytest.approx(sum(lev_canon), abs=_CENT)

    # The bottom-line rows carried on the statements match too.
    unlev_bottom = next(
        r for r in cf["unlevered"] if r["label"] == "Unlevered Cash Flow"
    )
    lev_bottom = next(
        r for r in cf["levered"] if r["label"] == "Net Cash Flow to Equity"
    )
    assert unlev_bottom["values"] == pytest.approx(unlev_canon, abs=_CENT)
    assert lev_bottom["values"] == pytest.approx(lev_canon, abs=_CENT)


@pytest.mark.asyncio
async def test_cash_flow_distributions_equal_partnership_flows() -> None:
    results = await _run_kimpton()
    cf = results["cash_flow"]["outputs"]
    part = results["partnership"]["outputs"]

    lp = [float(x) for x in part["lp_cash_flows"]]
    gp = [float(x) for x in part["gp_cash_flows"]]

    lp_row = next(r for r in cf["distributions"] if r["label"] == "LP Distributions")
    gp_row = next(r for r in cf["distributions"] if r["label"] == "GP Distributions")

    assert [float(v) for v in lp_row["values"]] == pytest.approx(lp, abs=_CENT)
    assert [float(v) for v in gp_row["values"]] == pytest.approx(gp, abs=_CENT)

    total_row = next(
        r for r in cf["distributions"] if r["label"] == "Total Distributions"
    )
    expected_total = [lp[i] + gp[i] for i in range(len(lp))]
    assert [float(v) for v in total_row["values"]] == pytest.approx(
        expected_total, abs=_CENT
    )


@pytest.mark.asyncio
async def test_cash_flow_every_row_has_valid_state() -> None:
    results = await _run_kimpton()
    cf = results["cash_flow"]["outputs"]
    prov = cf["provenance"]
    assert prov, "cash_flow must emit a provenance sidecar"

    # Every emitted line has a provenance trace with a valid state, and the
    # state follows the row kind (linked → linked, calc → calculated).
    for section, key in (("unlevered", "unlevered"), ("levered", "levered"),
                         ("distributions", "distributions")):
        for line in cf[section]:
            match = [
                t for k, t in prov.items()
                if k.startswith(f"{section}.") and t["value"] is not None
            ]
            assert match, f"no provenance for {section}"
        # Explicitly check kind→state on each traced row.
    for key, trace in prov.items():
        assert trace["state"] in _VALID_STATES, (key, trace["state"])
        # linked rows carry a cross-engine input; calc rows carry a formula.
        if trace["state"] == "linked":
            assert trace["inputs"], key
        elif trace["state"] == "calculated":
            assert trace["formula"], key
