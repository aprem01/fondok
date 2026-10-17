"""Sam 2026-10-15 — the OM's brand suggests the deal's positioning.

    "If we can confidently identify the brand from the OM and map it to STR,
    its chain scale should populate automatically as the suggested
    positioning. The analyst should still be able to override the value,
    and the source should remain visible."

Covered:

* the matcher's confidence rule (``services.brand_chain_scale``): exact,
  alias, short form and unambiguous leading-brand-mark matches; ambiguous
  ("Starwood", "Marriott Courtyard"), unknown and unpublished-scale
  ("Various") brands suggest nothing;
* the catalog JSON the matcher reads is the web catalog's export (shape and
  the scale → positioning id map the engine runner understands);
* the metadata sync: OM-only (FON-84 gate), fill-only on NULL / ``default``,
  never over an analyst's tier, source + document / page persisted and
  returned on ``GET /deals/{id}``;
* the analyst routes: a tier chosen at create / PATCH is ``analyst``;
  ``default`` clears the source.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-om-brand-positioning.db"
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
        for tbl in ("audit_log", "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ───────────────────────────── matcher ─────────────────────────────


@pytest.mark.parametrize(
    ("om_brand", "catalog_brand", "kind", "positioning"),
    [
        ("Kimpton Hotels & Restaurants", "Kimpton Hotels & Restaurants", "exact", "upper-upscale"),
        ("kimpton hotels and restaurants", "Kimpton Hotels & Restaurants", "exact", "upper-upscale"),
        ("Kimpton", "Kimpton Hotels & Restaurants", "short_form", "upper-upscale"),
        ("KIMPTON.", "Kimpton Hotels & Restaurants", "short_form", "upper-upscale"),
        ("Kimpton Angler's", "Kimpton Hotels & Restaurants", "leading_brand_mark", "upper-upscale"),
        ("Kimpton Angler’s Hotel", "Kimpton Hotels & Restaurants", "leading_brand_mark", "upper-upscale"),
        ("Graduate Hotels", "Graduate by Hilton", "alias", "upper-upscale"),
        ("Le Meridien", "Le Méridien", "exact", "upper-upscale"),
        ("Westin", "The Westin Hotels & Resorts", "short_form", "upper-upscale"),
        ("Hampton", "Hampton by Hilton", "short_form", "upper-midscale"),
        ("Hampton Inn & Suites", "Hampton by Hilton", "leading_brand_mark", "upper-midscale"),
        ("Hilton Garden Inn Miami Airport", "Hilton Garden Inn", "leading_brand_mark", "upscale"),
    ],
)
def test_confident_matches(om_brand: str, catalog_brand: str, kind: str, positioning: str) -> None:
    from app.services.brand_chain_scale import match_brand

    m = match_brand(om_brand)
    assert m.status == "matched", m
    assert m.brand is not None and m.brand.name == catalog_brand
    assert m.kind == kind
    assert m.suggested_positioning == positioning


@pytest.mark.parametrize(
    ("om_brand", "status"),
    [
        # Alias shared by a dozen legacy Starwood brands under Marriott.
        ("Starwood", "ambiguous"),
        # A second brand's word follows the leading mark — Marriott Hotels or
        # Courtyard? Not guessed.
        ("Marriott Courtyard", "ambiguous"),
        ("Hilton Garden Miami", "ambiguous"),
        # "Various" in the catalog — no single STR scale.
        ("Design Hotels", "no_str_scale"),
        ("Independent / Unflagged", "no_str_scale"),
        # Catalog tier but no STR row — the catalog's own tier is not STR's.
        ("Baccarat Hotels & Resorts", "no_str_scale"),
        # Not a brand at all / near-misses are never fuzzy-matched.
        ("Miami Beach Boutique", "no_match"),
        ("Kimptn", "no_match"),
        ("", "no_match"),
    ],
)
def test_no_suggestion_cases(om_brand: str, status: str) -> None:
    from app.services.brand_chain_scale import match_brand

    m = match_brand(om_brand)
    assert m.status == status, m
    assert m.suggested_positioning is None


def test_catalog_json_shape_and_scale_map() -> None:
    """The exported catalog: one row per web brand, positioning ids the
    engine runner reads as a chain scale, scale ↔ id consistent."""
    from app.services.brand_chain_scale import CATALOG_PATH
    from app.services.engine_runner import chain_scale_from_positioning

    doc = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert "scripts/export_brand_catalog.mjs" in doc["_generated_by"]
    by_scale = doc["positioning_by_str_scale"]
    assert set(by_scale) == {
        "Luxury", "Upper Upscale", "Upscale", "Upper Midscale", "Midscale", "Economy",
    }
    names = [b["name"] for b in doc["brands"]]
    assert len(names) == len(set(names)) > 250
    for b in doc["brands"]:
        if b["str_scale"] is None:
            assert b["positioning"] is None
        else:
            assert b["positioning"] == by_scale[b["str_scale"]]
            assert chain_scale_from_positioning(b["positioning"]) == b["positioning"]


# ─────────────────────────── metadata sync ───────────────────────────


async def _create_deal(**body: Any) -> dict[str, Any]:
    async with _client() as client:
        r = await client.post("/deals", json={"name": "OM brand hotel", **body})
        assert r.status_code == 201, r.text
        return r.json()


async def _insert_document(deal: dict[str, Any], filename: str = "Offering_Memorandum.pdf") -> str:
    from app.database import get_session_factory

    doc_id = str(uuid4())
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status) "
                "VALUES (:id, :deal, :tenant, :fn, 'OM', 'EXTRACTED')"
            ),
            {"id": doc_id, "deal": deal["id"], "tenant": deal["tenant_id"], "fn": filename},
        )
        await session.commit()
    return doc_id


async def _sync(deal: dict[str, Any], brand: str, doc_type: str, doc_id: str | None, page: int | None = 4) -> None:
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory

    field: dict[str, Any] = {"field_name": "property_overview.brand", "value": brand}
    if page is not None:
        field["source_page"] = page
    async with get_session_factory()() as session:
        await _sync_deal_metadata_from_extraction(
            session,
            deal_id=deal["id"],
            tenant_id=deal["tenant_id"],
            fields=[field],
            doc_type=doc_type,
            doc_id=doc_id,
        )


async def _get(deal_id: str) -> dict[str, Any]:
    async with _client() as client:
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200, r.text
        return r.json()


async def _positioning_audits(deal_id: str) -> list[dict[str, Any]]:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log WHERE resource_id = :rid "
                    "AND action = 'deal.metadata_synced_from_extraction'"
                ),
                {"rid": deal_id},
            )
        ).all()
    inputs = [json.loads(r._mapping["payload"])["input"] for r in rows]
    return [i for i in inputs if i.get("column") == "positioning"]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [None, "default"])
async def test_om_brand_fills_default_positioning_with_source(initial: str | None) -> None:
    deal = await _create_deal(positioning=initial)
    assert deal["positioning_source"] is None
    doc_id = await _insert_document(deal)

    await _sync(deal, "Kimpton Angler's", "OM", doc_id, page=4)

    got = await _get(deal["id"])
    assert got["positioning"] == "upper-upscale"
    assert got["positioning_source"] == "om_brand_str_scale"
    detail = got["positioning_source_detail"]
    assert detail == {
        "document_id": doc_id,
        "document_filename": "Offering_Memorandum.pdf",
        "page": 4,
        "doc_type": "OM",
        "om_brand": "Kimpton Angler's",
        "catalog_brand": "Kimpton Hotels & Restaurants",
        "catalog_family": "IHG Hotels & Resorts",
        "str_scale": "Upper Upscale",
        "match": "leading_brand_mark",
    }
    audits = await _positioning_audits(deal["id"])
    assert len(audits) == 1
    assert audits[0]["new_value"] == "upper-upscale"
    assert audits[0]["source"] == "om_brand_str_scale"
    assert audits[0]["detail"]["document_id"] == doc_id
    # Listed deals carry it too.
    async with _client() as client:
        listed = {d["id"]: d for d in (await client.get("/deals")).json()}
    assert listed[deal["id"]]["positioning_source"] == "om_brand_str_scale"


@pytest.mark.asyncio
async def test_analyst_positioning_never_overwritten() -> None:
    deal = await _create_deal(positioning="luxury")
    assert deal["positioning_source"] == "analyst"
    doc_id = await _insert_document(deal)

    await _sync(deal, "Kimpton", "OM", doc_id)

    got = await _get(deal["id"])
    assert (got["positioning"], got["positioning_source"]) == ("luxury", "analyst")
    assert got["positioning_source_detail"] is None
    assert await _positioning_audits(deal["id"]) == []


@pytest.mark.asyncio
async def test_legacy_positioning_without_source_is_not_overwritten() -> None:
    """A tier stored before the column existed (source NULL) is still a tier."""
    from app.database import get_session_factory

    deal = await _create_deal()
    async with get_session_factory()() as session:
        await session.execute(
            text("UPDATE deals SET positioning = 'upscale', positioning_source = NULL WHERE id = :id AND tenant_id = :t"),
            {"id": deal["id"], "t": deal["tenant_id"]},
        )
        await session.commit()

    await _sync(deal, "Kimpton", "OM", await _insert_document(deal))

    got = await _get(deal["id"])
    assert (got["positioning"], got["positioning_source"]) == ("upscale", None)


@pytest.mark.asyncio
async def test_ambiguous_or_unscaled_brand_is_a_noop() -> None:
    deal = await _create_deal(positioning="default")
    doc_id = await _insert_document(deal)
    for brand in ("Starwood", "Design Hotels", "Marriott Courtyard", "Miami Beach Boutique"):
        await _sync(deal, brand, "OM", doc_id)

    got = await _get(deal["id"])
    assert (got["positioning"], got["positioning_source"]) == ("default", None)
    assert await _positioning_audits(deal["id"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["T12", "PNL_MONTHLY", "PROPERTY_INFO", "MARKET_STUDY", "STR_TREND", None])
async def test_only_the_om_suggests_positioning(doc_type: str | None) -> None:
    deal = await _create_deal()

    await _sync(deal, "Kimpton", doc_type, None)

    got = await _get(deal["id"])
    assert got["positioning"] is None
    assert got["positioning_source"] is None


@pytest.mark.asyncio
async def test_second_om_does_not_replace_an_om_suggestion() -> None:
    """Fill-only: once a scale is suggested, a later OM's brand does not move it."""
    deal = await _create_deal()
    first = await _insert_document(deal, "OM_v1.pdf")
    await _sync(deal, "Kimpton", "OM", first)
    await _sync(deal, "Hampton by Hilton", "OM", await _insert_document(deal, "OM_v2.pdf"))

    got = await _get(deal["id"])
    assert got["positioning"] == "upper-upscale"
    assert got["positioning_source_detail"]["document_id"] == first


@pytest.mark.asyncio
async def test_missing_page_is_null_not_invented() -> None:
    deal = await _create_deal()
    await _sync(deal, "Kimpton", "OM", await _insert_document(deal), page=None)

    detail = (await _get(deal["id"]))["positioning_source_detail"]
    assert detail["page"] is None


# ─────────────────────────── analyst override ───────────────────────────


@pytest.mark.asyncio
async def test_analyst_override_replaces_om_suggestion_and_default_clears() -> None:
    deal = await _create_deal()
    await _sync(deal, "Kimpton", "OM", await _insert_document(deal))
    assert (await _get(deal["id"]))["positioning_source"] == "om_brand_str_scale"

    async with _client() as client:
        r = await client.patch(f"/deals/{deal['id']}", json={"positioning": "luxury"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["positioning"], body["positioning_source"]) == ("luxury", "analyst")
        assert body["positioning_source_detail"] is None

        # A later OM never takes it back.
        await _sync(deal, "Kimpton", "OM", await _insert_document(deal))
        assert (await _get(deal["id"]))["positioning"] == "luxury"

        # Back to Default = "no tier chosen": the source clears.
        r = await client.patch(f"/deals/{deal['id']}", json={"positioning": "default"})
        assert r.status_code == 200, r.text
        assert (r.json()["positioning"], r.json()["positioning_source"]) == ("default", None)


@pytest.mark.asyncio
async def test_create_with_default_positioning_has_no_source() -> None:
    deal = await _create_deal(positioning="default")
    assert deal["positioning_source"] is None
    assert (await _get(deal["id"]))["positioning_source"] is None


# ──────────────────── best-effort boundary + updated_at ────────────────────


@pytest.mark.asyncio
async def test_catalog_failure_never_blocks_the_om_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """A positioning suggestion is best-effort: when the brand catalog cannot
    be read (missing / malformed ``app/data/brand_chain_scales.json``), the
    metadata sync logs and returns — it never raises into the extraction
    pipeline, which would stamp the OM FAILED."""
    from app.services import brand_chain_scale

    def _broken(_value: object) -> Any:
        raise FileNotFoundError("app/data/brand_chain_scales.json")

    monkeypatch.setattr(brand_chain_scale, "match_brand", _broken)
    deal = await _create_deal()
    doc_id = await _insert_document(deal)

    # Must not raise.
    await _sync(deal, "Kimpton", "OM", doc_id)

    got = await _get(deal["id"])
    assert (got["positioning"], got["positioning_source"]) == (None, None)
    assert await _positioning_audits(deal["id"]) == []

    # And the rest of the sync still runs on the same call: a brand lands.
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        await _sync_deal_metadata_from_extraction(
            session,
            deal_id=deal["id"],
            tenant_id=deal["tenant_id"],
            fields=[
                {"field_name": "property_overview.brand", "value": "Kimpton", "source_page": 4},
                {"field_name": "property_overview.submarket", "value": "Miami Beach"},
            ],
            doc_type="OM",
            doc_id=doc_id,
        )
    got = await _get(deal["id"])
    assert got["positioning_source"] is None
    assert got["city"] == "Miami Beach"


@pytest.mark.asyncio
async def test_positioning_fill_bumps_updated_at() -> None:
    """The deal row changes when the OM's brand fills the tier, so
    ``deals.updated_at`` moves — even when no other metadata column does."""
    from app.database import get_session_factory

    deal = await _create_deal(brand="Kimpton", city="Miami Beach")
    stale = "2020-01-01 00:00:00"
    async with get_session_factory()() as session:
        await session.execute(
            text("UPDATE deals SET updated_at = :ts WHERE id = :id AND tenant_id = :tenant"),
            {"ts": stale, "id": deal["id"], "tenant": deal["tenant_id"]},
        )
        await session.commit()
    before = (await _get(deal["id"]))["updated_at"]
    assert before.startswith("2020-01-01")

    # Brand and city already match → the main sync writes nothing; only the
    # positioning fill touches the row.
    await _sync(deal, "Kimpton", "OM", await _insert_document(deal))

    got = await _get(deal["id"])
    assert got["positioning"] == "upper-upscale"
    assert got["brand"] == "Kimpton" and got["city"] == "Miami Beach"
    assert not got["updated_at"].startswith("2020-01-01"), got["updated_at"]

    # A no-op fill (tier already set) leaves the timestamp alone.
    async with get_session_factory()() as session:
        await session.execute(
            text("UPDATE deals SET updated_at = :ts WHERE id = :id AND tenant_id = :tenant"),
            {"ts": stale, "id": deal["id"], "tenant": deal["tenant_id"]},
        )
        await session.commit()
    await _sync(deal, "Hampton by Hilton", "OM", await _insert_document(deal, "OM_v2.pdf"))
    assert (await _get(deal["id"]))["updated_at"].startswith("2020-01-01")
