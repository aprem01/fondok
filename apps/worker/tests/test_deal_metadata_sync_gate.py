"""FON-84 — doc-type gate on the extraction → deals metadata sync.

``_sync_deal_metadata_from_extraction`` used to run after EVERY
extraction for EVERY document type and copy any ``property_overview.*``
field onto the ``deals`` row, so the last document to finish won. Live
evidence on two tester deals: a CoStar market report (user tag
STR_TREND, router lane MARKET_STUDY) emitted a ``property_overview``
block describing a COMP hotel ("Rosewood The Raleigh", 60 keys) and the
sync wrote keys 132 → 60 and brand → "Rosewood Hotel Group". The OM
wrote its full street address into ``city`` and replaced the analyst's
PROPOSED brand ("Thompson Hotels") with the existing flag ("Kimpton").

The rules under test:

* only subject-property doc types may write at all — OM, T12, PNL,
  PNL_MONTHLY, PNL_YTD, ROOM_MIX, PROPERTY_INFO; everything else
  (MARKET_STUDY, STR_TREND, comps, CAPEX, INSURANCE, LEASES, unknown /
  ``None``) is a no-op with no audit row;
* ``keys`` — the OM may overwrite a differing value; non-OM allowlisted
  types may only fill an empty / zero value;
* ``brand`` — fill-if-empty only, for every doc type including the OM;
* ``city`` — ``property_overview.address`` is never mapped; ``submarket``
  / ``location`` may fill an empty city only, and only from the OM;
* every applied change gets an audit row carrying ``doc_type`` + ``mode``.
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

# Force a per-module SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN (same pattern as the sibling
# documents tests).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deal-metadata-sync-gate.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-00000000f084"

# A ``property_overview`` block shaped like the one the CoStar report
# emitted on the live tester deal — it describes a comp, not the subject.
_COMP_HOTEL_FIELDS: list[dict[str, Any]] = [
    {"field_name": "property_overview.keys", "value": 60, "source_page": 3},
    {"field_name": "property_overview.brand", "value": "Rosewood Hotel Group"},
    {"field_name": "property_overview.submarket", "value": "Miami Airport"},
    {"field_name": "property_overview.location", "value": "Miami Airport"},
]


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema and empty the two tables every test touches."""
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


# ─────────────────────────────── helpers ───────────────────────────────


async def _insert_deal(
    *,
    keys: int | None = None,
    brand: str | None = None,
    city: str | None = None,
) -> str:
    from app.database import get_session_factory

    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, keys, brand, city)
                VALUES (:id, :tenant, 'FON-84 deal', 'Draft', :keys, :brand, :city)
                """
            ),
            {
                "id": deal_id,
                "tenant": _TENANT,
                "keys": keys,
                "brand": brand,
                "city": city,
            },
        )
        await session.commit()
    return deal_id


async def _read_deal(deal_id: str) -> tuple[int | None, str | None, str | None]:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT keys, brand, city FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
    assert row is not None
    m = row._mapping
    return m["keys"], m["brand"], m["city"]


async def _run_sync(
    deal_id: str,
    fields: list[dict[str, Any]],
    doc_type: str | None,
) -> None:
    """Invoke the sync exactly as the extraction-complete path does."""
    from app.api.documents import _sync_deal_metadata_from_extraction
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await _sync_deal_metadata_from_extraction(
            session,
            deal_id=deal_id,
            tenant_id=_TENANT,
            fields=fields,
            doc_type=doc_type,
        )


async def _audit_inputs(deal_id: str) -> list[dict[str, Any]]:
    """``input_payload`` of every metadata-sync audit row for the deal."""
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT payload FROM audit_log
                     WHERE resource_id = :rid
                       AND action = 'deal.metadata_synced_from_extraction'
                     ORDER BY rowid
                    """
                ),
                {"rid": deal_id},
            )
        ).all()
    return [json.loads(r._mapping["payload"])["input"] for r in rows]


def _field(name: str, value: Any) -> dict[str, Any]:
    return {"field_name": name, "value": value}


# ─────────────────────────── doc-type gate ───────────────────────────


@pytest.mark.asyncio
async def test_market_study_cannot_write_any_column() -> None:
    """The live bug: a MARKET_STUDY's comp-hotel block must not touch the deal."""
    deal_id = await _insert_deal(keys=132, brand="Thompson Hotels", city="Miami")

    await _run_sync(deal_id, _COMP_HOTEL_FIELDS, "MARKET_STUDY")

    assert await _read_deal(deal_id) == (132, "Thompson Hotels", "Miami")
    assert await _audit_inputs(deal_id) == []


@pytest.mark.asyncio
async def test_str_trend_cannot_write_any_column() -> None:
    """The user-tagged STR_TREND lane is gated just like MARKET_STUDY."""
    deal_id = await _insert_deal(keys=132, brand="Thompson Hotels", city="Miami")

    await _run_sync(deal_id, _COMP_HOTEL_FIELDS, "STR_TREND")

    assert await _read_deal(deal_id) == (132, "Thompson Hotels", "Miami")
    assert await _audit_inputs(deal_id) == []


@pytest.mark.asyncio
async def test_market_study_cannot_even_fill_empty_columns() -> None:
    """Gated types are a full no-op — they may not fill empties either."""
    deal_id = await _insert_deal(keys=None, brand=None, city=None)

    await _run_sync(deal_id, _COMP_HOTEL_FIELDS, "MARKET_STUDY")

    assert await _read_deal(deal_id) == (None, None, None)
    assert await _audit_inputs(deal_id) == []


@pytest.mark.parametrize(
    "doc_type",
    [
        None,
        "",
        "UNKNOWN",
        "CAPEX",
        "INSURANCE",
        "LEASES",
        "COMPS",
        "SALES_COMPS",
        "CBRE_HORIZONS",
        "PNL_BENCHMARK",
        "STR",
        "OTHER",
    ],
)
@pytest.mark.asyncio
async def test_non_subject_doc_types_are_skipped(doc_type: str | None) -> None:
    deal_id = await _insert_deal(keys=132, brand="Thompson Hotels", city="Miami")

    await _run_sync(deal_id, _COMP_HOTEL_FIELDS, doc_type)

    assert await _read_deal(deal_id) == (132, "Thompson Hotels", "Miami")
    assert await _audit_inputs(deal_id) == []


@pytest.mark.asyncio
async def test_skip_is_logged_with_doc_type(caplog: pytest.LogCaptureFixture) -> None:
    deal_id = await _insert_deal(keys=132)

    with caplog.at_level("INFO", logger="app.api.documents"):
        await _run_sync(deal_id, _COMP_HOTEL_FIELDS, "MARKET_STUDY")

    assert any(
        "deal_metadata_sync: skipped doc_type=MARKET_STUDY" in rec.getMessage()
        for rec in caplog.records
    )


# ─────────────────────────────── keys ───────────────────────────────


@pytest.mark.asyncio
async def test_keys_fill_then_om_overwrite_then_pnl_cannot_overwrite() -> None:
    """The full keys lifecycle from the spec, in order, on one deal."""
    deal_id = await _insert_deal(keys=None)

    # PNL on an empty deal → fills 132.
    await _run_sync(deal_id, [_field("property_overview.keys", 132)], "PNL")
    assert (await _read_deal(deal_id))[0] == 132
    audits = await _audit_inputs(deal_id)
    assert len(audits) == 1
    assert audits[0]["column"] == "keys"
    assert audits[0]["old_value"] is None
    assert audits[0]["new_value"] == "132"
    assert audits[0]["doc_type"] == "PNL"
    assert audits[0]["mode"] == "fill"

    # OM with the same value → no change, no new audit row.
    await _run_sync(deal_id, [_field("property_overview.keys", 132)], "OM")
    assert (await _read_deal(deal_id))[0] == 132
    assert len(await _audit_inputs(deal_id)) == 1

    # OM with a differing value → overwritten (docs > wizard).
    await _run_sync(deal_id, [_field("property_overview.keys", 140)], "OM")
    assert (await _read_deal(deal_id))[0] == 140
    audits = await _audit_inputs(deal_id)
    assert len(audits) == 2
    assert audits[1]["column"] == "keys"
    assert audits[1]["old_value"] == "132"
    assert audits[1]["new_value"] == "140"
    assert audits[1]["doc_type"] == "OM"
    assert audits[1]["mode"] == "overwrite"

    # PNL with yet another value → NOT overwritten; no new audit row.
    await _run_sync(deal_id, [_field("property_overview.keys", 150)], "PNL")
    assert (await _read_deal(deal_id))[0] == 140
    assert len(await _audit_inputs(deal_id)) == 2


@pytest.mark.parametrize(
    "doc_type", ["T12", "PNL", "PNL_MONTHLY", "PNL_YTD", "ROOM_MIX", "PROPERTY_INFO"]
)
@pytest.mark.asyncio
async def test_non_om_allowlisted_types_fill_empty_keys_only(doc_type: str) -> None:
    # Fill on NULL.
    deal_id = await _insert_deal(keys=None)
    await _run_sync(deal_id, [_field("property_overview.keys", 132)], doc_type)
    assert (await _read_deal(deal_id))[0] == 132
    audits = await _audit_inputs(deal_id)
    assert [(a["mode"], a["doc_type"]) for a in audits] == [("fill", doc_type)]

    # No overwrite of a non-empty value.
    await _run_sync(deal_id, [_field("property_overview.keys", 999)], doc_type)
    assert (await _read_deal(deal_id))[0] == 132
    assert len(await _audit_inputs(deal_id)) == 1


@pytest.mark.asyncio
async def test_zero_keys_counts_as_empty_for_fill() -> None:
    deal_id = await _insert_deal(keys=0)

    await _run_sync(deal_id, [_field("property_overview.keys", "132")], "T12")

    assert (await _read_deal(deal_id))[0] == 132
    assert [a["mode"] for a in await _audit_inputs(deal_id)] == ["fill"]


@pytest.mark.parametrize("doc_type", ["om", "T-12", "pnl monthly", "PNL_YTD "])
@pytest.mark.asyncio
async def test_doc_type_is_compared_in_canonical_form(doc_type: str) -> None:
    """Legacy / external surface forms collapse via ``_canonical_doc_type``."""
    deal_id = await _insert_deal(keys=None)

    await _run_sync(deal_id, [_field("property_overview.keys", 132)], doc_type)

    assert (await _read_deal(deal_id))[0] == 132


# ─────────────────────────────── brand ───────────────────────────────


@pytest.mark.asyncio
async def test_om_brand_never_overwrites_analyst_brand() -> None:
    """The wizard brand is the PROPOSED brand; the OM's existing flag must not win."""
    deal_id = await _insert_deal(brand="Thompson Hotels")

    await _run_sync(deal_id, [_field("property_overview.brand", "Kimpton")], "OM")

    assert (await _read_deal(deal_id))[1] == "Thompson Hotels"
    assert await _audit_inputs(deal_id) == []


@pytest.mark.asyncio
async def test_om_brand_fills_empty_brand() -> None:
    deal_id = await _insert_deal(brand=None)

    await _run_sync(deal_id, [_field("property_overview.brand", "Kimpton")], "OM")

    assert (await _read_deal(deal_id))[1] == "Kimpton"
    audits = await _audit_inputs(deal_id)
    assert len(audits) == 1
    assert audits[0]["column"] == "brand"
    assert audits[0]["old_value"] is None
    assert audits[0]["new_value"] == "Kimpton"
    assert audits[0]["doc_type"] == "OM"
    assert audits[0]["mode"] == "fill"


@pytest.mark.asyncio
async def test_blank_brand_counts_as_empty_for_fill() -> None:
    deal_id = await _insert_deal(brand="   ")

    await _run_sync(deal_id, [_field("property_overview.brand", "Kimpton")], "T12")

    assert (await _read_deal(deal_id))[1] == "Kimpton"


@pytest.mark.asyncio
async def test_non_om_brand_never_overwrites() -> None:
    deal_id = await _insert_deal(brand="Thompson Hotels")

    await _run_sync(deal_id, [_field("property_overview.brand", "Kimpton")], "T12")

    assert (await _read_deal(deal_id))[1] == "Thompson Hotels"
    assert await _audit_inputs(deal_id) == []


# ─────────────────────────────── city ───────────────────────────────


@pytest.mark.asyncio
async def test_om_address_never_lands_in_city() -> None:
    """A street address is not a city; the mapping was removed outright."""
    deal_id = await _insert_deal(city=None)

    await _run_sync(
        deal_id,
        [_field("property_overview.address", "1234 Collins Ave, Miami Beach, FL 33139")],
        "OM",
    )

    assert (await _read_deal(deal_id))[2] is None
    assert await _audit_inputs(deal_id) == []


@pytest.mark.asyncio
async def test_om_submarket_fills_empty_city() -> None:
    deal_id = await _insert_deal(city=None)

    await _run_sync(deal_id, [_field("property_overview.submarket", "South Beach")], "OM")

    assert (await _read_deal(deal_id))[2] == "South Beach"
    audits = await _audit_inputs(deal_id)
    assert len(audits) == 1
    assert audits[0]["column"] == "city"
    assert audits[0]["old_value"] is None
    assert audits[0]["new_value"] == "South Beach"
    assert audits[0]["doc_type"] == "OM"
    assert audits[0]["mode"] == "fill"


@pytest.mark.asyncio
async def test_om_location_fills_empty_city() -> None:
    deal_id = await _insert_deal(city=None)

    await _run_sync(deal_id, [_field("property_overview.location", "Miami Beach")], "OM")

    assert (await _read_deal(deal_id))[2] == "Miami Beach"


@pytest.mark.asyncio
async def test_om_submarket_never_overwrites_city() -> None:
    deal_id = await _insert_deal(city="Miami")

    await _run_sync(deal_id, [_field("property_overview.submarket", "Miami Airport")], "OM")

    assert (await _read_deal(deal_id))[2] == "Miami"
    assert await _audit_inputs(deal_id) == []


@pytest.mark.parametrize("doc_type", ["T12", "PNL", "PROPERTY_INFO", "ROOM_MIX"])
@pytest.mark.asyncio
async def test_non_om_cannot_fill_city(doc_type: str) -> None:
    """``city`` is OM-only, even in fill mode."""
    deal_id = await _insert_deal(city=None)

    await _run_sync(
        deal_id,
        [
            _field("property_overview.submarket", "South Beach"),
            _field("property_overview.location", "Miami Beach"),
        ],
        doc_type,
    )

    assert (await _read_deal(deal_id))[2] is None
    assert await _audit_inputs(deal_id) == []


# ─────────────────────── audit provenance / combined ───────────────────────


@pytest.mark.asyncio
async def test_om_on_fresh_deal_fills_all_three_with_one_audit_row_each() -> None:
    """A real OM on a wizard-only deal: keys + brand + city filled, each audited
    with the FON-84 provenance keys, and the address token is ignored."""
    deal_id = await _insert_deal(keys=None, brand=None, city=None)

    await _run_sync(
        deal_id,
        [
            _field("property_overview.keys", 132),
            _field("property_overview.brand", "Kimpton"),
            _field("property_overview.address", "1234 Collins Ave, Miami Beach, FL"),
            _field("property_overview.submarket", "South Beach"),
            _field("property_overview.location", "Miami Beach"),
        ],
        "OM",
    )

    assert await _read_deal(deal_id) == (132, "Kimpton", "South Beach")
    audits = await _audit_inputs(deal_id)
    assert {a["column"] for a in audits} == {"keys", "brand", "city"}
    for a in audits:
        assert a["doc_type"] == "OM"
        assert a["mode"] == "fill"
        assert a["old_value"] is None


@pytest.mark.asyncio
async def test_om_on_populated_deal_overwrites_keys_only() -> None:
    """Live scenario: analyst wizard = 132 keys / Thompson Hotels / Miami;
    OM says 140 keys / Kimpton / Miami Beach. Only keys may change."""
    deal_id = await _insert_deal(keys=132, brand="Thompson Hotels", city="Miami")

    await _run_sync(
        deal_id,
        [
            _field("property_overview.keys", 140),
            _field("property_overview.brand", "Kimpton"),
            _field("property_overview.submarket", "Miami Beach"),
        ],
        "OM",
    )

    assert await _read_deal(deal_id) == (140, "Thompson Hotels", "Miami")
    audits = await _audit_inputs(deal_id)
    assert len(audits) == 1
    assert audits[0]["column"] == "keys"
    assert audits[0]["doc_type"] == "OM"
    assert audits[0]["mode"] == "overwrite"
    assert audits[0]["old_value"] == "132"
    assert audits[0]["new_value"] == "140"


@pytest.mark.asyncio
async def test_sync_is_best_effort_on_unknown_deal() -> None:
    """A deal id that doesn't exist (or isn't a UUID) is a silent no-op."""
    await _run_sync(str(uuid4()), [_field("property_overview.keys", 132)], "OM")
    await _run_sync("not-a-uuid", [_field("property_overview.keys", 132)], "OM")
