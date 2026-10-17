"""Security guard (2026-10-15).

Two production holes found in browser QA follow-up:

* ``GET /deals/{id}/search`` had NO auth dependency — anyone holding a deal
  UUID could full-text search that deal's document text without a session.
  The cross-tenant catalog listed the route but called it without the
  required ``q`` parameter, so FastAPI's 422 counted as a "pass".
* ``POST /deals/{id}/documents/{doc}/extract`` and ``…/reprocess`` resolved
  no caller: the document lookup was by (id, deal_id) only.

This module (1) proves each route now refuses another tenant with a REAL
request (required params present), and (2) enumerates every mounted route
and fails when one ships without an auth / tenant dependency unless it is on
the reviewed allowlist below.
"""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("ALLOW_TENANT_HEADER_WITHOUT_JWT", "1")

# Routes that are deliberately reachable without a caller. Each needs a
# reason; adding to this list is a security review, not a test fix.
ALLOWED_UNAUTHENTICATED: dict[tuple[str, str], str] = {
    ("GET", "/health"): "liveness probe, no tenant data",
    ("GET", "/deals/{deal_id}/documents/{doc_id}/download/signed"): "HMAC-signed, expiring path",
    ("POST", "/webhooks/clerk"): "Svix signature verified in the handler; refuses without a secret",
    ("GET", "/ontology/concepts"): "static concept registry shipped with the code",
}

_AUTH_DEP_NAMES = {"get_tenant_id", "get_current_auth"}


def _dep_names(dependant, acc: set[str]) -> set[str]:
    for d in dependant.dependencies:
        acc.add(getattr(d.call, "__name__", repr(d.call)))
        _dep_names(d, acc)
    return acc


def test_every_route_has_auth_or_is_allowlisted() -> None:
    from fastapi.routing import APIRoute

    from app.main import app

    offenders = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        names = _dep_names(r.dependant, set())
        has_auth = bool(names & _AUTH_DEP_NAMES) or any(
            "role" in n or "tenant" in n or "auth" in n for n in names
        )
        if has_auth:
            continue
        for m in sorted(r.methods):
            if (m, r.path) not in ALLOWED_UNAUTHENTICATED:
                offenders.append(f"{m} {r.path}")
    assert offenders == [], (
        "Routes with no auth/tenant dependency (add Depends(get_tenant_id) or, "
        f"after a security review, an ALLOWED_UNAUTHENTICATED entry): {offenders}"
    )


@pytest.mark.asyncio
async def test_search_extract_reprocess_refuse_another_tenant() -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/deals", json={"name": "Search Guard", "city": "Miami, FL", "keys": 100},
                         headers={"X-Tenant-Id": tenant_a})
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]

        doc_id = str(uuid.uuid4())
        async with get_session_factory()() as s:
            await s.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
                    "VALUES (:id, :deal, :t, 'om.pdf', 'OM', 'FAILED', CURRENT_TIMESTAMP)"
                ),
                {"id": doc_id, "deal": deal_id, "t": tenant_a},
            )
            await s.commit()

        # Owner can search (SQLite has no FTS table rows → empty hits, but 200).
        own = await c.get(f"/deals/{deal_id}/search", params={"q": "revenue"},
                          headers={"X-Tenant-Id": tenant_a})
        assert own.status_code == 200, own.text

        # Another tenant gets 404 on all three, with required params present.
        s = await c.get(f"/deals/{deal_id}/search", params={"q": "revenue"},
                        headers={"X-Tenant-Id": tenant_b})
        assert s.status_code == 404, s.text
        e = await c.post(f"/deals/{deal_id}/documents/{doc_id}/extract",
                         headers={"X-Tenant-Id": tenant_b})
        assert e.status_code == 404, e.text
        rp = await c.post(f"/deals/{deal_id}/documents/{doc_id}/reprocess",
                          headers={"X-Tenant-Id": tenant_b})
        assert rp.status_code == 404, rp.text

    # The foreign extract/reprocess attempts changed nothing.
    async with get_session_factory()() as s:
        row = (await s.execute(text("SELECT status FROM documents WHERE id = :id AND tenant_id = :t"),
                               {"id": doc_id, "t": tenant_a})).first()
    assert row[0] == "FAILED"
