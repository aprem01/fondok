"""Deal completeness endpoint — Wave 1 #1.

``GET /deals/{deal_id}/completeness`` returns the 11-category IC
checklist + a 0-100 percent over the 10 required-for-IC items
(SURVEYS is recommended only and excluded from the denominator).

Covered:
  * Empty deal → 0% with every category uncovered.
  * Three categories covered (OM + T-12 + STR) → 30%.
  * Surveys only → 0% because Surveys is not required-for-IC.
  * Cross-tenant guess → 404 (canonical scoping pattern).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-completeness.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-completeness-storage"
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
                    "name": "Completeness Hotel",
                    "ts": now,
                },
            )
        await session.commit()


async def _seed_document(
    *,
    deal_id: str,
    tenant_id: str,
    doc_type: str,
    filename: str | None = None,
) -> None:
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
                    uploaded_at, content_hash, size_bytes
                ) VALUES (
                    :id, :deal, :tenant, :fn, :dt, 'EXTRACTED', :ts, :ch, 1024
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": filename or f"{doc_type.lower()}_seed.pdf",
                "dt": doc_type,
                "ts": now,
                "ch": uuid4().hex,
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
async def test_empty_deal_is_zero_percent() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/completeness",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completeness_pct"] == 0
    # 11 categories total — 10 required-for-IC, 1 optional (SURVEYS).
    assert len(body["categories"]) == 11
    required = [c for c in body["categories"] if c["required_for_ic"]]
    assert len(required) == 10
    optional = [c for c in body["categories"] if not c["required_for_ic"]]
    assert len(optional) == 1
    assert optional[0]["id"] == "surveys"
    # All zero coverage.
    for c in body["categories"]:
        assert c["covered"] is False
        assert c["doc_count"] == 0


@pytest.mark.asyncio
async def test_three_required_covered_is_thirty_percent() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="OM")
    await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="T12")
    await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="STR_TREND"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/completeness",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # 3 of 10 required-for-IC categories covered → 30%.
    assert body["completeness_pct"] == 30
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["om"]["covered"] is True
    assert by_id["t12"]["covered"] is True
    assert by_id["str"]["covered"] is True
    assert by_id["insurance"]["covered"] is False


@pytest.mark.asyncio
async def test_surveys_only_is_zero_percent() -> None:
    """Surveys is optional — covering only Surveys keeps the percent
    at 0 because it's excluded from the required-for-IC denominator."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="SURVEYS"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/completeness",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completeness_pct"] == 0
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["surveys"]["covered"] is True
    assert by_id["surveys"]["doc_count"] == 1


@pytest.mark.asyncio
async def test_doc_count_aggregates_within_category() -> None:
    """Two monthly P&Ls + one annual P&L all land under the
    ``historical_pnl`` row (count = 3)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL_MONTHLY",
        filename="m1.pdf",
    )
    await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL_MONTHLY",
        filename="m2.pdf",
    )
    await _seed_document(
        deal_id=deal_id, tenant_id=TENANT_A, doc_type="PNL",
        filename="annual.pdf",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/completeness",
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["historical_pnl"]["covered"] is True
    assert by_id["historical_pnl"]["doc_count"] == 3
    # The T-12 row stays uncovered — PNL_MONTHLY / PNL do NOT count there.
    assert by_id["t12"]["covered"] is False


@pytest.mark.asyncio
async def test_cross_tenant_returns_404() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    await _seed_deal(deal_id=deal_id, tenant_id=TENANT_A)
    await _seed_document(deal_id=deal_id, tenant_id=TENANT_A, doc_type="OM")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/deals/{deal_id}/completeness",
            headers={"X-Tenant-Id": TENANT_B},
        )

    assert r.status_code == 404, r.text
