"""Sam #6 groundwork — deal market / submarket, the STR reference list.

STR's market / submarket definition file is not in hand. These tests pin the
groundwork that does not need it:

* the additive migrations add ``deals.str_market_id / market / submarket``
  and the ``str_markets`` reference table (SQLite mirror of Postgres);
* create / get / list / patch round-trip the three fields, blank → NULL,
  ``city`` untouched;
* an STR id is accepted only when it is in the loaded list (422 otherwise —
  including while nothing is loaded), a picked id fills STR's market name,
  and changing the market without an id clears the stale id;
* a schema WITHOUT the columns still serves every route (column probe);
* ``GET /reference/str-markets`` requires auth, returns ``[]`` while the
  table is empty, and searches / narrows once rows exist;
* ``scripts/load_str_markets.py`` validates the CSV, is a dry run by
  default, refuses ``--commit`` on any problem, and replaces the table.

Every STR identifier and name below is an obviously synthetic test value
(``TEST-…``) — no real STR id or submarket name is asserted anywhere.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-str-markets.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

TENANT = "aaaaaaaa-6666-6666-6666-6666aaaaaaaa"
H = {"X-Tenant-Id": TENANT}

_WORKER_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _WORKER_ROOT / "scripts" / "load_str_markets.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("load_str_markets", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("load_str_markets", mod)
    spec.loader.exec_module(mod)
    return mod


loader = _load_script()

HEADER = "str_market_id,market_name,submarket_id,submarket_name,state,country\n"
GOOD_CSV = HEADER + (
    "TEST-M1,Test Market One,,,TS,Testland\n"
    "TEST-M1,Test Market One,TEST-S1,Test Submarket Alpha,TS,Testland\n"
    "TEST-M1,Test Market One,TEST-S2,Test Submarket Beta,TS,Testland\n"
    "TEST-M2,Test Market Two,TEST-S3,Test Submarket Gamma,TX,Testland\n"
)


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "scenarios", "deals", "str_markets"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _csv(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "str.csv"
    p.write_text(body, encoding="utf-8")
    return p


async def _load_reference(tmp_path: Path, body: str = GOOD_CSV) -> dict:
    from app.database import get_engine

    parsed = loader.parse_csv(_csv(tmp_path, body))
    assert parsed.problems == []
    return await loader.plan_and_apply(get_engine(), parsed, commit=True)


async def _deal_columns() -> set[str]:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        rows = (await session.execute(text("PRAGMA table_info(deals)"))).all()
    return {r[1] for r in rows}


# ── migrations ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migrations_add_the_columns_and_the_reference_table() -> None:
    from app.database import get_session_factory

    assert {"str_market_id", "market", "submarket", "city"} <= await _deal_columns()
    async with get_session_factory()() as session:
        cols = {r[1] for r in (await session.execute(text("PRAGMA table_info(str_markets)"))).all()}
    assert {"id", "str_market_id", "market_name", "submarket_id", "submarket_name",
            "state", "country"} <= cols
    assert "tenant_id" not in cols  # reference data, deliberately unscoped


def test_postgres_ddl_mirrors_sqlite() -> None:
    from app.migrations import MIGRATIONS, SQLITE_MIGRATIONS

    pg = {n for n, _ in MIGRATIONS}
    lite = {n for n, _ in SQLITE_MIGRATIONS}
    for col in ("str_market_id", "market", "submarket"):
        assert f"deals.add_{col}" in pg
        assert f"deals.add_{col}_sqlite" in lite
    assert {"str_markets.create_table", "str_markets.idx_market"} <= pg & lite


def test_listener_does_not_treat_str_markets_as_tenant_data() -> None:
    from app.tenant_middleware import _TENANT_SCOPED_TABLES

    assert "str_markets" not in _TENANT_SCOPED_TABLES
    assert "deals" in _TENANT_SCOPED_TABLES


# ── the deal API ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_typed_market_round_trip_with_no_str_id() -> None:
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={
            "name": "Market deal", "city": "Somewhere, TS",
            "market": "  Typed   Market ", "submarket": "Typed Sub",
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["market"] == "Typed Market"
        assert body["submarket"] == "Typed Sub"
        assert body["str_market_id"] is None
        assert body["city"] == "Somewhere, TS"
        deal_id = body["id"]

        got = (await c.get(f"/deals/{deal_id}", headers=H)).json()
        assert (got["market"], got["submarket"], got["str_market_id"]) == (
            "Typed Market", "Typed Sub", None,
        )
        listed = {d["id"]: d for d in (await c.get("/deals", headers=H)).json()}
        assert listed[deal_id]["market"] == "Typed Market"

        # Corrected after creation; blank → NULL; city untouched.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "Other", "submarket": "  "})
        assert r.status_code == 200, r.text
        assert r.json()["market"] == "Other"
        assert r.json()["submarket"] is None
        assert r.json()["city"] == "Somewhere, TS"

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": None})
        assert r.status_code == 200 and r.json()["market"] is None


@pytest.mark.asyncio
async def test_no_market_fields_reads_null() -> None:
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={"name": "No market"})
        assert r.status_code == 201, r.text
        assert (r.json()["market"], r.json()["submarket"], r.json()["str_market_id"]) == (None, None, None)


@pytest.mark.asyncio
async def test_an_str_id_is_refused_while_nothing_is_loaded() -> None:
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={"name": "x", "str_market_id": "TEST-M1"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_str_market_id"
        assert (await c.get("/deals", headers=H)).json() == []


@pytest.mark.asyncio
async def test_picked_str_market_fills_the_name_and_a_typed_change_clears_the_id(tmp_path: Path) -> None:
    await _load_reference(tmp_path)
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={
            "name": "Picked", "str_market_id": "TEST-M1", "submarket": "Test Submarket Alpha",
        })
        assert r.status_code == 201, r.text
        assert r.json()["str_market_id"] == "TEST-M1"
        assert r.json()["market"] == "Test Market One"  # STR's name for the id
        deal_id = r.json()["id"]

        # An id not in the list is refused, and nothing changes.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"str_market_id": "TEST-NOPE"})
        assert r.status_code == 422, r.text
        assert (await c.get(f"/deals/{deal_id}", headers=H)).json()["str_market_id"] == "TEST-M1"

        # Picking another STR market by id alone writes its name too.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"str_market_id": "TEST-M2"})
        assert r.status_code == 200, r.text
        assert (r.json()["str_market_id"], r.json()["market"]) == ("TEST-M2", "Test Market Two")

        # A typed market (no id sent) clears the old STR id.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "Typed instead"})
        assert r.status_code == 200, r.text
        assert (r.json()["str_market_id"], r.json()["market"]) == (None, "Typed instead")


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_columns(caplog: pytest.LogCaptureFixture) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    async with get_session_factory()() as session:
        for col in ("str_market_id", "market", "submarket"):
            await session.execute(text(f"ALTER TABLE deals DROP COLUMN {col}"))
        await session.commit()
    assert not ({"str_market_id", "market", "submarket"} & await _deal_columns())
    try:
        with caplog.at_level("WARNING", logger="app.api.deals"):
            async with _client() as c:
                r = await c.post("/deals", headers=H, json={"name": "Old", "market": "M", "submarket": "S"})
                assert r.status_code == 201, r.text
                assert r.json()["market"] is None and r.json()["submarket"] is None
                deal_id = r.json()["id"]
                assert (await c.get(f"/deals/{deal_id}", headers=H)).status_code == 200
                assert (await c.get("/deals", headers=H)).status_code == 200
                r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "M", "city": "Miami"})
                assert r.status_code == 200, r.text
                assert r.json()["city"] == "Miami" and r.json()["market"] is None
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("deals.create: market=" in m for m in msgs)
        assert any("deals.update: market dropped" in m for m in msgs)
    finally:
        await run_startup_migrations()
    assert {"str_market_id", "market", "submarket"} <= await _deal_columns()


# ── GET /reference/str-markets ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_reference_route_is_empty_until_loaded() -> None:
    async with _client() as c:
        r = await c.get("/reference/str-markets", headers=H)
        assert r.status_code == 200, r.text
        assert r.json() == []
        r = await c.get("/reference/str-markets", headers=H, params={"q": "anything"})
        assert r.json() == []


@pytest.mark.asyncio
async def test_reference_route_searches_and_narrows(tmp_path: Path) -> None:
    await _load_reference(tmp_path)
    async with _client() as c:
        rows = (await c.get("/reference/str-markets", headers=H)).json()
        assert len(rows) == 4
        hits = (await c.get("/reference/str-markets", headers=H, params={"q": "beta"})).json()
        assert [h["submarket_id"] for h in hits] == ["TEST-S2"]
        assert hits[0]["str_market_id"] == "TEST-M1"
        narrowed = (await c.get("/reference/str-markets", headers=H,
                                params={"str_market_id": "TEST-M2"})).json()
        assert [n["submarket_name"] for n in narrowed] == ["Test Submarket Gamma"]
        assert (await c.get("/reference/str-markets", headers=H, params={"limit": 0})).status_code == 422


@pytest.mark.asyncio
async def test_reference_route_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ALLOW_TENANT_HEADER_WITHOUT_JWT", "0")
    get_settings.cache_clear()
    try:
        async with _client() as c:
            assert (await c.get("/reference/str-markets")).status_code == 401
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_reference_route_survives_a_db_without_the_table() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    async with get_session_factory()() as session:
        await session.execute(text("DROP TABLE str_markets"))
        await session.commit()
    try:
        async with _client() as c:
            r = await c.get("/reference/str-markets", headers=H, params={"q": "x"})
            assert r.status_code == 200 and r.json() == []
            r = await c.post("/deals", headers=H, json={"name": "x", "str_market_id": "TEST-M1"})
            assert r.status_code == 422, r.text
    finally:
        await run_startup_migrations()


# ── the loader ───────────────────────────────────────────────────────────


def test_parse_accepts_a_good_file_with_bom_and_any_column_order(tmp_path: Path) -> None:
    body = "﻿Market_Name, str_market_id ,submarket_name,submarket_id,country,state,notes\n" + (
        "Test Market One,TEST-M1,Test Submarket Alpha,TEST-S1,Testland,TS,ignored\n"
        "\n"
    )
    parsed = loader.parse_csv(_csv(tmp_path, body))
    assert parsed.problems == []
    assert parsed.ignored_columns == ["notes"]
    assert [r.as_dict() for r in parsed.rows] == [{
        "str_market_id": "TEST-M1", "market_name": "Test Market One",
        "submarket_id": "TEST-S1", "submarket_name": "Test Submarket Alpha",
        "state": "TS", "country": "Testland",
    }]


def test_parse_lists_every_problem(tmp_path: Path) -> None:
    body = HEADER + (
        ",Missing Id,,,TS,Testland\n"
        "TEST-M1,,,,TS,Testland\n"
        "TEST-M1,Name A,TEST-S1,Sub,TS,Testland\n"
        "TEST-M1,Name B,TEST-S2,Sub2,TS,Testland\n"
        "TEST-M1,Name A,TEST-S1,Sub again,TS,Testland\n"
        "TEST-M3,Name C,,Orphan Sub Name,TS,Testland\n"
    )
    problems = loader.parse_csv(_csv(tmp_path, body)).problems
    assert len(problems) == 5, problems
    assert problems[0].startswith("line 2: str_market_id is blank")
    assert problems[1].startswith("line 3: market_name is blank")
    assert "named 'Name B' here but 'Name A' on line 4" in problems[2]
    assert "duplicate of line 4" in problems[3]
    assert "has no submarket_id" in problems[4]


def test_parse_refuses_a_wrong_header_or_an_empty_file(tmp_path: Path) -> None:
    p = loader.parse_csv(_csv(tmp_path, "market,submarket\nA,B\n")).problems
    assert len(p) == 1 and "missing column" in p[0]
    assert loader.parse_csv(_csv(tmp_path, HEADER)).problems == ["the CSV has no data rows"]
    assert "cannot open" in loader.parse_csv(tmp_path / "nope.csv").problems[0]


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_and_commit_replaces(tmp_path: Path) -> None:
    from app.database import get_engine, get_session_factory

    parsed = loader.parse_csv(_csv(tmp_path, GOOD_CSV))
    dry = await loader.plan_and_apply(get_engine(), parsed, commit=False)
    assert dry["applied"] is False
    assert (dry["csv_rows"], dry["csv_markets"], dry["csv_submarkets"]) == (4, 2, 3)
    assert (dry["table_rows"], dry["would_add"], dry["would_remove"]) == (0, 4, 0)

    async def count() -> int:
        async with get_session_factory()() as s:
            return int((await s.execute(text("SELECT COUNT(*) FROM str_markets"))).scalar() or 0)

    assert await count() == 0
    done = await loader.plan_and_apply(get_engine(), parsed, commit=True)
    assert done["applied"] is True and await count() == 4

    # A reload REPLACES: one row dropped, one added.
    smaller = loader.parse_csv(_csv(tmp_path, HEADER + (
        "TEST-M1,Test Market One,TEST-S1,Test Submarket Alpha,TS,Testland\n"
        "TEST-M4,Test Market Four,,,TS,Testland\n"
    )))
    plan = await loader.plan_and_apply(get_engine(), smaller, commit=True)
    assert (plan["would_add"], plan["would_remove"], plan["unchanged_keys"]) == (1, 3, 1)
    assert await count() == 2


@pytest.mark.asyncio
async def test_commit_is_refused_while_the_csv_has_problems(tmp_path: Path) -> None:
    from app.database import get_engine, get_session_factory

    await _load_reference(tmp_path)
    bad = loader.parse_csv(_csv(tmp_path, GOOD_CSV + ",No Id,,,TS,Testland\n"))
    assert bad.problems
    out = await loader.plan_and_apply(get_engine(), bad, commit=True)
    assert out["applied"] is False
    async with get_session_factory()() as s:
        assert int((await s.execute(text("SELECT COUNT(*) FROM str_markets"))).scalar() or 0) == 4
