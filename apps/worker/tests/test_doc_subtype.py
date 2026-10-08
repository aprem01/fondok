"""FON-41 / R-036 — Future CapEx as a real document category.

The wizard's ``future_capex`` and ``capex`` slots both upload as doc_type
``CAPEX``; the per-file ``user_doc_subtypes[]`` form field (index-aligned
with ``files[]`` like ``fiscal_years[]``) lands on ``documents.doc_subtype``
as ``future`` | ``historic`` | NULL. No new DocType value — the Router,
extractor and engines keep reading plain ``CAPEX``.

Covered:

* the startup migration adds ``documents.doc_subtype``;
* upload persists subtypes positionally, normalizes case, and drops a
  subtype that is unknown or sits on a non-CAPEX doc_type;
* the list payload exposes ``doc_subtype``;
* reclassify sets / clears it, rejects an invalid one, and clears it when
  the doc moves off CAPEX;
* a schema WITHOUT the column (migration not yet run) still serves upload,
  list and reclassify — ``doc_subtype`` reads NULL, writes are skipped.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-doc-subtype.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-doc-subtype-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

TENANT = "11111111-1111-1111-1111-1111aaaaaaaa"
HEADERS = {"X-Tenant-Id": TENANT}


def _pdf(label: str) -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.drawString(72, 720, f"Capital plan — {label} — {uuid4().hex}")
    c.showPage()
    c.save()
    return buf.getvalue()


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.storage import reset_raw_store_cache

    await run_startup_migrations()
    reset_raw_store_cache()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _seed_deal() -> str:
    from app.database import get_session_factory

    deal_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, "
                "updated_at) VALUES (:id, :t, 'CapEx Hotel', 'Draft', :ts, :ts)"
            ),
            {"id": deal_id, "t": TENANT, "ts": now},
        )
        await session.commit()
    return deal_id


async def _seed_document(
    deal_id: str, *, doc_type: str = "CAPEX", subtype: str | None = None
) -> str:
    from app.database import get_session_factory

    doc_id = str(uuid4())
    async with get_session_factory()() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, size_bytes,
                    user_provided_doc_type, misclassified, doc_subtype
                ) VALUES (
                    :id, :deal, :t, 'capex.xlsx', :dt, 'EXTRACTED', :ts, :ch,
                    1024, :dt, 0, :st
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "t": TENANT,
                "dt": doc_type,
                "ts": datetime.now(UTC).isoformat(),
                "ch": uuid4().hex,
                "st": subtype,
            },
        )
        await session.commit()
    return doc_id


async def _column_exists() -> bool:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        rows = (await session.execute(text("PRAGMA table_info(documents)"))).all()
    return any(r[1] == "doc_subtype" for r in rows)


async def _stored_subtype(doc_id: str) -> str | None:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        return (
            await session.execute(
                text("SELECT doc_subtype FROM documents WHERE id = :id"),
                {"id": doc_id},
            )
        ).scalar_one()


# ─────────────────────────── migration ───────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_doc_subtype_column() -> None:
    assert await _column_exists()


# ─────────────────────────── upload ───────────────────────────


@pytest.mark.asyncio
async def test_upload_persists_subtypes_index_aligned() -> None:
    deal_id = await _seed_deal()
    async with _client() as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            headers=HEADERS,
            files=[
                ("files", ("pip_budget.pdf", _pdf("future"), "application/pdf")),
                ("files", ("capex_hist.pdf", _pdf("historic"), "application/pdf")),
                ("files", ("capex_untagged.pdf", _pdf("none"), "application/pdf")),
                ("files", ("t12_2024.pdf", _pdf("t12"), "application/pdf")),
            ],
            data={
                "user_doc_types": ["CAPEX", "CAPEX", "CAPEX", "T12"],
                "fiscal_years": ["", "2024", "", "2024"],
                # Case-normalized; "" = not stated; a subtype on T12 drops.
                "user_doc_subtypes": ["Future", "historic", "", "future"],
            },
        )
        assert r.status_code == 201, r.text
        by_name = {row["filename"]: row for row in r.json()}
        assert by_name["pip_budget.pdf"]["doc_subtype"] == "future"
        assert by_name["pip_budget.pdf"]["doc_type"] == "CAPEX"
        assert by_name["capex_hist.pdf"]["doc_subtype"] == "historic"
        assert by_name["capex_untagged.pdf"]["doc_subtype"] is None
        assert by_name["t12_2024.pdf"]["doc_subtype"] is None

        stored = {name: await _stored_subtype(row["id"]) for name, row in by_name.items()}
        assert stored == {
            "pip_budget.pdf": "future",
            "capex_hist.pdf": "historic",
            "capex_untagged.pdf": None,
            "t12_2024.pdf": None,
        }

        # The list payload exposes it too.
        r = await client.get(f"/deals/{deal_id}/documents", headers=HEADERS)
        assert r.status_code == 200, r.text
        listed = {row["filename"]: row["doc_subtype"] for row in r.json()}
        assert listed["pip_budget.pdf"] == "future"
        assert listed["capex_hist.pdf"] == "historic"
        assert listed["capex_untagged.pdf"] is None
        assert listed["t12_2024.pdf"] is None


@pytest.mark.asyncio
async def test_upload_unknown_subtype_and_short_array_are_null() -> None:
    deal_id = await _seed_deal()
    async with _client() as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            headers=HEADERS,
            files=[
                ("files", ("a.pdf", _pdf("a"), "application/pdf")),
                ("files", ("b.pdf", _pdf("b"), "application/pdf")),
            ],
            data={
                "user_doc_types": ["CAPEX", "CAPEX"],
                # Only one entry for two files — the second pads to NULL.
                "user_doc_subtypes": ["someday"],
            },
        )
    assert r.status_code == 201, r.text
    assert [row["doc_subtype"] for row in r.json()] == [None, None]


@pytest.mark.asyncio
async def test_legacy_upload_without_subtypes_still_works() -> None:
    deal_id = await _seed_deal()
    async with _client() as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            headers=HEADERS,
            files={"files": ("drop.pdf", _pdf("legacy"), "application/pdf")},
        )
    assert r.status_code == 201, r.text
    assert r.json()[0]["doc_subtype"] is None


# ─────────────────────────── reclassify ───────────────────────────


@pytest.mark.asyncio
async def test_reclassify_sets_and_clears_subtype() -> None:
    deal_id = await _seed_deal()
    doc_id = await _seed_document(deal_id, subtype="historic")
    url = f"/deals/{deal_id}/documents/{doc_id}/classification"
    async with _client() as client:
        r = await client.patch(url, headers=HEADERS, json={"doc_subtype": "future"})
        assert r.status_code == 200, r.text
        assert r.json()["doc_subtype"] == "future"
        assert r.json()["doc_type"] == "CAPEX"
        assert await _stored_subtype(doc_id) == "future"

        # A fiscal_year-only reclassify leaves the subtype alone.
        r = await client.patch(url, headers=HEADERS, json={"fiscal_year": 2025})
        assert r.status_code == 200, r.text
        assert r.json()["doc_subtype"] == "future"

        # Explicit null clears it (Data Room → Historic CapEx).
        r = await client.patch(url, headers=HEADERS, json={"doc_subtype": None})
        assert r.status_code == 200, r.text
        assert r.json()["doc_subtype"] is None
        assert await _stored_subtype(doc_id) is None


@pytest.mark.asyncio
async def test_reclassify_rejects_invalid_subtype() -> None:
    deal_id = await _seed_deal()
    capex_id = await _seed_document(deal_id)
    t12_id = await _seed_document(deal_id, doc_type="T12")
    async with _client() as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{capex_id}/classification",
            headers=HEADERS,
            json={"doc_subtype": "someday"},
        )
        assert r.status_code == 422, r.text
        r = await client.patch(
            f"/deals/{deal_id}/documents/{t12_id}/classification",
            headers=HEADERS,
            json={"doc_subtype": "future"},
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_reclassify_to_capex_with_subtype_and_off_capex_clears() -> None:
    deal_id = await _seed_deal()
    doc_id = await _seed_document(deal_id, doc_type="OM")
    url = f"/deals/{deal_id}/documents/{doc_id}/classification"
    async with _client() as client:
        r = await client.patch(
            url, headers=HEADERS, json={"doc_type": "CAPEX", "doc_subtype": "future"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["doc_type"] == "CAPEX"
        assert r.json()["doc_subtype"] == "future"

        r = await client.patch(url, headers=HEADERS, json={"doc_type": "T12"})
        assert r.status_code == 200, r.text
        assert r.json()["doc_subtype"] is None
        assert await _stored_subtype(doc_id) is None


# ─────────────────────── schema without the column ───────────────────────


@pytest.mark.asyncio
async def test_routes_survive_a_schema_without_the_column(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.database import get_session_factory

    deal_id = await _seed_deal()
    doc_id = await _seed_document(deal_id)
    async with get_session_factory()() as session:
        await session.execute(text("ALTER TABLE documents DROP COLUMN doc_subtype"))
        await session.commit()
    assert not await _column_exists()
    try:
        with caplog.at_level("WARNING", logger="app.api.documents"):
            async with _client() as client:
                r = await client.post(
                    f"/deals/{deal_id}/documents/upload",
                    headers=HEADERS,
                    files={"files": ("pip.pdf", _pdf("old"), "application/pdf")},
                    data={"user_doc_types": ["CAPEX"], "user_doc_subtypes": ["future"]},
                )
                assert r.status_code == 201, r.text
                assert r.json()[0]["doc_subtype"] is None
                assert r.json()[0]["error_kind"] is None

                r = await client.get(f"/deals/{deal_id}/documents", headers=HEADERS)
                assert r.status_code == 200, r.text
                assert all(row["doc_subtype"] is None for row in r.json())

                url = f"/deals/{deal_id}/documents/{doc_id}/classification"
                r = await client.patch(url, headers=HEADERS, json={"doc_subtype": "future"})
                assert r.status_code == 200, r.text
                assert r.json()["doc_subtype"] is None

                r = await client.patch(url, headers=HEADERS, json={"doc_type": "T12"})
                assert r.status_code == 200, r.text
                assert r.json()["doc_type"] == "T12"
        messages = [rec.getMessage() for rec in caplog.records]
        assert any("doc_subtype column absent" in m for m in messages)
        assert any("upload: doc_subtype='future' dropped" in m for m in messages)
    finally:
        from app.migrations import run_startup_migrations

        await run_startup_migrations()
    assert await _column_exists()
