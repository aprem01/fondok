"""Reclassify endpoint — FON-18 / FON-22 DocumentCoverage dropdowns.

``PATCH /deals/{deal_id}/documents/{doc_id}/classification`` sets a
document's doc_type / fiscal_year post-upload, mirrors the choice onto
user_provided_doc_type, and clears the misclassification signals.

Covered:
  * doc_type + fiscal_year update, misclassified cleared.
  * invalid doc_type → 422.
  * empty body (nothing to update) → 422.
  * cross-tenant guess → 404 (canonical scoping pattern).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-reclassify.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-reclassify-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

TENANT_A = "11111111-1111-1111-1111-1111aaaaaaaa"
TENANT_B = "22222222-2222-2222-2222-2222bbbbbbbb"


async def _seed_deal(*, deal_id: str, tenant_id: str) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                """
            ),
            {"id": deal_id, "tenant": tenant_id, "name": "Reclassify Hotel", "ts": now},
        )
        await session.commit()


async def _seed_document(
    *, deal_id: str, tenant_id: str, doc_type: str, misclassified: bool = True
) -> str:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    now = datetime.now(UTC).isoformat()
    doc_id = str(uuid4())
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, size_bytes,
                    user_provided_doc_type, misclassified
                ) VALUES (
                    :id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :ts, :ch, 1024,
                    :dt, :mis
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": f"{doc_type.lower()}_seed.xlsx",
                "dt": doc_type,
                "ts": now,
                "ch": uuid4().hex,
                "mis": misclassified,
            },
        )
        await session.commit()
    return doc_id


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_reclassify_sets_doc_type_and_year() -> None:
    """A monthly P&L re-tagged as the annual T-12 updates doc_type +
    fiscal_year and clears the misclassified flag."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    doc_id = await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL_MONTHLY"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/classification",
            headers={"X-Tenant-Id": TENANT_A},
            json={"doc_type": "T12", "fiscal_year": 2024},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "T12"
    assert body["fiscal_year"] == 2024
    assert body["misclassified"] is False


@pytest.mark.asyncio
async def test_reclassify_lowercase_is_normalized() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    doc_id = await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/classification",
            headers={"X-Tenant-Id": TENANT_A},
            json={"doc_type": "pnl_monthly"},
        )

    assert r.status_code == 200, r.text
    assert r.json()["doc_type"] == "PNL_MONTHLY"


@pytest.mark.asyncio
async def test_reclassify_invalid_doc_type_422() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    doc_id = await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/classification",
            headers={"X-Tenant-Id": TENANT_A},
            json={"doc_type": "NOT_A_REAL_TYPE"},
        )

    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_reclassify_empty_body_422() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    doc_id = await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/classification",
            headers={"X-Tenant-Id": TENANT_A},
            json={},
        )

    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_reclassify_cross_tenant_404() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    doc_id = await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.patch(
            f"/deals/{deal_id}/documents/{doc_id}/classification",
            headers={"X-Tenant-Id": TENANT_B},
            json={"doc_type": "T12"},
        )

    assert r.status_code == 404, r.text
