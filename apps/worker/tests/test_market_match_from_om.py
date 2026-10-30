"""FON-96 — the OM fills a deal's market / submarket only on an exact match.

After the metadata sync of an Offering Memorandum, a deal with NO market
(``market IS NULL AND str_market_id IS NULL``) may receive one from the
loaded taxonomy list — but only when the OM's location phrase, normalised,
equals exactly one METRO market name ("<City>, <ST>" ≡ "<City> - <ST> USA").
A submarket is filled only when the market matched uniquely and the OM's
submarket phrase equals exactly one of that market's submarket names. Two
candidates, zero candidates, a regional market (named in words) or an
analyst's earlier value: nothing is written. Provenance is
``market_source = 'om_exact_match'`` + ``market_source_detail`` and an
``audit_log`` row ``deal.market_matched_from_extraction``.

The list is the synthetic 6-row fixture (fictional names); no CoStar row.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-market-match-om.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

TENANT = "aaaaaaaa-9696-9696-9696-9696aaaaaaaa"
H = {"X-Tenant-Id": TENANT}
#: An EXTERNAL tenant the list is withheld from (switches default off).
EXTERNAL = "bbbbbbbb-9696-9696-9696-9696bbbbbbbb"
HX = {"X-Tenant-Id": EXTERNAL}
#: No registry row at all.
UNREGISTERED = "cccccccc-9696-9696-9696-9696cccccccc"
HU = {"X-Tenant-Id": UNREGISTERED}

_WORKER_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _WORKER_ROOT / "scripts" / "load_str_markets.py"

NORTH = "costar:northhaven-ts-usa"
SOUTH = "costar:southport-tz-usa"
REGIONAL = "costar:testland-north-usa"
COSTAR_CSV = "Submarket,Market,State,Market Type\n" + (
    "Harbor District,Northhaven - TS USA,TS,Metro\n"
    "Riverside,Northhaven - TS USA,TS,Metro\n"
    "Riverside,Southport - TZ USA,TZ,Metro\n"
    "Old Town,Southport - TZ USA,TZ,Metro\n"
    "Hill Country,Testland North USA,TS,Regional\n"
    "Lakeshore,Testland North USA,TS,Regional\n"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("load_str_markets_om", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("load_str_markets_om", mod)
    spec.loader.exec_module(mod)
    return mod


loader = _load_script()


@pytest.fixture(autouse=True)
async def _reset_db(tmp_path: Path) -> None:
    from app.database import get_engine, get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache, upsert_tenant

    await run_startup_migrations()
    async with get_session_factory()() as session:
        for tbl in ("audit_log", "documents", "deals", "str_markets", "reference_datasets", "tenants"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    # An INTERNAL workspace: the list (and storing its keys) is always served.
    reset_tenant_cache()
    await upsert_tenant(
        tenant_id=UUID(TENANT), clerk_org_id="org_test_internal_om_match",
        name="Fondok Internal (test)", kind="internal", status="active",
    )
    await upsert_tenant(
        tenant_id=UUID(EXTERNAL), clerk_org_id="org_test_external_om_match",
        name="Client (test)", kind="external", status="active",
    )
    os.environ.pop("MARKET_TAXONOMY_SERVE_EXTERNAL", None)
    from app.config import get_settings

    get_settings.cache_clear()
    p = tmp_path / "geo.csv"
    p.write_text(COSTAR_CSV, encoding="utf-8")
    parsed = loader.parse_costar_geography(p)
    assert parsed.problems == []
    await loader.plan_and_apply(get_engine(), parsed, commit=True, dataset_version="test")
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _field(name: str, value: Any, page: int | None = 3) -> dict[str, Any]:
    return {"field_name": name, "value": value, "source_page": page, "confidence": 0.9}


async def _create_deal(body: dict[str, Any] | None = None) -> dict[str, Any]:
    async with _client() as c:
        r = await c.post("/deals", headers=H, json={"name": "OM deal", **(body or {})})
        assert r.status_code == 201, r.text
        return r.json()


async def _run_sync(
    deal_id: str, fields: list[dict[str, Any]], *, doc_type: str = "OM", tenant: str = TENANT,
) -> str:
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory

    doc_id = str(uuid4())
    async with get_session_factory()() as session:
        await _sync_deal_metadata_from_extraction(
            session, deal_id=deal_id, tenant_id=tenant, fields=fields,
            doc_type=doc_type, doc_id=doc_id,
        )
    return doc_id


async def _deal(deal_id: str) -> dict[str, Any]:
    async with _client() as c:
        return (await c.get(f"/deals/{deal_id}", headers=H)).json()


async def _audit(action: str, tenant: str = TENANT) -> list[dict[str, Any]]:
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        rows = (await s.execute(text(
            "SELECT payload FROM audit_log WHERE action = :a AND tenant_id = :t"
        ), {"a": action, "t": tenant})).all()
    out = []
    for (payload,) in rows:
        blob = json.loads(payload) if isinstance(payload, str) else payload
        out.append(blob["input"])  # log_audit stores {input, output, metadata}
    return out


# ── normalisation ────────────────────────────────────────────────────────


def test_market_name_key_folds_costar_and_om_spellings_together() -> None:
    from app.services.market_match import market_name_key

    assert market_name_key("Northhaven - TS USA") == "northhaven ts"
    assert market_name_key("Northhaven, TS") == "northhaven ts"
    assert market_name_key("  northhaven ,ts ") == "northhaven ts"
    assert market_name_key("St. Northhaven, TS") == market_name_key("St Northhaven - TS USA")
    # The words of a regional market are left as they are — and never match a city.
    assert market_name_key("Testland North USA") == "testland north"
    assert market_name_key("") == ""


# ── the fill step ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unique_exact_match_fills_market_and_submarket_with_provenance() -> None:
    deal = await _create_deal({"city": "Northhaven, TS"})
    doc_id = await _run_sync(deal["id"], [
        _field("property_overview.keys", 120),
        _field("property_overview.location", "Northhaven, TS", page=2),
        _field("property_overview.submarket", "Riverside", page=4),
    ])
    got = await _deal(deal["id"])
    assert (got["market"], got["str_market_id"]) == ("Northhaven - TS USA", NORTH)
    assert (got["submarket"], got["submarket_id"]) == ("Riverside", f"{NORTH}/riverside")
    assert got["market_type"] == "metro"
    assert got["market_source"] == "om_exact_match"
    detail = got["market_source_detail"]
    assert detail["document_id"] == doc_id
    assert detail["doc_type"] == "OM"
    assert detail["market_phrase"] == "Northhaven, TS" and detail["market_page"] == 2
    assert detail["submarket_phrase"] == "Riverside" and detail["submarket_page"] == 4
    # Nothing in the detail presents the key as a vendor id.
    assert "costar_id" not in json.dumps(detail).lower()
    rows = await _audit("deal.market_matched_from_extraction")
    assert len(rows) == 1
    assert rows[0]["market"] == "Northhaven - TS USA"
    assert rows[0]["market_key"] == NORTH
    assert rows[0]["submarket"] == "Riverside"
    assert rows[0]["submarket_key"] == f"{NORTH}/riverside"
    assert rows[0]["document_id"] == doc_id
    assert rows[0]["mode"] == "fill"
    # ``city`` handling is unchanged (fill-only from the OM; it was set).
    assert got["city"] == "Northhaven, TS"


@pytest.mark.asyncio
async def test_city_and_state_fields_combine_when_the_city_carries_no_state() -> None:
    deal = await _create_deal()
    await _run_sync(deal["id"], [
        _field("property_overview.city", "Southport"),
        _field("property_overview.state", "TZ"),
    ])
    got = await _deal(deal["id"])
    assert (got["market"], got["str_market_id"]) == ("Southport - TZ USA", SOUTH)
    assert got["submarket"] is None and got["submarket_id"] is None
    assert got["market_source"] == "om_exact_match"


@pytest.mark.asyncio
async def test_a_submarket_phrase_alone_never_picks_a_market() -> None:
    """Arlington-style: 'Riverside' sits under two markets. With no city the
    market cannot be determined, so nothing is written — not even the name."""
    deal = await _create_deal()
    await _run_sync(deal["id"], [_field("property_overview.submarket", "Riverside")])
    got = await _deal(deal["id"])
    assert (got["market"], got["str_market_id"], got["submarket"], got["submarket_id"]) == (
        None, None, None, None,
    )
    assert got["market_source"] is None
    assert await _audit("deal.market_matched_from_extraction") == []


@pytest.mark.asyncio
async def test_two_market_candidates_write_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Two OM phrases that each exactly match a different metro market → ambiguous."""
    deal = await _create_deal()
    with caplog.at_level("INFO", logger="app.services.market_match"):
        await _run_sync(deal["id"], [
            _field("property_overview.location", "Northhaven, TS"),
            _field("property_overview.city", "Southport, TZ"),
        ])
    got = await _deal(deal["id"])
    assert got["market"] is None and got["str_market_id"] is None and got["market_source"] is None
    assert any("market_match: ambiguous" in r.getMessage() for r in caplog.records)
    assert await _audit("deal.market_matched_from_extraction") == []


@pytest.mark.asyncio
async def test_no_exact_match_writes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    deal = await _create_deal()
    with caplog.at_level("INFO", logger="app.services.market_match"):
        await _run_sync(deal["id"], [
            _field("property_overview.location", "Northhaven"),            # no state → not exact
            _field("property_overview.city", "North Haven, TS"),           # spelling differs → not exact
            _field("property_overview.submarket", "Downtown Northhaven"),  # not a listed submarket
        ])
    got = await _deal(deal["id"])
    assert got["market"] is None and got["str_market_id"] is None
    assert any("market_match: none" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_regional_market_is_never_matched_from_a_city() -> None:
    deal = await _create_deal()
    await _run_sync(deal["id"], [
        _field("property_overview.location", "Testland North"),
        _field("property_overview.city", "Testland North USA"),
        _field("property_overview.submarket", "Lakeshore"),
    ])
    got = await _deal(deal["id"])
    assert got["market"] is None and got["str_market_id"] is None
    assert got["submarket"] is None and got["submarket_id"] is None


@pytest.mark.asyncio
async def test_submarket_is_filled_only_when_it_is_exactly_one_of_the_markets_own() -> None:
    deal = await _create_deal()
    await _run_sync(deal["id"], [
        _field("property_overview.location", "Northhaven, TS"),
        _field("property_overview.submarket", "Old Town"),  # a Southport submarket
    ])
    got = await _deal(deal["id"])
    assert (got["market"], got["str_market_id"]) == ("Northhaven - TS USA", NORTH)
    assert got["submarket"] is None and got["submarket_id"] is None
    rows = await _audit("deal.market_matched_from_extraction")
    assert len(rows) == 1 and rows[0]["submarket"] is None
    assert got["market_source_detail"]["submarket_phrase"] == "Old Town"
    assert got["market_source_detail"]["submarket_match"] == "none"


@pytest.mark.asyncio
async def test_an_analyst_value_is_never_overwritten() -> None:
    typed = await _create_deal({"market": "My Typed Market"})
    picked = await _create_deal({"str_market_id": SOUTH})
    for d in (typed, picked):
        await _run_sync(d["id"], [
            _field("property_overview.location", "Northhaven, TS"),
            _field("property_overview.submarket", "Riverside"),
        ])
    t = await _deal(typed["id"])
    assert (t["market"], t["str_market_id"], t["submarket"], t["market_source"]) == (
        "My Typed Market", None, None, "analyst",
    )
    p = await _deal(picked["id"])
    assert (p["market"], p["str_market_id"], p["submarket_id"], p["market_source"]) == (
        "Southport - TZ USA", SOUTH, None, "analyst",
    )
    assert await _audit("deal.market_matched_from_extraction") == []


@pytest.mark.asyncio
async def test_a_market_the_analyst_cleared_stays_cleared_until_they_set_one() -> None:
    """Clearing is an analyst decision too: a later OM re-fills only once the
    field has no analyst claim (market_source NULL) — a market cleared via
    PATCH drops the claim, mirroring the positioning rule, so the OM may fill.
    A market an analyst then types sets the claim again and wins."""
    deal = await _create_deal({"market": "Wrong"})
    async with _client() as c:
        r = await c.patch(f"/deals/{deal['id']}", headers=H, json={"market": None})
        assert r.status_code == 200 and r.json()["market_source"] is None
    await _run_sync(deal["id"], [_field("property_overview.location", "Northhaven, TS")])
    got = await _deal(deal["id"])
    assert (got["market"], got["market_source"]) == ("Northhaven - TS USA", "om_exact_match")

    # The analyst corrects it: the OM's provenance is replaced by theirs and a
    # second OM run changes nothing.
    async with _client() as c:
        r = await c.patch(f"/deals/{deal['id']}", headers=H, json={"market": "Analyst Market"})
        assert r.status_code == 200, r.text
        assert r.json()["market_source"] == "analyst" and r.json()["market_source_detail"] is None
    await _run_sync(deal["id"], [_field("property_overview.location", "Southport, TZ")])
    got = await _deal(deal["id"])
    assert (got["market"], got["str_market_id"], got["market_source"]) == ("Analyst Market", None, "analyst")


@pytest.mark.asyncio
async def test_only_an_om_may_fill_the_market() -> None:
    deal = await _create_deal()
    for doc_type in ("T12", "MARKET_STUDY", "STR_TREND", None):
        await _run_sync(deal["id"], [_field("property_overview.location", "Northhaven, TS")], doc_type=doc_type)
    got = await _deal(deal["id"])
    assert got["market"] is None and got["str_market_id"] is None and got["market_source"] is None


@pytest.mark.asyncio
async def test_nothing_loaded_or_no_columns_writes_nothing_and_never_raises() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    deal = await _create_deal()
    async with get_session_factory()() as s:
        await s.execute(text("DELETE FROM str_markets"))
        await s.commit()
    await _run_sync(deal["id"], [_field("property_overview.location", "Northhaven, TS")])
    assert (await _deal(deal["id"]))["market"] is None

    async with get_session_factory()() as s:
        for col in ("submarket_id", "market_source", "market_source_detail"):
            await s.execute(text(f"ALTER TABLE deals DROP COLUMN {col}"))
        await s.commit()
    try:
        await _run_sync(deal["id"], [_field("property_overview.location", "Northhaven, TS")])
        assert (await _deal(deal["id"]))["market"] is None
    finally:
        await run_startup_migrations()


@pytest.mark.asyncio
async def test_a_submarket_only_edit_keeps_the_om_provenance_detail() -> None:
    deal = await _create_deal()
    await _run_sync(deal["id"], [_field("property_overview.location", "Northhaven, TS")])
    before = await _deal(deal["id"])
    assert before["market_source"] == "om_exact_match" and before["market_source_detail"]
    async with _client() as c:
        r = await c.patch(f"/deals/{deal['id']}", headers=H, json={"submarket_id": f"{NORTH}/riverside"})
        assert r.status_code == 200, r.text
        assert r.json()["submarket"] == "Riverside"
        assert r.json()["market_source"] == "om_exact_match"
        assert r.json()["market_source_detail"] == before["market_source_detail"]
        # Touching the market itself hands the field to the analyst.
        r = await c.patch(f"/deals/{deal['id']}", headers=H, json={"str_market_id": SOUTH})
        assert r.status_code == 200, r.text
        assert r.json()["market_source"] == "analyst" and r.json()["market_source_detail"] is None


@pytest.mark.asyncio
async def test_the_om_never_fills_a_market_for_a_tenant_the_list_is_withheld_from(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default closed: the canonical name, key and type of a licensed market
    never reach a workspace that may not be served the list — even via an OM."""
    from app.config import get_settings
    from app.database import get_session_factory

    fields = [
        _field("property_overview.location", "Northhaven, TS"),
        _field("property_overview.submarket", "Riverside"),
    ]
    deals: dict[str, str] = {}
    async with _client() as c:
        for name, headers in (("external", HX), ("unregistered", HU)):
            r = await c.post("/deals", headers=headers, json={"name": name})
            assert r.status_code == 201, r.text
            deals[name] = r.json()["id"]
    with caplog.at_level("INFO", logger="app.services.market_match"):
        await _run_sync(deals["external"], fields, tenant=EXTERNAL)
        await _run_sync(deals["unregistered"], fields, tenant=UNREGISTERED)
    assert sum("market_match: withheld" in r.getMessage() for r in caplog.records) == 2
    async with get_session_factory()() as s:
        rows = (await s.execute(text(
            "SELECT market, str_market_id, submarket, submarket_id, market_source "
            "FROM deals WHERE tenant_id IN (:a, :b)"
        ), {"a": EXTERNAL, "b": UNREGISTERED})).all()
    assert len(rows) == 2 and all(tuple(r) == (None, None, None, None, None) for r in rows)
    assert await _audit("deal.market_matched_from_extraction", EXTERNAL) == []
    assert await _audit("deal.market_matched_from_extraction", UNREGISTERED) == []

    # Both switches on → the external tenant is served, and the OM fills.
    os.environ["MARKET_TAXONOMY_SERVE_EXTERNAL"] = "1"
    get_settings.cache_clear()
    async with get_session_factory()() as s:
        await s.execute(text("UPDATE reference_datasets SET licensed_for_external = 1"))
        await s.commit()
    try:
        await _run_sync(deals["external"], fields, tenant=EXTERNAL)
        async with _client() as c:
            got = (await c.get(f"/deals/{deals['external']}", headers=HX)).json()
        assert (got["market"], got["str_market_id"], got["market_source"]) == (
            "Northhaven - TS USA", NORTH, "om_exact_match",
        )
        # Still never for a caller with no registry row.
        await _run_sync(deals["unregistered"], fields, tenant=UNREGISTERED)
        async with _client() as c:
            got = (await c.get(f"/deals/{deals['unregistered']}", headers=HU)).json()
        assert got["market"] is None and got["str_market_id"] is None
    finally:
        os.environ.pop("MARKET_TAXONOMY_SERVE_EXTERNAL", None)
        get_settings.cache_clear()
    # The internal tenant is filled as before.
    internal = await _create_deal()
    await _run_sync(internal["id"], fields)
    assert (await _deal(internal["id"]))["str_market_id"] == NORTH
