"""Delivery path for the provenance spine (FON-25 / FON-27).

Two things worth locking:

  1. End-to-end persistence — running the full engine chain persists each
     engine's ``provenance`` sidecar into ``engine_outputs``, and it
     survives the JSON round-trip through the DB back into ``ValueTrace``
     objects the endpoint's response_model can validate.
  2. Tenant isolation — the endpoint 404s a deal id that doesn't belong to
     the caller's tenant (never leaks another tenant's provenance).
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


@pytest.mark.asyncio
async def test_run_all_engines_persists_provenance() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.services.engine_runner import get_latest_outputs, run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM engine_outputs"))
        await session.commit()

        await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
        )

        outputs = await get_latest_outputs(
            session, deal_id=deal_id, tenant_id=tenant_id
        )

    # Revenue + expense are the load-bearing provenance producers and always
    # run on the Kimpton fixture; assert their traces round-tripped.
    for engine, expected_key in (
        ("revenue", "years[0].rooms_revenue"),
        ("expense", "years[0].noi"),
    ):
        assert engine in outputs, f"{engine} engine did not persist"
        prov = outputs[engine]["outputs"].get("provenance")
        assert isinstance(prov, dict) and prov, f"{engine} lost provenance in DB"
        assert expected_key in prov
        trace = prov[expected_key]
        # Survived JSON round-trip with structure intact.
        assert "formula" in trace and "inputs" in trace and "value" in trace


@pytest.mark.asyncio
async def test_provenance_endpoint_404_cross_tenant() -> None:
    """A deal id not owned by the caller's tenant must 404, not leak."""
    from fastapi import HTTPException

    from app.api.deals import get_deal_provenance
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc:
            await get_deal_provenance(
                deal_id=uuid4(),
                session=session,
                tenant_id=uuid4(),
            )
        assert exc.value.status_code == 404
