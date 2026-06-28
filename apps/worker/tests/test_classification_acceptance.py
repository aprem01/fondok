"""``accept_classification`` endpoint — Wave 1 ROADMAP #1.

When the Router agent disagrees with the analyst's wizard tag, the
documents row carries ``misclassified=True`` and the UI surfaces a
warn-tone banner with two choices:

  * Accept Fondok's classification → POST ``{use_ai_classification: true}``
    The user's tag is overwritten with the current ``doc_type`` (so a
    future re-extract doesn't re-flip the flag), and ``misclassified``
    is cleared.

  * Keep mine → POST ``{use_ai_classification: false}``
    The flag is cleared; ``doc_type`` is restored to the user's tag
    if it had drifted.

Both branches are tenant-scoped via the canonical X-Tenant-Id pattern —
a guess at another tenant's doc_id must produce a 404 rather than
leaking the row.
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

# Per-module SQLite DB.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-classification.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-classification-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


async def _seed_misclassified_doc(
    *,
    deal_id: str,
    tenant_id: str,
    doc_id: str,
    user_tag: str,
    ai_doc_type: str,
) -> None:
    """Insert a documents row pre-seeded with a misclassification.

    Bypasses the upload background task so the test asserts purely on
    the ``accept_classification`` write path. Mirrors the state the
    extraction pipeline lands when the Router agent disagrees with
    the analyst's wizard tag.
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        # Ensure the deal exists.
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
                    "name": "Classification Hotel",
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
                    user_provided_doc_type, fiscal_year, misclassified
                ) VALUES (
                    :id, :deal_id, :tenant_id, :filename, :doc_type, :status,
                    :uploaded_at, :content_hash, :storage_key, :size_bytes,
                    :page_count, :parser, :extraction_data,
                    :user_provided_doc_type, :fiscal_year, :misclassified
                )
                """
            ),
            {
                "id": doc_id,
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "filename": f"{user_tag.lower()}_seed.pdf",
                # Worker keeps the user's tag on ``doc_type`` so
                # downstream engines stay aligned with analyst intent
                # while the banner is visible.
                "doc_type": user_tag,
                "status": "EXTRACTED",
                "uploaded_at": now,
                "content_hash": uuid4().hex,
                "storage_key": f"file:///tmp/{doc_id}",
                "size_bytes": 1024,
                "page_count": 1,
                "parser": "pymupdf",
                "extraction_data": json.dumps({
                    "ai_proposed_doc_type": ai_doc_type,
                }),
                "user_provided_doc_type": user_tag,
                "fiscal_year": 2024,
                "misclassified": 1,
            },
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Wipe the documents + deals tables between tests so each starts
    from a deterministic baseline."""
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
async def test_accept_ai_classification_clears_flag() -> None:
    """``use_ai_classification=True`` clears ``misclassified`` and
    copies the current ``doc_type`` (the user's tag, which the worker
    held while the banner was visible) onto ``user_provided_doc_type``
    so a re-extract doesn't re-flip the banner."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_misclassified_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        user_tag="T12",
        ai_doc_type="PNL_MONTHLY",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_classification",
            json={"use_ai_classification": True},
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["misclassified"] is False
    # The endpoint pins ``user_provided_doc_type`` to the current
    # ``doc_type`` (the worker kept this aligned with the user tag),
    # so the flag stays cleared on subsequent re-extracts.
    assert body["user_provided_doc_type"] == "T12"


@pytest.mark.asyncio
async def test_keep_mine_clears_flag_and_restores_user_tag() -> None:
    """``use_ai_classification=False`` clears the flag. When ``doc_type``
    has drifted (defensive — a future code path may set it to the AI
    tag), it's restored to the user's wizard tag."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_misclassified_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        user_tag="T12",
        ai_doc_type="PNL_MONTHLY",
    )

    # Force ``doc_type`` away from the user tag to exercise the
    # "restore" branch — this is the defensive path the endpoint
    # documents.
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("UPDATE documents SET doc_type = :dt WHERE id = :id"),
            {"dt": "PNL_MONTHLY", "id": doc_id},
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_classification",
            json={"use_ai_classification": False},
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["misclassified"] is False
    assert body["doc_type"] == "T12"
    assert body["user_provided_doc_type"] == "T12"


@pytest.mark.asyncio
async def test_cross_tenant_access_returns_404() -> None:
    """Tenant scoping — guessing another tenant's doc_id with a
    different X-Tenant-Id header must 404 rather than leak the row.
    Canonical pattern (see ``deals.py::get_deal``).
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    doc_id = str(uuid4())
    await _seed_misclassified_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=doc_id,
        user_tag="T12",
        ai_doc_type="PNL_MONTHLY",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{doc_id}/accept_classification",
            json={"use_ai_classification": True},
            headers={"X-Tenant-Id": TENANT_B},
        )

    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_unknown_doc_returns_404() -> None:
    """A doc_id that doesn't exist on the deal must 404 — same shape
    the cross-tenant path returns, so the UI can collapse both into a
    single "couldn't update classification" error."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    # Seed at least one valid row so the deal exists; target a
    # different doc id for the bogus call.
    await _seed_misclassified_doc(
        deal_id=deal_id,
        tenant_id=TENANT_A,
        doc_id=str(uuid4()),
        user_tag="T12",
        ai_doc_type="PNL_MONTHLY",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/{uuid4()}/accept_classification",
            json={"use_ai_classification": True},
            headers={"X-Tenant-Id": TENANT_A},
        )

    assert r.status_code == 404, r.text
