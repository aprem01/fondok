"""FON-23 — accept / edit / reject a low-confidence extracted field.

Locks the mutation contract of ``review_extraction_field``:
  * accept → confidence 1.0, reviewed="accepted", leaves low_confidence_fields
  * edit   → value replaced, confidence 1.0, reviewed="edited"
  * reject → field removed
and the confidence report is recomputed each time.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT = "23b0cff3-6f9b-57a9-8d2a-5511f3dd9f7e"


def _auth() -> object:
    from app.auth.context import AuthContext

    return AuthContext(
        tenant_id=UUID(_TENANT),
        user_id="user_analyst",
        role="member",
        source="jwt",
        org_id=None,
        email="analyst@fondok.test",
    )


async def _setup(deal_id: UUID, doc_id: UUID) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        ts = datetime.now(UTC)
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,'d','Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": _TENANT, "ts": ts},
        )
        await s.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                "status, uploaded_at) VALUES (:id,:deal,:t,'pnl.xlsx','PNL','EXTRACTED',:ts)"
            ),
            {"id": str(doc_id), "deal": str(deal_id), "t": _TENANT, "ts": ts},
        )
        fields = [
            {"field_name": "rooms_revenue", "value": 9810000, "confidence": 0.55},
            {"field_name": "adr", "value": 247, "confidence": 0.96},
        ]
        cr = {
            "overall": 0.755,
            "by_field": {"rooms_revenue": 0.55, "adr": 0.96},
            "low_confidence_fields": ["rooms_revenue"],
            "requires_human_review": True,
        }
        await s.execute(
            text(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, "
                "fields, confidence_report, agent_version, created_at) "
                "VALUES (:id,:doc,:deal,:t,:f,:cr,'v1',:ts)"
            ),
            {
                "id": str(uuid4()),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "t": _TENANT,
                "f": json.dumps(fields),
                "cr": json.dumps(cr),
                "ts": ts,
            },
        )
        await s.commit()


async def _call(deal_id, doc_id, **body):
    from app.api.documents import FieldReviewBody, review_extraction_field
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        return await review_extraction_field(
            deal_id=deal_id,
            doc_id=doc_id,
            body=FieldReviewBody(**body),
            session=s,
            auth=_auth(),
        )


@pytest.mark.asyncio
async def test_accept_promotes_to_high_confidence() -> None:
    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    res = await _call(deal_id, doc_id, field_name="rooms_revenue", action="accept")
    rooms = next(f for f in res.fields if f.field_name == "rooms_revenue")
    assert rooms.confidence == 1.0
    assert rooms.reviewed == "accepted"
    assert rooms.value == 9810000  # unchanged
    assert "rooms_revenue" not in res.confidence_report.low_confidence_fields


@pytest.mark.asyncio
async def test_edit_replaces_value_and_promotes() -> None:
    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    res = await _call(
        deal_id, doc_id, field_name="rooms_revenue", action="edit", value=10250000
    )
    rooms = next(f for f in res.fields if f.field_name == "rooms_revenue")
    assert rooms.value == 10250000
    assert rooms.confidence == 1.0
    assert rooms.reviewed == "edited"
    assert "rooms_revenue" not in res.confidence_report.low_confidence_fields


@pytest.mark.asyncio
async def test_edit_without_value_400s() -> None:
    from fastapi import HTTPException

    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    with pytest.raises(HTTPException) as exc:
        await _call(deal_id, doc_id, field_name="rooms_revenue", action="edit")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reject_removes_field() -> None:
    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    res = await _call(deal_id, doc_id, field_name="rooms_revenue", action="reject")
    assert all(f.field_name != "rooms_revenue" for f in res.fields)
    assert "rooms_revenue" not in res.confidence_report.by_field


@pytest.mark.asyncio
async def test_unknown_field_404s() -> None:
    from fastapi import HTTPException

    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    with pytest.raises(HTTPException) as exc:
        await _call(deal_id, doc_id, field_name="nonexistent", action="accept")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_deal_404s() -> None:
    from fastapi import HTTPException

    deal_id, doc_id = uuid4(), uuid4()
    await _setup(deal_id, doc_id)
    # Different deal id the field doesn't belong to → 404, no leak.
    with pytest.raises(HTTPException) as exc:
        await _call(uuid4(), doc_id, field_name="rooms_revenue", action="accept")
    assert exc.value.status_code == 404
