"""Year-mismatch banner — Wave 1 #4.

The Extractor pulled a ``p_and_l_usali.period_ending`` whose year
disagrees with the analyst's wizard ``fiscal_year``. The documents row
is left with ``year_mismatch=True`` and the UI surfaces a banner with
two choices, mirrored on the ``accept_year`` endpoint:

  * ``use_ai_year=True``  — overwrite fiscal_year with extracted_period_year.
  * ``use_ai_year=False`` — keep fiscal_year; just clear the flag.

Both branches clear the flag.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-year-mismatch.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-year-mismatch-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-111111aaaaaa"
TENANT_B = "22222222-2222-2222-2222-222222bbbbbb"


async def _seed_year_mismatch_doc(
    *,
    deal_id: str,
    tenant_id: str,
    doc_id: str,
    fiscal_year: int,
    extracted_period_year: int,
) -> None:
    """Insert a documents row preset to year_mismatch=True so we can
    test the accept_year resolution paths without driving the full
    extraction pipeline."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        existing = (
            await session.execute(
                text("SELECT id FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
        if existing is None:
            await session.execute(
                text(
                    """
                    INSERT INTO deals (
                        id, tenant_id, name, status, created_at, updated_at
                    ) VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                    """
                ),
                {
                    "id": deal_id,
                    "tenant": tenant_id,
                    "name": "Year Mismatch Hotel",
                    "ts": now,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, storage_key, size_bytes,
                    page_count, parser, extraction_data,
                    user_provided_doc_type, fiscal_year, misclassified,
                    year_mismatch, extracted_period_year
                ) VALUES (
                    :id, :deal_id, :tenant_id, :filename, :doc_type, :status,
                    :uploaded_at, :content_hash, :storage_key, :size_bytes,
                    :page_count, :parser, :extraction_data,
                    :user_provided_doc_type, :fiscal_year, :misclassified,
                    :year_mismatch, :extracted_period_year
                )
                """
            ),
            {
                "id": doc_id,
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "filename": "annual_with_wrong_year.pdf",
                "doc_type": "T12",
                "status": "EXTRACTED",
                "uploaded_at": now,
                "content_hash": uuid4().hex,
                "storage_key": f"file:///tmp/{doc_id}",
                "size_bytes": 2048,
                "page_count": 3,
                "parser": "pymupdf",
                "extraction_data": json.dumps({"parser": "pymupdf"}),
                "user_provided_doc_type": "T12",
                "fiscal_year": fiscal_year,
                "misclassified": 0,
                "year_mismatch": 1,
                "extracted_period_year": extracted_period_year,
            },
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "audit_log",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_accept_ai_year_overwrites_fiscal_year() -> None:
    """``use_ai_year=True`` writes ``extracted_period_year`` over
    ``fiscal_year`` and clears the flag."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_year_mismatch_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        fiscal_year=2024,
        extracted_period_year=2025,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_year",
            json={"use_ai_year": True},
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year_mismatch"] is False
    assert body["fiscal_year"] == 2025
    # extracted_period_year stays as the worker's read.
    assert body["extracted_period_year"] == 2025


@pytest.mark.asyncio
async def test_keep_mine_year_clears_flag_only() -> None:
    """``use_ai_year=False`` keeps the analyst's fiscal_year and
    clears the flag — fiscal_year DOES NOT change."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_year_mismatch_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        fiscal_year=2024,
        extracted_period_year=2025,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_year",
            json={"use_ai_year": False},
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year_mismatch"] is False
    assert body["fiscal_year"] == 2024
    assert body["extracted_period_year"] == 2025


@pytest.mark.asyncio
async def test_year_mismatch_surfaces_in_list_documents() -> None:
    """The list endpoint must include ``year_mismatch`` +
    ``extracted_period_year`` so the Data Room can render the banner
    without a second fetch."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_year_mismatch_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        fiscal_year=2024,
        extracted_period_year=2025,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/documents",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    rows = r.json()
    assert any(
        row["id"] == doc_id
        and row["year_mismatch"] is True
        and row["fiscal_year"] == 2024
        and row["extracted_period_year"] == 2025
        for row in rows
    ), rows


@pytest.mark.asyncio
async def test_cross_tenant_accept_year_returns_404() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_year_mismatch_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        fiscal_year=2024,
        extracted_period_year=2025,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_year",
            json={"use_ai_year": True},
            headers={"X-Tenant-Id": TENANT_B},
        )

    assert r.status_code == 404, r.text
