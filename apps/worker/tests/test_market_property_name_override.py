"""FON-59 — Property Name is an analyst override; Project Name is not.

``GET /market/{deal_id}/overview`` resolves ``property_name`` as
analyst override (``field_overrides["property_overview.name"]``) >
document-extracted (OM first) > null, and exposes the extracted value +
its source document as ``property_name_original`` so the override can be
restored. The deal row's ``name`` — the analyst's confidential project
identifier — is never used as a fallback.

DB-backed against a per-file SQLite DB (same bootstrap as
``test_comp_sales.py``): seed a deal + OM extraction row, hit the endpoint
with ``X-Tenant-Id``, assert the additive fields.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-market-property-name.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

from app.api.market import (  # noqa: E402
    PROPERTY_NAME_OVERRIDE_KEY,
    MarketOverview,
    PropertyNameOriginal,
    _property_name_override,
)

EXTRACTED = "Kimpton Angler's South Beach"
PROJECT = "Project Unicorn"


# ─────────────────────────── unit: override shape ───────────────────────────


def test_override_accepts_structured_record() -> None:
    ov = {PROPERTY_NAME_OVERRIDE_KEY: {"value": "The Anglers", "note": "Sam's edit"}}
    assert _property_name_override(ov) == "The Anglers"


def test_override_accepts_bare_string() -> None:
    assert _property_name_override({PROPERTY_NAME_OVERRIDE_KEY: "  The Anglers "}) == "The Anglers"


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", 42, {"value": ""}, {"value": None}, {"note": "no value"}, ["x"]],
)
def test_override_ignores_blank_or_non_string(raw: Any) -> None:
    assert _property_name_override({PROPERTY_NAME_OVERRIDE_KEY: raw}) is None
    assert _property_name_override({}) is None


def test_model_fields_are_additive_and_nullable() -> None:
    m = MarketOverview(deal_id=uuid4())
    assert m.property_name is None
    assert m.property_name_original is None
    assert m.property_name_source is None
    o = PropertyNameOriginal(value=EXTRACTED)
    assert o.doc_name is None and o.page is None


# ─────────────────────────── endpoint materialization ───────────────────────


async def _seed(
    *,
    overrides: dict[str, Any] | None,
    with_extraction: bool,
    page: int | None = 3,
) -> tuple[Any, Any]:
    """Insert a deal (named PROJECT) and, optionally, an OM extraction row
    carrying ``property_overview.name``. Returns (tenant_id, deal_id)."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = uuid4()
    deal_id = uuid4()
    document_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, "
                "purchase_price, service, status, deal_stage, risk, "
                "ai_confidence, field_overrides, created_at, updated_at) "
                "VALUES (:id, :tenant, :name, 'Miami Beach', 132, "
                "36000000, 'Full Service', 'Draft', 'Teaser', 'Medium', "
                "0.8, :fo, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(deal_id),
                "tenant": str(tenant_id),
                "name": PROJECT,
                "fo": json.dumps(overrides or {}),
            },
        )
        if with_extraction:
            await session.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                    "doc_type, status, storage_key, size_bytes) "
                    "VALUES (:id, :deal, :tenant, 'Anglers OM.pdf', 'OM', "
                    "'EXTRACTED', 'om.pdf', 100)"
                ),
                {
                    "id": str(document_id),
                    "deal": str(deal_id),
                    "tenant": str(tenant_id),
                },
            )
            field: dict[str, Any] = {
                "field_name": "property_overview.name",
                "value": EXTRACTED,
            }
            if page is not None:
                field["source_page"] = page
            await session.execute(
                text(
                    "INSERT INTO extraction_results (id, deal_id, document_id, "
                    "tenant_id, fields) "
                    "VALUES (:id, :deal, :doc, :tenant, :fields)"
                ),
                {
                    "id": str(uuid4()),
                    "deal": str(deal_id),
                    "doc": str(document_id),
                    "tenant": str(tenant_id),
                    "fields": json.dumps(
                        [field, {"field_name": "property_overview.year_built", "value": 1962}]
                    ),
                },
            )
        await session.commit()
    return tenant_id, deal_id


async def _overview(tenant_id: Any, deal_id: Any) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/market/{deal_id}/overview",
            headers={"X-Tenant-Id": str(tenant_id)},
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
        return r.json()


@pytest.mark.asyncio
async def test_extracted_name_with_source_doc_and_no_override() -> None:
    tenant_id, deal_id = await _seed(overrides=None, with_extraction=True)
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] == EXTRACTED
    assert body["property_name_source"] == "document"
    assert body["property_name_original"] == {
        "value": EXTRACTED,
        "doc_name": "Anglers OM.pdf",
        "page": 3,
    }
    # Unrelated FON-70 metadata still flows.
    assert body["year_built"] == 1962


@pytest.mark.asyncio
async def test_structured_override_wins_and_original_is_preserved() -> None:
    tenant_id, deal_id = await _seed(
        overrides={
            PROPERTY_NAME_OVERRIDE_KEY: {"value": "The Anglers Hotel", "note": "Analyst"},
        },
        with_extraction=True,
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] == "The Anglers Hotel"
    assert body["property_name_source"] == "analyst_override"
    assert body["property_name_original"]["value"] == EXTRACTED
    assert body["property_name_original"]["doc_name"] == "Anglers OM.pdf"
    assert body["property_name_original"]["page"] == 3


@pytest.mark.asyncio
async def test_bare_string_override_is_honored() -> None:
    tenant_id, deal_id = await _seed(
        overrides={PROPERTY_NAME_OVERRIDE_KEY: "Anglers Beach Club"},
        with_extraction=True,
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] == "Anglers Beach Club"
    assert body["property_name_source"] == "analyst_override"
    assert body["property_name_original"]["value"] == EXTRACTED


@pytest.mark.asyncio
async def test_blank_override_falls_back_to_document() -> None:
    tenant_id, deal_id = await _seed(
        overrides={PROPERTY_NAME_OVERRIDE_KEY: {"value": "   ", "note": ""}},
        with_extraction=True,
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] == EXTRACTED
    assert body["property_name_source"] == "document"


@pytest.mark.asyncio
async def test_nothing_extracted_never_falls_back_to_project_name() -> None:
    tenant_id, deal_id = await _seed(overrides=None, with_extraction=False)
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] is None
    assert body["property_name_original"] is None
    assert body["property_name_source"] is None
    # The project name lives on the deal row only.
    assert PROJECT not in json.dumps(body)


@pytest.mark.asyncio
async def test_override_without_extraction_has_no_original() -> None:
    tenant_id, deal_id = await _seed(
        overrides={PROPERTY_NAME_OVERRIDE_KEY: {"value": "Pre-OM Name", "note": "x"}},
        with_extraction=False,
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_name"] == "Pre-OM Name"
    assert body["property_name_source"] == "analyst_override"
    assert body["property_name_original"] is None


@pytest.mark.asyncio
async def test_original_page_is_null_when_extractor_recorded_none() -> None:
    tenant_id, deal_id = await _seed(overrides=None, with_extraction=True, page=None)
    body = await _overview(tenant_id, deal_id)
    assert body["property_name_original"]["value"] == EXTRACTED
    assert body["property_name_original"]["doc_name"] == "Anglers OM.pdf"
    assert body["property_name_original"]["page"] is None
