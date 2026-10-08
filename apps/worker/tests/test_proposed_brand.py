"""FON-59 / R-048 (Sam's decision 4) — Existing Brand vs Proposed Brand.

``deals.brand`` is the EXISTING flag (analyst-typed, or filled from the OM's
``property_overview.brand`` only when empty). ``deals.proposed_brand`` is the
analyst's PROPOSED brand — optional, and never touched by a document.

Covered here:

* create / read / list / update round-trip ``proposed_brand`` beside ``brand``
  and the audit rows carry it;
* an OM extraction fills an empty ``brand`` but never writes ``proposed_brand``;
* a schema WITHOUT the column (migration not yet applied) still serves every
  route: ``proposed_brand`` reads as NULL and writes are skipped with a log
  line instead of a 500.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-proposed-brand.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _audit_payloads(deal_id: str, action: str) -> list[dict[str, Any]]:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log "
                    "WHERE resource_id = :rid AND action = :action ORDER BY rowid"
                ),
                {"rid": deal_id, "action": action},
            )
        ).all()
    return [json.loads(r._mapping["payload"]) for r in rows]


async def _column_exists() -> bool:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("PRAGMA table_info(deals)"))).all()
    return any(r[1] == "proposed_brand" for r in rows)


async def _drop_column() -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("ALTER TABLE deals DROP COLUMN proposed_brand"))
        await session.commit()


# ─────────────────────────── round-trip ───────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_the_column() -> None:
    assert await _column_exists()


@pytest.mark.asyncio
async def test_create_and_read_keep_existing_and_proposed_brand_apart() -> None:
    async with _client() as client:
        r = await client.post(
            "/deals",
            json={
                "name": "Kimpton Surfcomber",
                "brand": "Kimpton",
                "proposed_brand": "Thompson Hotels",
            },
        )
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["brand"] == "Kimpton"
        assert created["proposed_brand"] == "Thompson Hotels"
        deal_id = created["id"]

        body = (await client.get(f"/deals/{deal_id}")).json()
        assert body["brand"] == "Kimpton"
        assert body["proposed_brand"] == "Thompson Hotels"

        listed = {d["id"]: d for d in (await client.get("/deals")).json()}
        assert listed[deal_id]["proposed_brand"] == "Thompson Hotels"

    created_audit = await _audit_payloads(deal_id, "deal.created")
    assert len(created_audit) == 1
    flat = json.dumps(created_audit[0])
    assert "Thompson Hotels" in flat and "Kimpton" in flat


@pytest.mark.asyncio
async def test_proposed_brand_is_optional() -> None:
    async with _client() as client:
        r = await client.post("/deals", json={"name": "No proposal"})
        assert r.status_code == 201, r.text
        assert r.json()["proposed_brand"] is None
        assert r.json()["brand"] is None
        body = (await client.get(f"/deals/{r.json()['id']}")).json()
        assert body["proposed_brand"] is None


@pytest.mark.asyncio
async def test_patch_sets_and_clears_proposed_brand_and_audits_it() -> None:
    async with _client() as client:
        deal_id = (
            await client.post("/deals", json={"name": "Patch me", "brand": "Kimpton"})
        ).json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}", json={"proposed_brand": "Thompson Hotels"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["proposed_brand"] == "Thompson Hotels"
        assert r.json()["brand"] == "Kimpton"

        r = await client.patch(f"/deals/{deal_id}", json={"proposed_brand": None})
        assert r.status_code == 200, r.text
        assert r.json()["proposed_brand"] is None
        assert r.json()["brand"] == "Kimpton"

    updates = await _audit_payloads(deal_id, "deal.updated")
    changes = [json.dumps(u) for u in updates]
    assert any('"proposed_brand": "Thompson Hotels"' in c for c in changes)
    assert any('"proposed_brand": null' in c for c in changes)


# ─────────────────────── OM sync never touches it ───────────────────────


@pytest.mark.asyncio
async def test_om_extraction_never_touches_proposed_brand() -> None:
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory

    async with _client() as client:
        created = (
            await client.post(
                "/deals", json={"name": "OM sync", "proposed_brand": "Thompson Hotels"}
            )
        ).json()
    deal_id, tenant_id = created["id"], created["tenant_id"]

    factory = get_session_factory()
    async with factory() as session:
        await _sync_deal_metadata_from_extraction(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            fields=[
                {"field_name": "property_overview.brand", "value": "Kimpton"},
                {"field_name": "property_overview.proposed_brand", "value": "Kimpton"},
            ],
            doc_type="OM",
        )

    async with _client() as client:
        body = (await client.get(f"/deals/{deal_id}")).json()
    # The OM filled the empty EXISTING brand…
    assert body["brand"] == "Kimpton"
    # …and left the analyst's PROPOSED brand alone.
    assert body["proposed_brand"] == "Thompson Hotels"


# ─────────────────────── schema without the column ───────────────────────


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_column(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _drop_column()
    assert not await _column_exists()
    try:
        with caplog.at_level("WARNING", logger="app.api.deals"):
            async with _client() as client:
                r = await client.post(
                    "/deals",
                    json={
                        "name": "Old schema",
                        "brand": "Kimpton",
                        "proposed_brand": "Thompson Hotels",
                    },
                )
                assert r.status_code == 201, r.text
                assert r.json()["brand"] == "Kimpton"
                assert r.json()["proposed_brand"] is None
                deal_id = r.json()["id"]

                r = await client.get(f"/deals/{deal_id}")
                assert r.status_code == 200, r.text
                assert r.json()["proposed_brand"] is None

                r = await client.get("/deals")
                assert r.status_code == 200, r.text

                # A PATCH that only carries proposed_brand is a no-op…
                r = await client.patch(
                    f"/deals/{deal_id}", json={"proposed_brand": "Thompson Hotels"}
                )
                assert r.status_code == 200, r.text
                assert r.json()["proposed_brand"] is None

                # …and one mixing it with a real column still applies the rest.
                r = await client.patch(
                    f"/deals/{deal_id}",
                    json={"proposed_brand": "Thompson Hotels", "city": "Miami"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["city"] == "Miami"
                assert r.json()["proposed_brand"] is None

        messages = [rec.getMessage() for rec in caplog.records]
        assert any("deals.create: proposed_brand=" in m for m in messages)
        assert any("deals.update: proposed_brand dropped" in m for m in messages)
    finally:
        # Restore the column for any later test in this module.
        from app.migrations import run_startup_migrations

        await run_startup_migrations()
    assert await _column_exists()
