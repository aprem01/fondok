"""FON-20 — "document <id> not found on deal <deal>" when opening the review
of a document the Data Room lists as EXTRACTED.

What this file pins
-------------------
1. The happy path the ticket describes, end to end on SQLite with the mock
   extractor: upload → (auto) extract → list shows EXTRACTED → open the
   review (``GET /extraction``, ``PATCH /fields``, ``GET /download-url``)
   under the SAME tenant → every per-document route answers 200. The list
   and every per-document route resolve the tenant through one dependency
   (``get_tenant_id``) and filter on the same three columns
   (``id`` / ``deal_id`` / ``tenant_id``), so a document the list returns
   is a document the review can open.

2. The one way a document CAN be listed-but-unopenable: a ``documents`` row
   whose ``tenant_id`` differs from its deal's. The July root cause (an
   org-less Clerk token silently resolving ``DEFAULT_TENANT_ID``) wrote
   exactly such rows, because ``POST /upload`` never checked that the deal
   belongs to the caller's tenant. Upload now fails closed (404) so the
   invariant ``documents.tenant_id == deals.tenant_id`` cannot be broken
   again at the write site.

3. The 404 detail names WHICH lookup failed, so the next report says
   "belongs to deal X" / "no documents row" / "no extraction_results row"
   instead of a bare "not found on deal". Nothing about another tenant's
   data is ever named: a row on another tenant reads like a missing row.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "fon20_review.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault(
    "DOCUMENT_STORAGE_ROOT", str(Path(tempfile.mkdtemp()) / "fon20-storage")
)


# ─────────────────────────── helpers ───────────────────────────


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _default_tenant() -> str:
    from app.config import get_settings

    return get_settings().DEFAULT_TENANT_ID


def _headers(tenant: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant}


def _pdf_bytes() -> bytes:
    """Reuse the reportlab T-12 fixture builder (``test_documents``) so the
    mock extractor's citation verifier has real page text to match."""
    from tests.test_documents import _build_sample_pdf

    return _build_sample_pdf()


async def _insert_deal(*, tenant_id: str, name: str) -> str:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                """
            ),
            {"id": deal_id, "tenant": tenant_id, "name": name, "ts": "2026-10-14 00:00:00"},
        )
        await session.commit()
    return deal_id


async def _upload_and_extract(client, *, deal_id: str, tenant: str, name: str) -> str:
    """Upload one PDF and poll ``/extraction`` until the mock run lands."""
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    r = await client.post(
        f"/deals/{deal_id}/documents/upload",
        files={"files": (name, _pdf_bytes(), "application/pdf")},
        headers=_headers(tenant),
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()[0]["id"]
    final = None
    for _ in range(100):
        r = await client.get(
            f"/deals/{deal_id}/documents/{doc_id}/extraction", headers=_headers(tenant)
        )
        assert r.status_code == 200, r.text
        final = r.json()["status"]
        if final in ("EXTRACTED", "FAILED", "PARSE_FAILED"):
            break
        await asyncio.sleep(0.1)
    assert final == "EXTRACTED", f"expected EXTRACTED, got {final}: {r.json()}"
    return doc_id


async def _tenant_mismatched_documents() -> list[tuple[str, str, str]]:
    """Every documents row whose tenant differs from its deal's — the one
    data shape that makes a listed document unopenable."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT d.id, d.tenant_id, x.tenant_id
                      FROM documents d
                      JOIN deals x ON x.id = d.deal_id
                     WHERE d.tenant_id <> x.tenant_id
                    """
                )
            )
        ).fetchall()
    return [(str(a), str(b), str(c)) for a, b, c in rows]


# ─────────────────────────── 1. the ticket's path ───────────────────────────


@pytest.mark.asyncio
async def test_extracted_document_opens_for_review_on_its_deal() -> None:
    """Upload → EXTRACTED in the list → open the review: 200 everywhere."""
    tenant = _default_tenant()
    deal_id = await _insert_deal(tenant_id=tenant, name="FON-20 review")

    async with _client() as client:
        doc_id = await _upload_and_extract(
            client, deal_id=deal_id, tenant=tenant, name="sample_t12.pdf"
        )

        # The Data Room list shows the document as EXTRACTED …
        r = await client.get(f"/deals/{deal_id}/documents", headers=_headers(tenant))
        assert r.status_code == 200, r.text
        listed = {d["id"]: d for d in r.json()}
        assert doc_id in listed
        assert listed[doc_id]["status"] == "EXTRACTED"

        # … and every route the review surface calls finds it on the deal.
        r = await client.get(
            f"/deals/{deal_id}/documents/{doc_id}/extraction", headers=_headers(tenant)
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EXTRACTED" and body["fields"]
        field_name = body["fields"][0]["field_name"]

        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/fields",
            json={"field_name": field_name, "action": "accept"},
            headers=_headers(tenant),
        )
        assert r.status_code == 200, r.text
        accepted = next(f for f in r.json()["fields"] if f["field_name"] == field_name)
        assert accepted["reviewed"] == "accepted"

        r = await client.get(
            f"/deals/{deal_id}/documents/{doc_id}/download-url", headers=_headers(tenant)
        )
        assert r.status_code == 200, r.text

    # The write path kept the invariant the review depends on.
    assert await _tenant_mismatched_documents() == []


# ───────────── 2. the root-cause class: a document on the wrong tenant ─────────────


@pytest.mark.asyncio
async def test_upload_to_a_deal_on_another_tenant_is_refused() -> None:
    """The caller's tenant must own the deal it uploads to. Before this the
    row was written with the CALLER's tenant on SOMEONE ELSE's deal — the
    exact shape behind the July "not found on deal" reports (an org-less
    token resolved to DEFAULT_TENANT_ID and uploaded onto the org's deal).
    404, not 403: the deal's existence is not confirmed to a stranger."""
    owner = _default_tenant()
    stranger = str(uuid4())
    deal_id = await _insert_deal(tenant_id=owner, name="Owned by default tenant")

    async with _client() as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files={"files": ("t12.pdf", _pdf_bytes(), "application/pdf")},
            headers=_headers(stranger),
        )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == f"deal {deal_id} not found"
    assert await _tenant_mismatched_documents() == []


# ───────────── 3. the 404 detail names the lookup that failed ─────────────


@pytest.mark.asyncio
async def test_review_404_detail_names_the_failed_lookup(caplog: pytest.LogCaptureFixture) -> None:
    """The diagnosis (which lookup failed) is operator intel: it goes to the
    log at WARNING; the client gets one sentence with no ids in it (the raw
    body used to reach every toast in the web app — client sweep 2026-10-17)."""
    import logging

    from app.api.documents import DOCUMENT_NOT_FOUND_DETAIL, EXTRACTION_NOT_FOUND_DETAIL

    tenant = _default_tenant()
    deal_a = await _insert_deal(tenant_id=tenant, name="Deal A")
    deal_b = await _insert_deal(tenant_id=tenant, name="Deal B")

    async with _client() as client:
        doc_id = await _upload_and_extract(
            client, deal_id=deal_a, tenant=tenant, name="sample_t12.pdf"
        )

        # (a) Right document, wrong deal, same tenant → the LOG says which
        #     deal it is on; the client sees the bare sentence.
        for route in ("extraction", "download-url", "download"):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="app.api.documents"):
                r = await client.get(
                    f"/deals/{deal_b}/documents/{doc_id}/{route}", headers=_headers(tenant)
                )
            assert r.status_code == 404, (route, r.text)
            detail = r.json()["detail"]
            assert detail == DOCUMENT_NOT_FOUND_DETAIL, detail
            assert str(doc_id) not in detail and str(deal_b) not in detail
            assert f"belongs to deal {deal_a}" in caplog.text, caplog.text
        for patch_route, body in (
            ("fields", {"field_name": "noi_year_1", "action": "accept"}),
            ("classification", {"doc_type": "OM"}),
        ):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="app.api.documents"):
                r = await client.patch(
                    f"/deals/{deal_b}/documents/{doc_id}/{patch_route}",
                    json=body,
                    headers=_headers(tenant),
                )
            assert r.status_code == 404, r.text
            assert r.json()["detail"] in (DOCUMENT_NOT_FOUND_DETAIL, EXTRACTION_NOT_FOUND_DETAIL), r.text
            assert f"belongs to deal {deal_a}" in caplog.text, caplog.text

        # (b) A document id nobody has → the log says the documents row is
        #     missing; the client sentence is unchanged.
        ghost = uuid4()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.api.documents"):
            r = await client.get(
                f"/deals/{deal_a}/documents/{ghost}/extraction", headers=_headers(tenant)
            )
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == DOCUMENT_NOT_FOUND_DETAIL, r.text
        assert "no documents row" in caplog.text, caplog.text

        # (c) Another tenant asking about the real document → nothing of
        #     ours is named anywhere a client can read: not the deal it is
        #     on, not that it exists.
        stranger = str(uuid4())
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.api.documents"):
            r = await client.get(
                f"/deals/{deal_a}/documents/{doc_id}/extraction", headers=_headers(stranger)
            )
        assert r.status_code == 404, r.text
        detail = r.json()["detail"]
        assert detail == DOCUMENT_NOT_FOUND_DETAIL, detail
        assert "belongs to deal" not in caplog.text and "status=" not in caplog.text, caplog.text
        assert "not on this tenant" in caplog.text, caplog.text

        # (d) Document present, extraction row absent → the review PATCH
        #     says it is the EXTRACTION lookup that failed.
        from sqlalchemy import text

        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("DELETE FROM extraction_results WHERE document_id = :d"),
                {"d": doc_id},
            )
            await session.commit()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.api.documents"):
            r = await client.patch(
                f"/deals/{deal_a}/documents/{doc_id}/fields",
                json={"field_name": "noi_year_1", "action": "accept"},
                headers=_headers(tenant),
            )
        assert r.status_code == 404, r.text
        detail = r.json()["detail"]
        assert detail == EXTRACTION_NOT_FOUND_DETAIL, detail
        assert "no extraction_results row" in caplog.text, caplog.text
        assert "status=EXTRACTED" in caplog.text, caplog.text
