"""FON-73 Move 2 — every deal-wide reader serves ONE run-scoped snapshot.

Move 1 fixed ``GET /deals/{id}/engines``. These tests lock the three other
deal-wide readers now routed through ``get_run_scoped_outputs``:

    1. IC Memo / due-diligence context  (deals._build_real_analyst_fields)
    2. Deal provenance endpoint         (deals.get_deal_provenance)
    3. Transaction timeline             (model.get_deal_timeline)

Each must read the canonical (deal-wide, base-case) run — NOT a later
single-engine re-run or a non-base scenario run — and fall back to
latest-per-engine only when no full chain has completed. The IC Memo test
reproduces the FON-54 shape directly: a later downside scenario run (lower
IRR) must not swap into the memo's Base numbers.
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
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-fon73-readers.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

# Downside override set — moves the numbers each reader keys off:
#   * starting_occupancy 0.762 → 0.55  → lower rooms_revenue (provenance) + IRR
#   * hold_years 5 → 7                 → different disposition/exit (timeline)
_DOWNSIDE = {"hold_years": 7, "starting_occupancy": 0.55}


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate + clear the schema before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("engine_outputs", "scenarios", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _seed_deal(session, *, deal_id: str, tenant_id: str, overrides_json: str = "{}") -> None:
    """Insert a minimal deals row so tenant-ownership checks pass."""
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, field_overrides) "
            "VALUES (:id, :tenant, :name, :fo)"
        ),
        {"id": deal_id, "tenant": tenant_id, "name": "Angler Test", "fo": overrides_json},
    )


async def _record_scenario(session, *, deal_uuid: str, tenant_id: str, run_id: str) -> None:
    """Point a NON-base scenario at ``run_id`` (as run_scenario does)."""
    sid = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO scenarios (id, deal_id, tenant_id, name, "
            "is_base, overrides, last_run_id) VALUES "
            "(:id, :deal, :tenant, :name, :is_base, :ov, :run)"
        ),
        {
            "id": sid,
            "deal": deal_uuid,
            "tenant": tenant_id,
            "name": f"downside-{sid[:8]}",
            "is_base": False,
            "ov": "[]",
            "run": run_id,
        },
    )


# ─────────────────────────── 1. IC Memo (FON-54) ──────────────────────────


@pytest.mark.asyncio
async def test_ic_memo_context_pins_base_ignoring_scenario() -> None:
    """FON-54 — the IC Memo's engine_results equal the Base run, not a
    later non-base scenario run.

    Reproduces the IC-Memo-vs-Scenario mismatch: a downside scenario run
    (lower IRR, longer hold) written AFTER the base run must NOT swap into
    the memo's Base numbers.
    """
    from app.api.deals import _build_real_analyst_fields
    from app.database import get_session_factory
    from app.services.engine_runner import _coerce_uuid, run_all_engines

    deal_uuid = str(uuid4())
    tenant_id = str(uuid4())
    base_run = str(uuid4())
    scen_run = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_uuid, tenant_id=tenant_id)

        base = await run_all_engines(
            session, deal_id=deal_uuid, tenant_id=tenant_id, run_id=base_run
        )
        base_irr = base["returns"]["outputs"]["levered_irr"]

        # LATER non-base scenario run — downside, different IRR + hold.
        scen = await run_all_engines(
            session, deal_id=deal_uuid, tenant_id=tenant_id,
            run_id=scen_run, overrides=_DOWNSIDE,
        )
        scen_irr = scen["returns"]["outputs"]["levered_irr"]
        assert abs(base_irr - scen_irr) > 1e-6, "downside didn't move IRR"

        await _record_scenario(
            session, deal_uuid=str(_coerce_uuid(deal_uuid)),
            tenant_id=tenant_id, run_id=scen_run,
        )
        await session.commit()

        fields = await _build_real_analyst_fields(
            session, deal_id=deal_uuid, tenant_id=tenant_id
        )

    memo = fields["engine_results"]["returns"]
    assert memo["levered_irr"] == base_irr, (
        f"IC Memo read scenario IRR {memo['levered_irr']} not Base {base_irr}"
    )
    assert memo["levered_irr"] != scen_irr
    assert memo["hold_years"] == 5, "IC Memo hold_years must be Base (5), not 7"


@pytest.mark.asyncio
async def test_ic_memo_context_falls_back_without_full_chain() -> None:
    """FON-73 — with no full chain, the IC Memo still surfaces the partial
    latest outputs (fallback) rather than an empty engine_results."""
    from app.api.deals import _build_real_analyst_fields
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    deal_uuid = str(uuid4())
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=deal_uuid, tenant_id=tenant_id)
        # revenue only — no full chain, canonical is None.
        await run_single_engine(
            session, deal_id=deal_uuid, tenant_id=tenant_id,
            engine_name="revenue", run_id=str(uuid4()),
        )
        fields = await _build_real_analyst_fields(
            session, deal_id=deal_uuid, tenant_id=tenant_id
        )

    assert "revenue" in fields["engine_results"]


# ─────────────────────────── 2. Provenance endpoint ───────────────────────


@pytest.mark.asyncio
async def test_provenance_endpoint_reads_canonical_run() -> None:
    """FON-73/54 — per-value provenance reflects the Base run, not a later
    non-base scenario run (whose downside occupancy changes rooms_revenue)."""
    from app.api.deals import get_deal_provenance
    from app.database import get_session_factory
    from app.services.engine_runner import _coerce_uuid, get_run_status, run_all_engines

    deal_uuid = uuid4()
    tenant_id = uuid4()
    base_run = str(uuid4())
    scen_run = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(session, deal_id=str(deal_uuid), tenant_id=str(tenant_id))
        await run_all_engines(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id), run_id=base_run
        )
        await run_all_engines(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            run_id=scen_run, overrides=_DOWNSIDE,
        )
        await _record_scenario(
            session, deal_uuid=str(_coerce_uuid(str(deal_uuid))),
            tenant_id=str(tenant_id), run_id=scen_run,
        )
        await session.commit()

        # The two runs' revenue provenance values must actually differ.
        def _rooms_rev(rows: list[dict]) -> float:
            rev = next(r for r in rows if r["engine"] == "revenue")
            return rev["outputs"]["provenance"]["years[0].rooms_revenue"]["value"]

        base_val = _rooms_rev(
            await get_run_status(
                session, deal_id=str(deal_uuid), run_id=base_run, tenant_id=str(tenant_id)
            )
        )
        scen_val = _rooms_rev(
            await get_run_status(
                session, deal_id=str(deal_uuid), run_id=scen_run, tenant_id=str(tenant_id)
            )
        )
        assert abs(base_val - scen_val) > 1.0, "downside didn't move rooms_revenue"

        resp = await get_deal_provenance(
            deal_id=deal_uuid, session=session, tenant_id=tenant_id
        )

    trace = resp.engines["revenue"]["years[0].rooms_revenue"]
    got = trace.value if hasattr(trace, "value") else trace["value"]
    assert got == base_val, f"provenance read scenario {got} not Base {base_val}"
    assert got != scen_val


# ─────────────────────────── 3. Transaction timeline ──────────────────────


@pytest.mark.asyncio
async def test_timeline_reads_canonical_run() -> None:
    """FON-73 — the dated timeline is derived from the Base run's hold, not
    a later non-base scenario run's longer hold."""
    from app.api.model import get_deal_timeline
    from app.database import get_session_factory
    from app.services.engine_runner import _coerce_uuid, run_all_engines

    deal_uuid = uuid4()
    tenant_id = uuid4()
    base_run = str(uuid4())
    scen_run = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        # Close date makes the derived exit date concrete.
        await _seed_deal(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            overrides_json='{"acquisition_close_date": "2026-01-01"}',
        )
        await run_all_engines(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id), run_id=base_run
        )  # Base hold_years=5 → exit 2031
        await run_all_engines(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            run_id=scen_run, overrides=_DOWNSIDE,
        )  # scenario hold_years=7 → exit 2033
        await _record_scenario(
            session, deal_uuid=str(_coerce_uuid(str(deal_uuid))),
            tenant_id=str(tenant_id), run_id=scen_run,
        )
        await session.commit()

        resp = await get_deal_timeline(
            deal_id=str(deal_uuid), session=session, tenant_id=tenant_id
        )

    # 2026-01-01 + 5y hold = 2031 disposition (Base), NOT 2033 (scenario).
    assert resp.exit_date is not None
    assert resp.exit_date.startswith("2031"), (
        f"timeline exit {resp.exit_date} — not derived from Base hold_years=5"
    )


@pytest.mark.asyncio
async def test_timeline_falls_back_without_full_chain() -> None:
    """FON-73 — timeline still derives from the latest returns row when no
    full chain exists (fallback), rather than going empty."""
    from app.api.model import get_deal_timeline
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    deal_uuid = uuid4()
    tenant_id = uuid4()

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            overrides_json='{"acquisition_close_date": "2026-01-01"}',
        )
        # Single-engine returns run (walks deps) — no full chain, canonical None.
        await run_single_engine(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            engine_name="returns", run_id=str(uuid4()),
        )
        resp = await get_deal_timeline(
            deal_id=str(deal_uuid), session=session, tenant_id=tenant_id
        )

    assert resp.exit_date is not None
    assert resp.exit_date.startswith("2031")
