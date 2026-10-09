"""R-025 — the intended operating model on a deal.

``deals.operating_model`` (owner_operated / third_party / brand_managed) is
captured in the New Project wizard. It is descriptive only — no engine reads
it. Covered here: create / read / list / update round-trip, the allow-list,
and a schema WITHOUT the column (startup migration not yet run) never 500s.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-operating-model.db"
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


async def _column_exists() -> bool:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("PRAGMA table_info(deals)"))).all()
    return any(r[1] == "operating_model" for r in rows)


@pytest.mark.asyncio
async def test_migration_adds_the_column() -> None:
    assert await _column_exists()


@pytest.mark.asyncio
async def test_create_read_list_update_round_trip() -> None:
    async with _client() as client:
        r = await client.post(
            "/deals", json={"name": "Op Model Hotel", "operating_model": "third_party"}
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]
        assert r.json()["operating_model"] == "third_party"

        assert (await client.get(f"/deals/{deal_id}")).json()["operating_model"] == (
            "third_party"
        )
        listed = {d["id"]: d for d in (await client.get("/deals")).json()}
        assert listed[deal_id]["operating_model"] == "third_party"

        r = await client.patch(f"/deals/{deal_id}", json={"operating_model": "brand_managed"})
        assert r.status_code == 200, r.text
        assert r.json()["operating_model"] == "brand_managed"

        r = await client.patch(f"/deals/{deal_id}", json={"operating_model": None})
        assert r.status_code == 200, r.text
        assert r.json()["operating_model"] is None


@pytest.mark.asyncio
async def test_optional_and_allow_listed() -> None:
    async with _client() as client:
        r = await client.post("/deals", json={"name": "No op model"})
        assert r.status_code == 201, r.text
        assert r.json()["operating_model"] is None

        r = await client.post("/deals", json={"name": "Bad", "operating_model": "franchise"})
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_column(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("ALTER TABLE deals DROP COLUMN operating_model"))
        await session.commit()
    assert not await _column_exists()
    try:
        with caplog.at_level("WARNING", logger="app.api.deals"):
            async with _client() as client:
                r = await client.post(
                    "/deals", json={"name": "Old schema", "operating_model": "owner_operated"}
                )
                assert r.status_code == 201, r.text
                assert r.json()["operating_model"] is None
                deal_id = r.json()["id"]
                r = await client.get(f"/deals/{deal_id}")
                assert r.status_code == 200 and r.json()["operating_model"] is None
                assert (await client.get("/deals")).status_code == 200
                r = await client.patch(
                    f"/deals/{deal_id}",
                    json={"operating_model": "third_party", "city": "Miami"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["city"] == "Miami"
                assert r.json()["operating_model"] is None
        messages = [rec.getMessage() for rec in caplog.records]
        assert any("deals.create: operating_model=" in m for m in messages)
        assert any("deals.update: operating_model dropped" in m for m in messages)
    finally:
        await run_startup_migrations()
    assert await _column_exists()
