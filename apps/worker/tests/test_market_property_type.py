"""FON-59 R-054 — Property Type rides the market overview, Floors never does.

Testers (Eshan / Rani) saw Property Type and Floors blank on the Overview.
The OM extraction carries ``property_overview.property_type`` ("Boutique
Lifestyle Full-Service", confidence 0.92) but the catalog has NO floors /
stories concept — ``number_of_buildings`` (5) is a different fact and is not
a stand-in. So ``GET /market/{deal_id}/overview`` now surfaces
``property_type`` read-only from the extraction (OM first), and deliberately
has no ``floors`` field: the web renders that row as a reasoned dash.

DB-backed against a per-file SQLite DB (same bootstrap as
``test_market_property_name_override.py``).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-market-property-type.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

from app.api.market import MarketOverview  # noqa: E402

PROPERTY_TYPE = "Boutique Lifestyle Full-Service"


def test_model_field_is_additive_and_nullable() -> None:
    m = MarketOverview(deal_id=uuid4())
    assert m.property_type is None
    # No floors field exists on the payload — the catalog has no such concept,
    # so the endpoint must not invent one.
    assert "floors" not in MarketOverview.model_fields
    assert "stories" not in MarketOverview.model_fields


async def _seed(*, fields: list[dict[str, Any]] | None) -> tuple[Any, Any]:
    """Insert a deal (service column deliberately NULL — the testers' deals
    have none) and, when ``fields`` is given, one OM extraction row carrying
    them. Returns (tenant_id, deal_id)."""
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
                "VALUES (:id, :tenant, 'Project Angler', 'Islamorada', 87, "
                "36000000, NULL, 'Draft', 'Teaser', 'Medium', "
                "0.8, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
        if fields is not None:
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
                    "fields": json.dumps(fields),
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
async def test_property_type_is_read_from_the_om_extraction() -> None:
    tenant_id, deal_id = await _seed(
        fields=[
            {"field_name": "property_overview.name", "value": "Angler's Hotel"},
            {
                "field_name": "property_overview.property_type",
                "value": PROPERTY_TYPE,
                "confidence": 0.92,
            },
            {"field_name": "property_overview.number_of_buildings", "value": 5},
        ]
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_type"] == PROPERTY_TYPE
    # The deal row's service column is NULL and stays NULL — the extraction
    # is the source, not a fallback dressed up as one.
    assert body["service"] is None
    # Buildings are not floors; nothing about floors is invented.
    assert "floors" not in body
    assert "stories" not in body
    assert "number_of_buildings" not in body


@pytest.mark.asyncio
async def test_property_type_is_null_when_the_om_does_not_state_it() -> None:
    tenant_id, deal_id = await _seed(
        fields=[{"field_name": "property_overview.name", "value": "Angler's Hotel"}]
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_type"] is None


@pytest.mark.asyncio
async def test_blank_property_type_is_treated_as_absent() -> None:
    tenant_id, deal_id = await _seed(
        fields=[{"field_name": "property_overview.property_type", "value": "   "}]
    )
    body = await _overview(tenant_id, deal_id)
    assert body["property_type"] is None


@pytest.mark.asyncio
async def test_no_documents_means_no_property_type() -> None:
    tenant_id, deal_id = await _seed(fields=None)
    body = await _overview(tenant_id, deal_id)
    assert body["property_type"] is None
