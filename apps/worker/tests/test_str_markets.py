"""Sam #6 / FON-96 — deal market / submarket and the market taxonomy list.

Two generations of the reference list are covered:

* the original STR CSV format (vendor ids) — the groundwork tests, unchanged
  in behaviour: additive migrations, create / get / list / patch round-trip,
  an unknown key is 422, a picked key fills the name, a typed market clears a
  stale key, a schema without the columns still serves every route;
* FON-96 — the CoStar geography export has NO vendor ids, so Fondok mints
  deterministic taxonomy keys from ``(Market, Submarket)``
  (``costar:<slug>`` / ``costar:<slug>/<slug>``), stores ``market_type``
  (metro / regional) and a ``reference_datasets`` row, and serves the list to
  an external workspace only when BOTH the dataset's licence flag and the
  ``MARKET_TAXONOMY_SERVE_EXTERNAL`` setting are on. Deals gain
  ``submarket_id`` (422 ``submarket_not_in_market`` when the key is not under
  the market key) and ``market_source`` provenance.

Every market / submarket name below is fictional (a synthetic 6-row
fixture); no CoStar row is reproduced anywhere in this file.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-str-markets.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

#: An INTERNAL tenant (Fondok's own workspace) — always sees the list.
TENANT = "aaaaaaaa-6666-6666-6666-6666aaaaaaaa"
H = {"X-Tenant-Id": TENANT}
#: An EXTERNAL tenant (a client) — sees the list only when licensed + enabled.
EXTERNAL = "bbbbbbbb-6666-6666-6666-6666bbbbbbbb"
HX = {"X-Tenant-Id": EXTERNAL}
#: A tenant with no registry row at all — treated as external (closed).
UNREGISTERED = "cccccccc-6666-6666-6666-6666cccccccc"
HU = {"X-Tenant-Id": UNREGISTERED}

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

# ── legacy STR CSV (vendor ids) ─────────────────────────────────────────
HEADER = "str_market_id,market_name,submarket_id,submarket_name,state,country\n"
GOOD_CSV = HEADER + (
    "TEST-M1,Test Market One,,,TS,Testland\n"
    "TEST-M1,Test Market One,TEST-S1,Test Submarket Alpha,TS,Testland\n"
    "TEST-M1,Test Market One,TEST-S2,Test Submarket Beta,TS,Testland\n"
    "TEST-M2,Test Market Two,TEST-S3,Test Submarket Gamma,TX,Testland\n"
)

# ── FON-96 synthetic CoStar-geography fixture (fictional names) ─────────
# Two metro markets ("City - ST USA" names) sharing the submarket name
# "Riverside" (the Arlington-style collision the key must survive) and one
# regional market named in words. 3 markets · 6 submarkets · 2 states.
COSTAR_HEADER = "Submarket,Market,State,Market Type\n"
COSTAR_ROWS = (
    "Harbor District,Northhaven - TS USA,TS,Metro\n"
    "Riverside,Northhaven - TS USA,TS,Metro\n"
    "Riverside,Southport - TZ USA,TZ,Metro\n"
    "Old Town,Southport - TZ USA,TZ,Metro\n"
    "Hill Country,Testland North USA,TS,Regional\n"
    "Lakeshore,Testland North USA,TS,Regional\n"
)
COSTAR_CSV = COSTAR_HEADER + COSTAR_ROWS
NORTH = "costar:northhaven-ts-usa"
SOUTH = "costar:southport-tz-usa"
REGIONAL = "costar:testland-north-usa"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache, upsert_tenant

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "scenarios", "deals", "str_markets", "reference_datasets", "tenants"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    reset_tenant_cache()
    await upsert_tenant(
        tenant_id=UUID(TENANT), clerk_org_id="org_test_internal_strm",
        name="Fondok Internal (test)", kind="internal", status="active",
    )
    await upsert_tenant(
        tenant_id=UUID(EXTERNAL), clerk_org_id="org_test_external_strm",
        name="Client (test)", kind="external", status="active",
    )
    os.environ.pop("MARKET_TAXONOMY_SERVE_EXTERNAL", None)
    get_settings.cache_clear()
    yield
    os.environ.pop("MARKET_TAXONOMY_SERVE_EXTERNAL", None)
    get_settings.cache_clear()


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _csv(tmp_path: Path, body: str, name: str = "str.csv") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


async def _load_reference(tmp_path: Path, body: str = GOOD_CSV) -> dict:
    from app.database import get_engine

    parsed = loader.parse_csv(_csv(tmp_path, body))
    assert parsed.problems == []
    return await loader.plan_and_apply(get_engine(), parsed, commit=True)


async def _load_costar(
    tmp_path: Path, body: str = COSTAR_CSV, *, licensed_external: bool = False,
    version: str = "test-2026-10",
) -> dict:
    from app.database import get_engine

    parsed = loader.parse_costar_geography(_csv(tmp_path, body, "geo.csv"))
    assert parsed.problems == [], parsed.problems
    return await loader.plan_and_apply(
        get_engine(), parsed, commit=True,
        dataset_version=version, licensed_external=licensed_external, loaded_by="pytest",
    )


async def _deal_columns() -> set[str]:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        rows = (await session.execute(text("PRAGMA table_info(deals)"))).all()
    return {r[1] for r in rows}


def _serve_external(on: bool) -> None:
    from app.config import get_settings

    os.environ["MARKET_TAXONOMY_SERVE_EXTERNAL"] = "1" if on else "0"
    get_settings.cache_clear()


# ── migrations ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migrations_add_the_columns_and_the_reference_table() -> None:
    from app.database import get_session_factory

    assert {"str_market_id", "market", "submarket", "submarket_id", "market_source",
            "market_source_detail", "city"} <= await _deal_columns()
    async with get_session_factory()() as session:
        cols = {r[1] for r in (await session.execute(text("PRAGMA table_info(str_markets)"))).all()}
        ds = {r[1] for r in (await session.execute(text("PRAGMA table_info(reference_datasets)"))).all()}
    assert {"id", "str_market_id", "market_name", "submarket_id", "submarket_name",
            "state", "country", "market_type", "source", "dataset_version"} <= cols
    assert "tenant_id" not in cols  # reference data, deliberately unscoped
    assert {"dataset", "version", "source_sha256", "row_count", "market_count",
            "submarket_count", "state_count", "licensed_for_external", "licence_note",
            "loaded_at", "loaded_by"} <= ds
    assert "tenant_id" not in ds


def test_postgres_ddl_mirrors_sqlite() -> None:
    from app.migrations import MIGRATIONS, SQLITE_MIGRATIONS

    pg = {n for n, _ in MIGRATIONS}
    lite = {n for n, _ in SQLITE_MIGRATIONS}
    for col in ("str_market_id", "market", "submarket", "submarket_id",
                "market_source", "market_source_detail"):
        assert f"deals.add_{col}" in pg
        assert f"deals.add_{col}_sqlite" in lite
    assert {"str_markets.create_table", "str_markets.idx_market",
            "str_markets.add_market_type", "str_markets.add_source",
            "str_markets.add_dataset_version", "reference_datasets.create_table"} <= pg
    assert {"str_markets.create_table", "str_markets.idx_market",
            "str_markets.add_market_type_sqlite", "str_markets.add_source_sqlite",
            "str_markets.add_dataset_version_sqlite", "reference_datasets.create_table"} <= lite


def test_listener_does_not_treat_reference_tables_as_tenant_data() -> None:
    from app.tenant_middleware import _TENANT_SCOPED_TABLES

    assert "str_markets" not in _TENANT_SCOPED_TABLES
    assert "reference_datasets" not in _TENANT_SCOPED_TABLES
    assert "deals" in _TENANT_SCOPED_TABLES


# ── taxonomy keys ────────────────────────────────────────────────────────


def test_keys_are_deterministic_slugs_never_vendor_ids() -> None:
    from app.services.str_markets import (
        MARKET_KEY_PREFIX,
        infer_market_type,
        market_key,
        slugify,
        submarket_key,
    )

    assert MARKET_KEY_PREFIX == "costar:"
    assert slugify("Northhaven - TS USA") == "northhaven-ts-usa"
    assert slugify("  O'Harbour / Côte-d’Or  ") == "o-harbour-cote-d-or"  # noqa: RUF001 — the curly quote is the point
    assert market_key("Northhaven - TS USA") == NORTH
    assert submarket_key("Northhaven - TS USA", "Harbor District") == f"{NORTH}/harbor-district"
    # The same submarket name under two markets → two different keys.
    assert submarket_key("Northhaven - TS USA", "Riverside") != submarket_key("Southport - TZ USA", "Riverside")
    # Same input → same key, always (no counter, no random part).
    assert market_key("Northhaven - TS USA") == market_key("northhaven - ts usa")
    assert infer_market_type("Northhaven - TS USA") == "metro"
    assert infer_market_type("Testland North USA") == "regional"
    assert infer_market_type("Southport - TZ") == "regional"  # no USA suffix → not the metro pattern


# ── the deal API (legacy behaviour, unchanged) ───────────────────────────


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
        assert body["submarket_id"] is None
        assert body["market_type"] is None
        assert body["market_source"] == "analyst"
        assert body["city"] == "Somewhere, TS"
        deal_id = body["id"]

        got = (await c.get(f"/deals/{deal_id}", headers=H)).json()
        assert (got["market"], got["submarket"], got["str_market_id"], got["submarket_id"]) == (
            "Typed Market", "Typed Sub", None, None,
        )
        listed = {d["id"]: d for d in (await c.get("/deals", headers=H)).json()}
        assert listed[deal_id]["market"] == "Typed Market"

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "Other", "submarket": "  "})
        assert r.status_code == 200, r.text
        assert r.json()["market"] == "Other"
        assert r.json()["submarket"] is None
        assert r.json()["city"] == "Somewhere, TS"

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": None})
        assert r.status_code == 200 and r.json()["market"] is None
        # Everything cleared → no analyst claim on the field any more.
        assert r.json()["market_source"] is None


@pytest.mark.asyncio
async def test_no_market_fields_reads_null() -> None:
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={"name": "No market"})
        assert r.status_code == 201, r.text
        b = r.json()
        assert (b["market"], b["submarket"], b["str_market_id"], b["submarket_id"],
                b["market_type"], b["market_source"]) == (None, None, None, None, None, None)


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
        assert r.json()["market"] == "Test Market One"
        deal_id = r.json()["id"]

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"str_market_id": "TEST-NOPE"})
        assert r.status_code == 422, r.text
        assert (await c.get(f"/deals/{deal_id}", headers=H)).json()["str_market_id"] == "TEST-M1"

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"str_market_id": "TEST-M2"})
        assert r.status_code == 200, r.text
        assert (r.json()["str_market_id"], r.json()["market"]) == ("TEST-M2", "Test Market Two")

        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "Typed instead"})
        assert r.status_code == 200, r.text
        assert (r.json()["str_market_id"], r.json()["market"]) == (None, "Typed instead")


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_columns(caplog: pytest.LogCaptureFixture) -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    dropped = ("str_market_id", "market", "submarket", "submarket_id", "market_source", "market_source_detail")
    async with get_session_factory()() as session:
        for col in dropped:
            await session.execute(text(f"ALTER TABLE deals DROP COLUMN {col}"))
        await session.commit()
    assert not (set(dropped) & await _deal_columns())
    try:
        with caplog.at_level("WARNING", logger="app.api.deals"):
            async with _client() as c:
                r = await c.post("/deals", headers=H, json={"name": "Old", "market": "M", "submarket": "S"})
                assert r.status_code == 201, r.text
                assert r.json()["market"] is None and r.json()["submarket"] is None
                assert r.json()["submarket_id"] is None and r.json()["market_type"] is None
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
    assert set(dropped) <= await _deal_columns()


# ── FON-96 — submarket keys on the deal ──────────────────────────────────


@pytest.mark.asyncio
async def test_submarket_key_must_sit_under_the_market_key(tmp_path: Path) -> None:
    await _load_costar(tmp_path)
    async with _client() as c:
        # Riverside under Southport while the market is Northhaven → refused.
        r = await c.post("/deals", headers=H, json={
            "name": "x", "str_market_id": NORTH, "submarket_id": f"{SOUTH}/riverside",
        })
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "submarket_not_in_market"
        # A submarket key with no market key cannot belong to anything.
        r = await c.post("/deals", headers=H, json={"name": "x", "submarket_id": f"{NORTH}/riverside"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "submarket_not_in_market"
        # An unknown submarket key is refused too.
        r = await c.post("/deals", headers=H, json={
            "name": "x", "str_market_id": NORTH, "submarket_id": f"{NORTH}/nowhere",
        })
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_submarket_id"
        assert (await c.get("/deals", headers=H)).json() == []

        # The right pair: both names come from the list, type is joined.
        r = await c.post("/deals", headers=H, json={
            "name": "Picked", "str_market_id": NORTH, "submarket_id": f"{NORTH}/riverside",
        })
        assert r.status_code == 201, r.text
        b = r.json()
        assert (b["market"], b["submarket"]) == ("Northhaven - TS USA", "Riverside")
        assert (b["str_market_id"], b["submarket_id"]) == (NORTH, f"{NORTH}/riverside")
        assert b["market_type"] == "metro"
        assert b["market_source"] == "analyst"
        got = (await c.get(f"/deals/{b['id']}", headers=H)).json()
        assert got["market_type"] == "metro" and got["submarket_id"] == f"{NORTH}/riverside"


@pytest.mark.asyncio
async def test_typed_submarket_has_no_key_and_a_market_change_clears_it(tmp_path: Path) -> None:
    await _load_costar(tmp_path)
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={
            "name": "Typed sub", "str_market_id": NORTH, "submarket": "Somewhere Typed",
        })
        assert r.status_code == 201, r.text
        assert r.json()["submarket"] == "Somewhere Typed" and r.json()["submarket_id"] is None
        deal_id = r.json()["id"]

        # Pick a submarket by key alone → its name is written.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"submarket_id": f"{NORTH}/harbor-district"})
        assert r.status_code == 200, r.text
        assert (r.json()["submarket"], r.json()["submarket_id"]) == ("Harbor District", f"{NORTH}/harbor-district")

        # Switching the market without a submarket key clears the key, and the
        # old name goes with it (a Northhaven submarket under Southport is wrong).
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"str_market_id": SOUTH})
        assert r.status_code == 200, r.text
        assert r.json()["market"] == "Southport - TZ USA"
        assert r.json()["submarket_id"] is None
        assert r.json()["submarket"] is None

        # A typed submarket (no key sent) drops any key.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"submarket_id": f"{SOUTH}/old-town"})
        assert r.status_code == 200 and r.json()["submarket_id"] == f"{SOUTH}/old-town"
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"submarket": "Typed over"})
        assert r.status_code == 200, r.text
        assert (r.json()["submarket"], r.json()["submarket_id"]) == ("Typed over", None)

        # A typed market clears both keys.
        r = await c.patch(f"/deals/{deal_id}", headers=H, json={"market": "Typed market"})
        assert r.status_code == 200, r.text
        assert (r.json()["str_market_id"], r.json()["submarket_id"], r.json()["market_type"]) == (None, None, None)


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
async def test_typeahead_kinds_keys_labels_and_ranking(tmp_path: Path) -> None:
    await _load_costar(tmp_path)
    async with _client() as c:
        markets = (await c.get("/reference/str-markets", headers=H, params={"kind": "market"})).json()
        assert [m["market_key"] for m in markets] == [NORTH, SOUTH, REGIONAL]
        assert [m["market_type"] for m in markets] == ["metro", "metro", "regional"]
        assert markets[0]["label"] == "Northhaven - TS USA · Metro market"
        assert markets[2]["label"] == "Testland North USA · Regional market"
        assert all(m["submarket_id"] is None and m["submarket_key"] is None for m in markets)
        # Old field names still present (existing clients).
        assert markets[0]["str_market_id"] == NORTH and markets[0]["state"] == "TS"

        subs = (await c.get("/reference/str-markets", headers=H, params={"kind": "submarket"})).json()
        assert len(subs) == 6
        assert all(s["submarket_key"] == s["submarket_id"] for s in subs)
        riverside = [s for s in subs if s["submarket_name"] == "Riverside"]
        assert {s["submarket_key"] for s in riverside} == {f"{NORTH}/riverside", f"{SOUTH}/riverside"}
        assert sorted(s["label"] for s in riverside) == [
            "Riverside · submarket of Northhaven - TS USA",
            "Riverside · submarket of Southport - TZ USA",
        ]

        narrowed = (await c.get("/reference/str-markets", headers=H,
                                params={"kind": "submarket", "market_key": SOUTH})).json()
        assert [s["submarket_name"] for s in narrowed] == ["Old Town", "Riverside"]

        # Ranking: a prefix hit before a contains hit.
        hits = (await c.get("/reference/str-markets", headers=H,
                            params={"q": "river", "kind": "submarket"})).json()
        assert [h["submarket_name"] for h in hits] == ["Riverside", "Riverside"]
        hits = (await c.get("/reference/str-markets", headers=H, params={"q": "north", "kind": "market"})).json()
        assert [h["market_name"] for h in hits] == ["Northhaven - TS USA", "Testland North USA"]

        # No kind → every row, market rows first within a market.
        rows = (await c.get("/reference/str-markets", headers=H)).json()
        assert len(rows) == 9
        assert rows[0]["market_key"] == NORTH and rows[0]["submarket_key"] is None


@pytest.mark.asyncio
async def test_licensing_gate_internal_sees_external_needs_both_switches(tmp_path: Path) -> None:
    await _load_costar(tmp_path, licensed_external=False)
    async with _client() as c:
        # Internal: always.
        assert len((await c.get("/reference/str-markets", headers=H)).json()) == 9
        # External / unregistered: both switches off → nothing.
        assert (await c.get("/reference/str-markets", headers=HX)).json() == []
        assert (await c.get("/reference/str-markets", headers=HU)).json() == []

        # Setting on, dataset flag off → still nothing.
        _serve_external(True)
        assert (await c.get("/reference/str-markets", headers=HX)).json() == []
        assert len((await c.get("/reference/str-markets", headers=H)).json()) == 9

    # Dataset flag on, setting off → still nothing.
    _serve_external(False)
    await _load_costar(tmp_path, licensed_external=True)
    async with _client() as c:
        assert (await c.get("/reference/str-markets", headers=HX)).json() == []
        # Both on → served.
        _serve_external(True)
        assert len((await c.get("/reference/str-markets", headers=HX)).json()) == 9
        # A caller with no registry row is treated as external: served under
        # the same two switches, never more than a registered client.
        assert len((await c.get("/reference/str-markets", headers=HU)).json()) == 9
        # Flip the setting back off → withheld again, internal unaffected.
        _serve_external(False)
        assert (await c.get("/reference/str-markets", headers=HX)).json() == []
        assert len((await c.get("/reference/str-markets", headers=H)).json()) == 9


@pytest.mark.asyncio
async def test_external_tenant_may_still_store_a_key_it_was_served(tmp_path: Path) -> None:
    """Storing a legitimately picked key is validated against the table only,
    never against the caller's kind (design B.2)."""
    await _load_costar(tmp_path)
    async with _client() as c:
        r = await c.post("/deals", headers=HX, json={"name": "ext", "str_market_id": NORTH})
        assert r.status_code == 201, r.text
        assert r.json()["market"] == "Northhaven - TS USA"


@pytest.mark.asyncio
async def test_admin_health_reports_the_reference_posture(tmp_path: Path) -> None:
    async with _client() as c:
        body = (await c.get("/admin/health", headers=H)).json()
        ref = body["reference"]
        assert ref["str_markets"]["loaded"] is False
        assert ref["market_taxonomy_serve_external"] is False
        assert ref["external_tenants_served"] is False
    await _load_costar(tmp_path, licensed_external=True, version="test-2026-10")
    _serve_external(True)
    async with _client() as c:
        body = (await c.get("/admin/health", headers=H)).json()
        ref = body["reference"]
        assert ref["str_markets"]["loaded"] is True
        assert ref["str_markets"]["version"] == "test-2026-10"
        assert (ref["str_markets"]["row_count"], ref["str_markets"]["market_count"],
                ref["str_markets"]["submarket_count"], ref["str_markets"]["state_count"]) == (9, 3, 6, 2)
        assert ref["str_markets"]["licensed_for_external"] is True
        assert ref["market_taxonomy_serve_external"] is True
        assert ref["external_tenants_served"] is True


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
async def test_reference_route_survives_a_db_without_the_tables() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    async with get_session_factory()() as session:
        await session.execute(text("DROP TABLE str_markets"))
        await session.execute(text("DROP TABLE reference_datasets"))
        await session.commit()
    try:
        async with _client() as c:
            r = await c.get("/reference/str-markets", headers=H, params={"q": "x"})
            assert r.status_code == 200 and r.json() == []
            r = await c.get("/reference/str-markets", headers=HX, params={"q": "x"})
            assert r.status_code == 200 and r.json() == []
            r = await c.post("/deals", headers=H, json={"name": "x", "str_market_id": "TEST-M1"})
            assert r.status_code == 422, r.text
            r = await c.post("/deals", headers=H, json={"name": "ok", "market": "Typed"})
            assert r.status_code == 201 and r.json()["market_type"] is None
            body = (await c.get("/admin/health", headers=H)).json()
            assert body["reference"]["str_markets"]["loaded"] is False
    finally:
        await run_startup_migrations()


# ── the legacy loader ────────────────────────────────────────────────────


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
        "market_type": None, "source": None, "dataset_version": None,
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


# ── FON-96 — the costar-geography loader format ──────────────────────────


def test_costar_parse_derives_keys_types_and_counts(tmp_path: Path) -> None:
    parsed = loader.parse_costar_geography(_csv(tmp_path, COSTAR_CSV, "geo.csv"))
    assert parsed.problems == []
    assert (parsed.market_count, parsed.submarket_count, parsed.state_count) == (3, 6, 2)
    assert len(parsed.rows) == 9  # 3 market-level rows + 6 submarkets
    assert len(parsed.source_sha256) == 64
    by_key = {(r.str_market_id, r.submarket_id): r for r in parsed.rows}
    north = by_key[(NORTH, None)]
    assert (north.market_name, north.state, north.market_type) == ("Northhaven - TS USA", "TS", "metro")
    assert north.source == "costar_geography_export"
    assert north.country == "USA"
    assert by_key[(REGIONAL, None)].market_type == "regional"
    riverside_n = by_key[(NORTH, f"{NORTH}/riverside")]
    riverside_s = by_key[(SOUTH, f"{SOUTH}/riverside")]
    assert riverside_n.submarket_name == riverside_s.submarket_name == "Riverside"
    assert riverside_n.market_type == "metro" and riverside_s.state == "TZ"
    # No row carries a value that is not in the file (besides the keys).
    assert {r.dataset_version for r in parsed.rows} == {None}


def test_costar_parse_is_header_case_insensitive_and_infers_a_missing_type(tmp_path: Path) -> None:
    body = "﻿ market , SUBMARKET ,state\n" + (
        "Northhaven - TS USA, Harbor District ,TS\n"
        "Testland North USA,Lakeshore,TS\n"
    )
    parsed = loader.parse_costar_geography(_csv(tmp_path, body, "geo.csv"))
    assert parsed.problems == []
    types = {r.str_market_id: r.market_type for r in parsed.rows if r.submarket_id is None}
    assert types == {NORTH: "metro", REGIONAL: "regional"}
    assert {r.submarket_name for r in parsed.rows if r.submarket_id} == {"Harbor District", "Lakeshore"}


def test_costar_parse_reads_a_workbook(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Submarkets"
    ws.append(["Submarket", "Market", "State", "Market Type"])
    for line in COSTAR_ROWS.strip().splitlines():
        ws.append(line.split(","))
    notes = wb.create_sheet("Notes")
    notes.append(["Item", "Note"])
    notes.append(["Licensing", "fixture"])
    p = tmp_path / "geo.xlsx"
    wb.save(p)
    parsed = loader.parse_costar_geography(p)
    assert parsed.problems == []
    assert (parsed.market_count, parsed.submarket_count, parsed.state_count) == (3, 6, 2)
    assert len(parsed.rows) == 9


def test_costar_parse_lists_every_problem(tmp_path: Path) -> None:
    body = COSTAR_HEADER + (
        ",Northhaven - TS USA,TS,Metro\n"                 # blank submarket
        "Harbor District,,TS,Metro\n"                     # blank market
        "Harbor District,Northhaven - TS USA,TS,Metro\n"
        "Harbor District,Northhaven - TS USA,TS,Metro\n"  # 5: duplicate (Market, Submarket)
        "Lakeshore,Testland North USA,TS,Regional\n"      # 6
        "Hill Country,Testland North USA,TZ,Regional\n"   # 7: second state for one market
        "Old Town,Testland North USA,TS,Metro\n"          # 8: type says metro, name is not City - ST USA
        "Riverside,Testland North USA,TS,Elsewhere\n"     # 9: unknown type word
        "Riverside,Southport - TZ USA,TS,Metro\n"         # 10: state differs from the name's ST
    )
    problems = loader.parse_costar_geography(_csv(tmp_path, body, "geo.csv")).problems
    assert len(problems) == 7, problems
    assert problems[0].startswith("line 2: Submarket is blank")
    assert problems[1].startswith("line 3: Market is blank")
    assert "duplicate of line 4" in problems[2]
    assert "state 'TZ' here but 'TS' on line 6" in problems[3]
    assert "Market Type 'Metro' but the name is not a \"City - ST USA\" market name" in problems[4]
    assert "Market Type 'Elsewhere' is neither Metro nor Regional" in problems[5]
    assert "State 'TS' but the market name says 'TZ'" in problems[6]


def test_costar_parse_refuses_slug_collisions(tmp_path: Path) -> None:
    body = COSTAR_HEADER + (
        "Harbor District,Northhaven - TS USA,TS,Metro\n"
        "Harbor-District,Northhaven - TS USA,TS,Metro\n"   # distinct names, one slug
        "Old Town,North Haven - TS USA,TS,Metro\n"
        "Old Town,North-Haven - TS USA,TS,Metro\n"         # two distinct market names, one slug
    )
    problems = loader.parse_costar_geography(_csv(tmp_path, body, "geo.csv")).problems
    assert len(problems) == 2, problems
    assert "submarket names 'Harbor District' and 'Harbor-District'" in problems[0]
    assert f"{NORTH}/harbor-district" in problems[0]
    assert "market names 'North Haven - TS USA' and 'North-Haven - TS USA'" in problems[1]
    assert "costar:north-haven-ts-usa" in problems[1]


def test_costar_parse_refuses_a_wrong_header_or_an_empty_file(tmp_path: Path) -> None:
    p = loader.parse_costar_geography(_csv(tmp_path, "Submarket,Market\nA,B\n", "geo.csv")).problems
    assert len(p) == 1 and "missing column" in p[0] and "state" in p[0].lower()
    assert loader.parse_costar_geography(_csv(tmp_path, COSTAR_HEADER, "geo.csv")).problems == [
        "the file has no data rows"
    ]
    assert "cannot open" in loader.parse_costar_geography(tmp_path / "nope.csv").problems[0]


@pytest.mark.asyncio
async def test_costar_commit_writes_the_table_and_the_dataset_row_together(tmp_path: Path) -> None:
    from app.database import get_engine, get_session_factory

    parsed = loader.parse_costar_geography(_csv(tmp_path, COSTAR_CSV, "geo.csv"))
    dry = await loader.plan_and_apply(get_engine(), parsed, commit=False, dataset_version="v-dry")
    assert dry["applied"] is False
    assert (dry["csv_rows"], dry["csv_markets"], dry["csv_submarkets"], dry["csv_states"]) == (9, 3, 6, 2)
    async with get_session_factory()() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM str_markets"))).scalar() == 0
        assert (await s.execute(text("SELECT COUNT(*) FROM reference_datasets"))).scalar() == 0

    done = await loader.plan_and_apply(
        get_engine(), parsed, commit=True, dataset_version="test-2026-10", loaded_by="pytest",
    )
    assert done["applied"] is True
    async with get_session_factory()() as s:
        rows = (await s.execute(text(
            "SELECT id, str_market_id, submarket_id, market_type, source, dataset_version "
            "FROM str_markets ORDER BY id"
        ))).all()
        assert len(rows) == 9
        assert all(r[4] == "costar_geography_export" and r[5] == "test-2026-10" for r in rows)
        assert {r[3] for r in rows} == {"metro", "regional"}
        # The primary key is the market key, or the submarket key.
        assert {r[0] for r in rows} == {r[2] or r[1] for r in rows}
        ds = (await s.execute(text("SELECT * FROM reference_datasets"))).mappings().one()
        assert ds["dataset"] == "str_markets"
        assert ds["version"] == "test-2026-10"
        assert ds["source_sha256"] == parsed.source_sha256
        assert (ds["row_count"], ds["market_count"], ds["submarket_count"], ds["state_count"]) == (9, 3, 6, 2)
        assert not ds["licensed_for_external"]
        assert ds["loaded_by"] == "pytest"

    # A second load with the licence flag replaces the dataset row too.
    again = await loader.plan_and_apply(
        get_engine(), parsed, commit=True, dataset_version="test-2026-11", licensed_external=True,
    )
    assert again["applied"] is True
    async with get_session_factory()() as s:
        ds = (await s.execute(text("SELECT version, licensed_for_external FROM reference_datasets"))).all()
        assert len(ds) == 1 and ds[0][0] == "test-2026-11" and bool(ds[0][1]) is True


@pytest.mark.asyncio
async def test_costar_commit_is_refused_on_any_problem(tmp_path: Path) -> None:
    from app.database import get_engine, get_session_factory

    await _load_costar(tmp_path)
    bad = loader.parse_costar_geography(_csv(tmp_path, COSTAR_CSV + ",Northhaven - TS USA,TS,Metro\n", "geo.csv"))
    assert bad.problems
    out = await loader.plan_and_apply(get_engine(), bad, commit=True, dataset_version="bad")
    assert out["applied"] is False
    async with get_session_factory()() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM str_markets"))).scalar() == 9
        assert (await s.execute(text("SELECT version FROM reference_datasets"))).scalar() == "test-2026-10"


@pytest.mark.asyncio
async def test_orphan_report_lists_deals_whose_keys_left_the_list(tmp_path: Path) -> None:
    from app.database import get_engine

    await _load_costar(tmp_path)
    async with _client() as c:
        kept = (await c.post("/deals", headers=H, json={
            "name": "kept", "str_market_id": NORTH, "submarket_id": f"{NORTH}/riverside",
        })).json()
        gone = (await c.post("/deals", headers=HX, json={
            "name": "gone", "str_market_id": SOUTH, "submarket_id": f"{SOUTH}/old-town",
        })).json()
        typed = (await c.post("/deals", headers=H, json={"name": "typed", "market": "Typed"})).json()

    smaller = loader.parse_costar_geography(_csv(tmp_path, COSTAR_HEADER + (
        "Harbor District,Northhaven - TS USA,TS,Metro\n"
        "Riverside,Northhaven - TS USA,TS,Metro\n"
        "Riverside,Southport - TZ USA,TZ,Metro\n"
    ), "geo.csv"))
    assert smaller.problems == []
    orphans = await loader.report_orphans(get_engine(), smaller)
    assert [o["deal_id"] for o in orphans] == [gone["id"]]
    assert orphans[0]["tenant_id"] == EXTERNAL
    assert orphans[0]["market_key"] == SOUTH and orphans[0]["market_key_in_list"] is True
    assert orphans[0]["submarket_key"] == f"{SOUTH}/old-town" and orphans[0]["submarket_key_in_list"] is False
    assert orphans[0]["market"] == "Southport - TZ USA" and orphans[0]["submarket"] == "Old Town"
    assert kept["id"] not in {o["deal_id"] for o in orphans}
    assert typed["id"] not in {o["deal_id"] for o in orphans}

    # Applying the smaller file never touches the deal: names and keys stay.
    await loader.plan_and_apply(get_engine(), smaller, commit=True, dataset_version="v2")
    async with _client() as c:
        got = (await c.get(f"/deals/{gone['id']}", headers=HX)).json()
        assert (got["str_market_id"], got["submarket_id"], got["submarket"]) == (
            SOUTH, f"{SOUTH}/old-town", "Old Town",
        )
        assert got["market_type"] == "metro"  # the market itself is still listed
