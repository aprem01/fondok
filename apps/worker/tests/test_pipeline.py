"""Pipeline view (Wave 3 W3.5) — multi-deal table + summary KPIs.

Drives the ``GET /deals/pipeline`` endpoint against a real SQLite DB,
seeding deals + engine_outputs rows so the join + window-function
("latest engine row per deal") logic is exercised end-to-end. We
verify:

* Empty pipeline returns an empty list + zeroed summary.
* Tenant scoping (cross-tenant deals stay invisible).
* Latest-run-per-deal join (older engine rows are ignored).
* Each sort token + filter combinator.
* Pagination via ``limit`` / ``offset``.
* Summary p25 / median / p75 IRR + ``target_irr_met`` semantics.

Mirrors the test_deals_crud.py pattern: a per-test SQLite reset via
the ``_reset_db`` autouse fixture so each test gets a clean state.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import. Same pattern as
# test_deals_crud.py — without this the cached Settings / engine pick
# up the wrong DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-pipeline.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Wipe + re-migrate before each test so cached pipeline rows from
    a prior test never leak into the next assertion.
    """
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.pipeline import invalidate as pipeline_invalidate

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "audit_log",
            "engine_outputs",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    # Clear the 60s pipeline cache too — tests run faster than the TTL
    # so the previous snapshot would otherwise be served.
    pipeline_invalidate()
    yield


# ─────────────────────────── helpers ───────────────────────────


_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def _insert_deal(
    session,
    *,
    name: str,
    tenant: str = _DEFAULT_TENANT,
    keys: int | None = 200,
    purchase_price: float | None = 30_000_000.0,
    state: str = "VALIDATING",
    target_irr: float | None = None,
    deal_stage: str | None = "Active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    status: str = "Active",
) -> str:
    """Insert a deal row directly so a test can pin every column the
    pipeline aggregator reads — avoids depending on the create-deal
    audit-log side-effects.
    """
    from sqlalchemy import text

    deal_id = str(uuid4())
    now = (created_at or datetime.now(UTC)).isoformat()
    upd = (updated_at or created_at or datetime.now(UTC)).isoformat()
    await session.execute(
        text(
            """
            INSERT INTO deals (
                id, tenant_id, name, city, keys, service, status,
                deal_stage, risk, ai_confidence, return_profile, brand,
                positioning, purchase_price, sourcing_channel,
                target_irr, state, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, NULL, :keys, NULL, :status,
                :deal_stage, NULL, 0.0, NULL, NULL,
                NULL, :pp, NULL,
                :target_irr, :state, :created_at, :updated_at
            )
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant,
            "name": name,
            "keys": keys,
            "status": status,
            "deal_stage": deal_stage,
            "pp": purchase_price,
            "target_irr": target_irr,
            "state": state,
            "created_at": now,
            "updated_at": upd,
        },
    )
    await session.commit()
    return deal_id


async def _insert_engine_output(
    session,
    *,
    deal_id: str,
    engine_name: str,
    outputs: dict,
    inputs: dict | None = None,
    tenant: str = _DEFAULT_TENANT,
    started_at: datetime | None = None,
    status: str = "complete",
) -> str:
    """Insert an engine_outputs row. ``inputs`` defaults to empty {}."""
    from sqlalchemy import text

    row_id = str(uuid4())
    ts = (started_at or datetime.now(UTC)).isoformat()
    await session.execute(
        text(
            """
            INSERT INTO engine_outputs (
                id, deal_id, tenant_id, run_id, engine_name, status,
                inputs, outputs, error, started_at, completed_at,
                runtime_ms
            ) VALUES (
                :id, :deal, :tenant, :run, :engine, :status,
                :inputs, :outputs, NULL, :ts, :ts, 10
            )
            """
        ),
        {
            "id": row_id,
            "deal": deal_id,
            "tenant": tenant,
            "run": str(uuid4()),
            "engine": engine_name,
            "status": status,
            "inputs": json.dumps(inputs or {}),
            "outputs": json.dumps(outputs),
            "ts": ts,
        },
    )
    await session.commit()
    return row_id


def _returns_outputs(
    *, irr: float, em: float = 2.0, hold_years: int = 5
) -> dict:
    """Minimal ReturnsEngineOutput-shaped blob."""
    return {
        "deal_id": str(uuid4()),
        "levered_irr": irr,
        "unlevered_irr": irr - 0.05,
        "equity_multiple": em,
        "year_one_coc": 0.05,
        "avg_coc": 0.06,
        "gross_sale_price": 60_000_000.0,
        "selling_costs": 1_200_000.0,
        "net_proceeds": 25_000_000.0,
        "hold_years": hold_years,
    }


def _returns_inputs(*, exit_cap_rate: float = 0.075) -> dict:
    return {
        "assumptions": {
            "exit_cap_rate": exit_cap_rate,
            "revpar_growth": 0.03,
            "expense_growth": 0.03,
        }
    }


def _expense_outputs(*, noi_y1: float, noi_stab: float) -> dict:
    return {
        "deal_id": str(uuid4()),
        "noi_cagr": 0.03,
        "sourced_from_t12": [],
        "years": [
            {"year": 1, "noi": noi_y1, "noi_institutional": noi_y1},
            {"year": 2, "noi": (noi_y1 + noi_stab) / 2,
             "noi_institutional": (noi_y1 + noi_stab) / 2},
            {"year": 3, "noi": noi_stab, "noi_institutional": noi_stab},
        ],
    }


def _debt_outputs(*, dscr_y1: float) -> dict:
    return {
        "deal_id": str(uuid4()),
        "annual_debt_service": 2_400_000.0,
        "avg_dscr": dscr_y1 + 0.1,
        "schedule": [
            {"year": 1, "interest": 1_900_000.0, "principal": 500_000.0,
             "debt_service": 2_400_000.0, "ending_balance": 24_500_000.0,
             "dscr": dscr_y1},
            {"year": 2, "interest": 1_870_000.0, "principal": 530_000.0,
             "debt_service": 2_400_000.0, "ending_balance": 23_970_000.0,
             "dscr": dscr_y1 + 0.05},
        ],
    }


def _capital_inputs(*, renovation_budget: float = 0.0) -> dict:
    return {
        "renovation_budget": renovation_budget,
        "purchase_price": 30_000_000.0,
        "keys": 200,
    }


def _capital_outputs(*, price_per_key: float = 150_000.0) -> dict:
    return {
        "deal_id": str(uuid4()),
        "total_capital": 30_600_000.0,
        "price_per_key": price_per_key,
        "sources": [],
        "uses": [],
        "debt_amount": 20_000_000.0,
        "equity_amount": 10_600_000.0,
        "ltc": 0.66,
    }


async def _seed_deal_with_engines(
    session,
    *,
    name: str,
    irr: float,
    em: float = 2.0,
    noi_y1: float = 4_500_000.0,
    noi_stab: float = 5_500_000.0,
    dscr_y1: float = 1.45,
    price_per_key: float = 150_000.0,
    exit_cap: float = 0.075,
    keys: int = 200,
    purchase_price: float = 30_000_000.0,
    renovation_budget: float = 0.0,
    state: str = "VALIDATING",
    target_irr: float | None = None,
    deal_stage: str = "Active",
    status: str = "Active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    tenant: str = _DEFAULT_TENANT,
) -> str:
    """Seed a deal + the four engines (returns / debt / expense / capital)
    so the pipeline aggregator finds every metric populated.
    """
    deal_id = await _insert_deal(
        session,
        name=name,
        tenant=tenant,
        keys=keys,
        purchase_price=purchase_price,
        state=state,
        target_irr=target_irr,
        deal_stage=deal_stage,
        created_at=created_at,
        updated_at=updated_at,
        status=status,
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="returns",
        outputs=_returns_outputs(irr=irr, em=em),
        inputs=_returns_inputs(exit_cap_rate=exit_cap),
        tenant=tenant,
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="debt",
        outputs=_debt_outputs(dscr_y1=dscr_y1),
        tenant=tenant,
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="expense",
        outputs=_expense_outputs(noi_y1=noi_y1, noi_stab=noi_stab),
        tenant=tenant,
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="capital",
        outputs=_capital_outputs(price_per_key=price_per_key),
        inputs=_capital_inputs(renovation_budget=renovation_budget),
        tenant=tenant,
    )
    return deal_id


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-Id": _DEFAULT_TENANT},
    )


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_empty_pipeline_returns_zero_deals() -> None:
    """No deals in the tenant → empty list + zeroed summary KPIs."""
    async with _client() as client:
        r = await client.get("/deals/pipeline")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deals"] == []
        assert body["total_count"] == 0
        s = body["summary"]
        assert s["deal_count"] == 0
        assert s["median_irr"] is None
        assert s["p25_irr"] is None
        assert s["p75_irr"] is None
        assert s["deals_meeting_target_irr"] == 0
        assert s["deals_by_state"] == {}


@pytest.mark.asyncio
async def test_pipeline_excludes_other_tenants() -> None:
    """Deals on a foreign tenant must never leak into the response."""
    from app.database import get_session_factory

    other_tenant = "00000000-0000-0000-0000-0000000000ff"
    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(
            session, name="Mine", irr=0.18, tenant=_DEFAULT_TENANT
        )
        await _seed_deal_with_engines(
            session, name="Theirs", irr=0.30, tenant=other_tenant
        )

    async with _client() as client:
        r = await client.get("/deals/pipeline")
        assert r.status_code == 200, r.text
        body = r.json()
        names = [d["name"] for d in body["deals"]]
        assert names == ["Mine"]
        assert body["total_count"] == 1


@pytest.mark.asyncio
async def test_pipeline_includes_only_latest_engine_run_per_deal() -> None:
    """Two `returns` rows for one deal → the older run is ignored."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        deal_id = await _insert_deal(session, name="Two Runs")
        old = datetime.now(UTC) - timedelta(days=7)
        new = datetime.now(UTC) - timedelta(minutes=5)
        # Old run: IRR 0.10
        await _insert_engine_output(
            session,
            deal_id=deal_id,
            engine_name="returns",
            outputs=_returns_outputs(irr=0.10),
            inputs=_returns_inputs(),
            started_at=old,
        )
        # Newer run: IRR 0.22 — this is what the row should reflect.
        await _insert_engine_output(
            session,
            deal_id=deal_id,
            engine_name="returns",
            outputs=_returns_outputs(irr=0.22),
            inputs=_returns_inputs(),
            started_at=new,
        )

    async with _client() as client:
        r = await client.get("/deals/pipeline")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["deals"]) == 1
        assert body["deals"][0]["levered_irr"] == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_sort_by_irr_desc() -> None:
    """Highest IRR ranks first."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(session, name="A", irr=0.12)
        await _seed_deal_with_engines(session, name="B", irr=0.20)
        await _seed_deal_with_engines(session, name="C", irr=0.16)

    async with _client() as client:
        r = await client.get("/deals/pipeline", params={"sort": "irr_desc"})
        names = [d["name"] for d in r.json()["deals"]]
        assert names == ["B", "C", "A"]


@pytest.mark.asyncio
async def test_sort_by_per_key_asc() -> None:
    """Cheapest $/key ranks first."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(session, name="Pricey", irr=0.18,
                                      price_per_key=400_000.0)
        await _seed_deal_with_engines(session, name="Cheap", irr=0.18,
                                      price_per_key=120_000.0)
        await _seed_deal_with_engines(session, name="Mid", irr=0.18,
                                      price_per_key=250_000.0)

    async with _client() as client:
        r = await client.get(
            "/deals/pipeline", params={"sort": "per_key_asc"}
        )
        names = [d["name"] for d in r.json()["deals"]]
        assert names == ["Cheap", "Mid", "Pricey"]


@pytest.mark.asyncio
async def test_sort_by_last_activity_desc_default() -> None:
    """Default sort is last_activity_desc — most-recently-touched first."""
    from app.database import get_session_factory

    factory = get_session_factory()
    base = datetime.now(UTC)
    async with factory() as session:
        await _seed_deal_with_engines(
            session, name="Old", irr=0.18,
            created_at=base - timedelta(days=10),
            updated_at=base - timedelta(days=10),
        )
        await _seed_deal_with_engines(
            session, name="Fresh", irr=0.18,
            created_at=base - timedelta(days=1),
            updated_at=base - timedelta(minutes=10),
        )
        await _seed_deal_with_engines(
            session, name="Middle", irr=0.18,
            created_at=base - timedelta(days=5),
            updated_at=base - timedelta(days=2),
        )

    async with _client() as client:
        r = await client.get("/deals/pipeline")
        names = [d["name"] for d in r.json()["deals"]]
        assert names == ["Fresh", "Middle", "Old"]


@pytest.mark.asyncio
async def test_filter_by_state_validating() -> None:
    """state=VALIDATING drops ONBOARDING + READY deals."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(session, name="OnA", irr=0.18,
                                      state="ONBOARDING")
        await _seed_deal_with_engines(session, name="OnB", irr=0.18,
                                      state="ONBOARDING")
        await _seed_deal_with_engines(session, name="ValC", irr=0.18,
                                      state="VALIDATING")
        await _seed_deal_with_engines(session, name="RdyD", irr=0.18,
                                      state="READY")

    async with _client() as client:
        r = await client.get("/deals/pipeline",
                              params={"state": "VALIDATING"})
        body = r.json()
        names = sorted(d["name"] for d in body["deals"])
        assert names == ["ValC"]
        assert body["summary"]["deal_count"] == 1


@pytest.mark.asyncio
async def test_filter_by_min_irr() -> None:
    """min_irr=0.15 keeps only deals with IRR ≥ 15%."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(session, name="Lo", irr=0.08)
        await _seed_deal_with_engines(session, name="Hi", irr=0.21)
        await _seed_deal_with_engines(session, name="Mid", irr=0.155)
        # Deal with no IRR (no engine run): falls under the predicate
        # because "is it ≥ 15%?" is unknowable.
        await _insert_deal(session, name="Unknown")

    async with _client() as client:
        r = await client.get(
            "/deals/pipeline", params={"min_irr": "0.15"}
        )
        names = sorted(d["name"] for d in r.json()["deals"])
        assert names == ["Hi", "Mid"]


@pytest.mark.asyncio
async def test_filter_by_max_per_key() -> None:
    """max_per_key=300000 drops deals priced above $300k/key."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_engines(session, name="Modest", irr=0.18,
                                      price_per_key=200_000.0)
        await _seed_deal_with_engines(session, name="Luxury", irr=0.18,
                                      price_per_key=500_000.0)
        await _seed_deal_with_engines(session, name="Edge", irr=0.18,
                                      price_per_key=300_000.0)

    async with _client() as client:
        r = await client.get(
            "/deals/pipeline", params={"max_per_key": "300000"}
        )
        names = sorted(d["name"] for d in r.json()["deals"])
        # 300000 is inclusive — equals the threshold.
        assert names == ["Edge", "Modest"]


@pytest.mark.asyncio
async def test_pagination_limit_offset() -> None:
    """limit/offset slice the list AFTER sort/filter."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Five deals at descending IRRs so sort=irr_desc is deterministic.
        for i, irr in enumerate([0.10, 0.20, 0.15, 0.25, 0.05]):
            await _seed_deal_with_engines(
                session, name=f"D{i}", irr=irr,
            )

    async with _client() as client:
        # Page 1: first 2 highest IRR.
        r = await client.get(
            "/deals/pipeline",
            params={"sort": "irr_desc", "limit": 2, "offset": 0},
        )
        body = r.json()
        assert [d["name"] for d in body["deals"]] == ["D3", "D1"]
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert body["total_count"] == 5
        # Page 2: next 2.
        r = await client.get(
            "/deals/pipeline",
            params={"sort": "irr_desc", "limit": 2, "offset": 2},
        )
        body = r.json()
        assert [d["name"] for d in body["deals"]] == ["D2", "D0"]


@pytest.mark.asyncio
async def test_summary_median_irr_computed() -> None:
    """Summary.median_irr matches statistics.median over the IRR set."""
    from app.database import get_session_factory

    factory = get_session_factory()
    irrs = [0.10, 0.14, 0.18, 0.22, 0.30]
    async with factory() as session:
        for i, irr in enumerate(irrs):
            await _seed_deal_with_engines(session, name=f"D{i}", irr=irr)

    async with _client() as client:
        r = await client.get("/deals/pipeline")
        s = r.json()["summary"]
        assert s["median_irr"] == pytest.approx(statistics.median(irrs))
        # Median EM uses the default 2.0 across every seeded deal.
        assert s["median_em"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_summary_p25_p75_irr() -> None:
    """p25 and p75 use the linear-interpolation percentile."""
    from app.database import get_session_factory

    factory = get_session_factory()
    irrs = [0.10, 0.12, 0.16, 0.20, 0.24]
    async with factory() as session:
        for i, irr in enumerate(irrs):
            await _seed_deal_with_engines(session, name=f"D{i}", irr=irr)

    async with _client() as client:
        r = await client.get("/deals/pipeline")
        s = r.json()["summary"]
        # Sorted IRRs = [0.10, 0.12, 0.16, 0.20, 0.24] (n=5).
        # Linear-interp: p25 = at index 1.0 → 0.12; p75 = at index 3.0 → 0.20.
        assert s["p25_irr"] == pytest.approx(0.12)
        assert s["p75_irr"] == pytest.approx(0.20)
        assert s["p25_irr"] < s["median_irr"] < s["p75_irr"]


@pytest.mark.asyncio
async def test_target_irr_met_flag_when_deal_has_target() -> None:
    """target_irr_met is True when levered_irr ≥ target_irr."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Beat the target by 3 pts.
        await _seed_deal_with_engines(
            session, name="Beats", irr=0.20, target_irr=0.17,
        )
        # Miss the target by 5 pts.
        await _seed_deal_with_engines(
            session, name="Misses", irr=0.10, target_irr=0.15,
        )

    async with _client() as client:
        r = await client.get("/deals/pipeline", params={"sort": "name_asc"})
        deals = {d["name"]: d for d in r.json()["deals"]}
        assert deals["Beats"]["target_irr_met"] is True
        assert deals["Misses"]["target_irr_met"] is False
        s = r.json()["summary"]
        # Both deals have a target — both count toward
        # ``deals_with_target_irr``; only the beater counts toward met.
        assert s["deals_with_target_irr"] == 2
        assert s["deals_meeting_target_irr"] == 1


@pytest.mark.asyncio
async def test_target_irr_met_null_when_no_target() -> None:
    """A deal with no target_irr has target_irr_met=None and is skipped
    in the summary's deals_with_target_irr tally — sparse pipelines
    shouldn't inflate the "missing target" rate.
    """
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Deal with target met.
        await _seed_deal_with_engines(
            session, name="HasTarget", irr=0.22, target_irr=0.18,
        )
        # Deal with no target — flag is NULL regardless of how high IRR is.
        await _seed_deal_with_engines(
            session, name="NoTarget", irr=0.30, target_irr=None,
        )

    async with _client() as client:
        r = await client.get("/deals/pipeline", params={"sort": "name_asc"})
        deals = {d["name"]: d for d in r.json()["deals"]}
        assert deals["HasTarget"]["target_irr_met"] is True
        assert deals["NoTarget"]["target_irr_met"] is None
        # Summary: only one deal has a target.
        s = r.json()["summary"]
        assert s["deals_with_target_irr"] == 1
        assert s["deals_meeting_target_irr"] == 1
