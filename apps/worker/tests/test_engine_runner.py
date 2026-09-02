"""Tests for the engine orchestration service.

The Run Model button in the web app fans out to ``run_all_engines`` /
``run_single_engine``; these tests pin the contract:

* full chain on the Kimpton fixture completes with NOI/IRR in tolerance
* a single-engine call (returns) walks its dependencies and persists
* a synthetic failure in one engine doesn't sink independent engines
* every successful run lands one row in ``engine_outputs``
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-engine-runner.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema before each test."""
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


@pytest.mark.asyncio
async def test_run_all_engines_kimpton() -> None:
    """Full chain on the Kimpton fixture — every engine completes and
    headline numbers land within tolerance of the seeded UI."""
    from app.database import get_session_factory
    from app.services.engine_runner import ENGINE_NAMES, run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    assert set(results) == set(ENGINE_NAMES)
    for name, payload in results.items():
        assert payload["status"] == "complete", (
            f"engine {name} did not complete: {payload}"
        )
        assert payload["runtime_ms"] >= 0

    # Y1 NOI from the expense engine should be in the right neighbourhood.
    y1_noi = results["expense"]["outputs"]["years"][0]["noi"]
    assert 3_500_000 <= y1_noi <= 6_500_000, f"Y1 NOI out of band: {y1_noi}"

    # Levered IRR from the returns engine on Kimpton lands ~20-30%.
    irr = results["returns"]["outputs"]["levered_irr"]
    assert 0.10 <= irr <= 0.40, f"levered IRR out of band: {irr}"

    # Equity multiple sanity.
    em = results["returns"]["outputs"]["equity_multiple"]
    assert 1.5 <= em <= 3.5, f"equity multiple out of band: {em}"

    # Sensitivity grid is 5x5.
    cells = results["sensitivity"]["outputs"]["cells"]
    assert len(cells) == 25

    # Partnership engine emits both GP/LP IRR.
    lp_irr = results["partnership"]["outputs"]["lp"]["irr"]
    gp_irr = results["partnership"]["outputs"]["gp"]["irr"]
    assert lp_irr > 0
    assert gp_irr > 0


@pytest.mark.asyncio
async def test_run_single_engine_returns() -> None:
    """``run_single_engine('returns', ...)`` walks its deps and lands
    a complete output row."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        result = await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name="returns",
        )

    assert result["status"] == "complete"
    out = result["outputs"]
    for key in (
        "levered_irr", "unlevered_irr", "equity_multiple",
        "year_one_coc", "gross_sale_price", "selling_costs",
        "net_proceeds", "hold_years", "cash_flows",
    ):
        assert key in out, f"missing key {key!r} in returns output"
    assert out["hold_years"] == 5
    assert "IRR" in result["summary"]


@pytest.mark.asyncio
async def test_engine_failure_doesnt_block_independent_engines() -> None:
    """A bad ``ltv`` override blows up ``capital`` (which validates >=0
    <=1). Independent engines (revenue, fb, expense) should still run.

    revenue/fb/expense don't depend on capital — they should land
    'complete'. debt/returns/sensitivity/partnership all depend on
    capital (directly or transitively) and should land 'failed' with a
    skipped-upstream error.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
            overrides={"ltv": 1.5},  # invalid: ltv must be <= 1.0
        )

    assert results["capital"]["status"] == "failed"
    # Independent engines unaffected.
    assert results["revenue"]["status"] == "complete"
    assert results["fb"]["status"] == "complete"
    assert results["expense"]["status"] == "complete"
    # Downstream of capital — every one should be skipped/failed.
    for downstream in ("debt", "returns", "sensitivity", "partnership"):
        assert results[downstream]["status"] == "failed", (
            f"expected {downstream} to be failed when capital fails, "
            f"got {results[downstream]['status']}"
        )


@pytest.mark.asyncio
async def test_run_persists_to_db() -> None:
    """run_all → engine_outputs has one row per engine (9 incl. cash_flow),
    all 'complete', for the run."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        _coerce_uuid,
        get_run_status,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

        rows = await get_run_status(
            session, deal_id=deal_id, run_id=run_id, tenant_id=tenant_id
        )

        # Direct count to confirm one row per engine, no dupes.
        # Producer coerces deal_id via _coerce_uuid before persisting,
        # so the raw query parameter must be coerced the same way.
        deal_id_db = str(_coerce_uuid(deal_id))
        count_row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n FROM engine_outputs "
                    "WHERE deal_id = :deal AND run_id = :run"
                ),
                {"deal": deal_id_db, "run": run_id},
            )
        ).first()
        assert count_row is not None
        assert int(count_row._mapping["n"]) == len(ENGINE_NAMES)

    assert len(rows) == len(ENGINE_NAMES)
    seen_engines = {r["engine"] for r in rows}
    assert seen_engines == set(ENGINE_NAMES)
    for r in rows:
        assert r["status"] == "complete", f"row not complete: {r}"
        assert r["outputs"] is not None
        assert r["completed_at"] is not None


@pytest.mark.asyncio
async def test_get_latest_outputs_returns_per_engine_map() -> None:
    """``GET /deals/{id}/engines`` shape — one entry per engine name."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_latest_outputs,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        outputs = await get_latest_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    assert set(outputs) == set(ENGINE_NAMES)
    for name, row in outputs.items():
        assert row["status"] == "complete"
        assert row["engine"] == name


@pytest.mark.asyncio
async def test_canonical_run_survives_single_engine_rerun() -> None:
    """FON-73 — the deal-wide read stays pinned to ONE run.

    A lone single-engine re-run stamps a fresh ``run_id`` on its own row
    (and its dependency rows), so ``get_latest_outputs`` — the old
    latest-row-per-engine read — mixes run_ids (returns from run B,
    sensitivity still from run A). That silent de-sync is the FON-54/69
    root cause. ``get_canonical_run_id`` must ignore the partial re-run
    and keep the full chain, and the run-scoped read must return one
    internally-consistent run.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_canonical_run_id,
        get_latest_outputs,
        get_run_status,
        run_all_engines,
        run_single_engine,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_a = str(uuid4())
    run_b = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        # Full chain — the canonical base-case run (all 8 engines).
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_a
        )
        # Later single-engine re-run of returns. It walks its deps, so it
        # persists revenue/fb/expense/capital/debt/returns under run_b —
        # 6 engines, NOT the full 8 (no sensitivity/partnership).
        await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name="returns",
            run_id=run_b,
        )

        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical == run_a, "partial single-engine re-run hijacked canonical"

        # OLD behavior drifts: latest-per-engine straddles two runs.
        latest = await get_latest_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert latest["returns"]["run_id"] == run_b
        assert latest["sensitivity"]["run_id"] == run_a  # de-synced mix

        # NEW behavior: run-scoped read is one internally-consistent run.
        run_rows = await get_run_status(
            session, deal_id=deal_id, run_id=canonical, tenant_id=tenant_id
        )
        scoped = {r["engine"]: r for r in run_rows if r["engine"] in ENGINE_NAMES}
        assert set(scoped) == set(ENGINE_NAMES)
        assert {r["run_id"] for r in scoped.values()} == {run_a}


@pytest.mark.asyncio
async def test_canonical_run_none_without_full_chain() -> None:
    """FON-73 — no complete chain → None → caller falls back to latest."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        get_canonical_run_id,
        get_latest_outputs,
        run_single_engine,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        # Only revenue ever ran (no deps) — never a full chain.
        await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name="revenue",
            run_id=str(uuid4()),
        )
        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical is None

        # Fallback still surfaces the partial output (no 404 / empty page).
        latest = await get_latest_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert "revenue" in latest


@pytest.mark.asyncio
async def test_canonical_run_excludes_non_base_scenario() -> None:
    """FON-73 — a non-base scenario's full run must not become canonical.

    Scenario runs share ``run_all_engines`` and write all-8-engine rows to
    ``engine_outputs`` with a fresh run_id, but carry scenario overrides.
    Even when a non-base scenario run is the MOST recent full chain, the
    deal-wide read must skip it (via ``scenarios.last_run_id``) and keep
    the base run.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _coerce_uuid,
        get_canonical_run_id,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    base_run = str(uuid4())
    scenario_run = str(uuid4())
    scenario_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        # Base-case full chain first ...
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=base_run
        )
        # ... then a LATER non-base scenario full chain (would otherwise win
        # on recency).
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=scenario_run
        )
        # Record the scenario row pointing at its run (as run_scenario does).
        await session.execute(
            text(
                "INSERT INTO scenarios (id, deal_id, tenant_id, name, "
                "is_base, overrides, last_run_id) VALUES "
                "(:id, :deal, :tenant, :name, :is_base, :ov, :run)"
            ),
            {
                "id": scenario_id,
                "deal": str(_coerce_uuid(deal_id)),
                "tenant": tenant_id,
                "name": f"downside-{scenario_id[:8]}",
                "is_base": False,
                "ov": "[]",
                "run": scenario_run,
            },
        )
        await session.commit()

        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical == base_run, (
            "non-base scenario run must be excluded from canonical"
        )


@pytest.mark.asyncio
async def test_run_scoped_outputs_canonical_and_fallback() -> None:
    """FON-73 — ``get_run_scoped_outputs`` is the shared deal-wide read.

    Every deal-wide surface (the /engines list, IC Memo context,
    provenance, timeline) routes through it. It must return the canonical
    run's snapshot — all one run_id — after a desyncing single-engine
    re-run, and fall back to latest-per-engine only when no full chain
    exists.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_run_scoped_outputs,
        run_all_engines,
        run_single_engine,
    )

    deal_id = "kimpton-angler-2026"
    factory = get_session_factory()

    # Canonical path — full chain, then a desyncing single-engine re-run.
    tenant_a = str(uuid4())
    run_a = str(uuid4())
    async with factory() as session:
        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_a, run_id=run_a
        )
        await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_a,
            engine_name="returns",
            run_id=str(uuid4()),
        )
        scoped = await get_run_scoped_outputs(
            session, deal_id=deal_id, tenant_id=tenant_a
        )
    assert set(scoped) == set(ENGINE_NAMES)
    assert {v["run_id"] for v in scoped.values()} == {run_a}

    # Fallback path — only a single engine ever ran (no full chain).
    tenant_b = str(uuid4())
    async with factory() as session:
        await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_b,
            engine_name="revenue",
            run_id=str(uuid4()),
        )
        scoped = await get_run_scoped_outputs(
            session, deal_id=deal_id, tenant_id=tenant_b
        )
    assert "revenue" in scoped
    assert set(scoped) <= set(ENGINE_NAMES)


async def _insert_engine_row(
    session,
    *,
    deal_id: str,
    tenant_id: str,
    run_id: str,
    engine_name: str,
    started_at: str,
) -> None:
    """Insert a minimal complete engine_outputs row (canonical-gating tests)."""
    from uuid import uuid4 as _uuid4

    from app.services.engine_runner import _coerce_uuid

    await session.execute(
        text(
            """
            INSERT INTO engine_outputs (
                id, deal_id, tenant_id, run_id, engine_name,
                status, inputs, outputs, error,
                started_at, completed_at, runtime_ms
            ) VALUES (
                :id, :deal, :tenant, :run, :engine,
                'complete', NULL, '{}', NULL,
                :started_at, :started_at, 1
            )
            """
        ),
        {
            "id": str(_uuid4()),
            "deal": str(_coerce_uuid(deal_id)),
            "tenant": tenant_id,
            "run": run_id,
            "engine": engine_name,
            "started_at": started_at,
        },
    )


@pytest.mark.asyncio
async def test_canonical_gates_on_base_engines_not_cashflow() -> None:
    """Move 2 — canonical gating counts the 8 BASE engines, ignoring cash_flow.

    * An existing 8-base-engine run (no cash_flow row — the pre-Move-2 shape)
      stays canonical with NO backfill.
    * A later LONE cash_flow re-run (1 row, not a base engine) can't hijack
      canonical — it has zero base engines (0 < 8).
    """
    from app.database import get_session_factory
    from app.services.engine_runner import CANONICAL_ENGINES, get_canonical_run_id

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_base = str(uuid4())
    run_cf = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        # A full 8-base-engine run, no cash_flow row (legacy shape).
        for i, name in enumerate(CANONICAL_ENGINES):
            await _insert_engine_row(
                session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_base,
                engine_name=name, started_at=f"2026-01-01T00:00:{i:02d}",
            )
        await session.commit()

        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical == run_base, "8-base-engine run must stay canonical"

        # A LATER lone cash_flow re-run — newest started_at, single row.
        await _insert_engine_row(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_cf,
            engine_name="cash_flow", started_at="2026-01-01T01:00:00",
        )
        await session.commit()

        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical == run_base, (
            "a lone cash_flow re-run must not become canonical"
        )


@pytest.mark.asyncio
async def test_nine_engine_run_is_canonical() -> None:
    """Move 2 — a real full chain (9 engines incl. cash_flow) is canonical, and
    the cash_flow row is present, complete, and served by the run-scoped read."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_canonical_run_id,
        get_run_scoped_outputs,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )
        assert "cash_flow" in results
        assert results["cash_flow"]["status"] == "complete"

        canonical = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert canonical == run_id, "9-engine full chain must be canonical"

        scoped = await get_run_scoped_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        assert set(scoped) == set(ENGINE_NAMES)
        assert "cash_flow" in scoped
        assert {v["run_id"] for v in scoped.values()} == {run_id}


@pytest.mark.asyncio
async def test_unknown_engine_raises() -> None:
    """``run_single_engine`` rejects unknown engine names cleanly."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(ValueError, match="unknown engine"):
            await run_single_engine(
                session,
                deal_id="kimpton-angler-2026",
                tenant_id=str(uuid4()),
                engine_name="nope",
            )
