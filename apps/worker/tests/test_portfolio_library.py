"""Wave 4 W4.1 — Portfolio P&L Library tests.

Sam's June 2026 ask: *"Apollo (and other capital partners) own hotels
in the same market and want to upload THEIR P&Ls as benchmarks."*

Wave 2 P2.7 added the per-deal PORTFOLIO_PNL doc type. W4.1 promotes
that to a firm-level Library: analysts upload portfolio P&L roll-ups
once, tag them with chain scales + vintage years, and the engine
automatically pulls them as portfolio_pnl candidates on every deal.

Coverage:

 1. ``test_create_entry_persists`` — POST persists and GET round-trips.
 2. ``test_list_entries_tenant_scoped`` — two tenants don't see each
    other's entries.
 3. ``test_unique_name_per_tenant`` — two entries with same (tenant,
    name) → 409.
 4. ``test_deactivate_excludes_from_engine_resolution`` — inactive
    entries are not pulled into the median.
 5. ``test_reactivate_includes_again`` — flipping back to active
    re-includes the entry.
 6. ``test_delete_blocked_when_referenced_by_deal`` — hard delete is
    blocked when ``source_document_id`` is still owned by a deal doc.
 7. ``test_engine_uses_library_median_when_no_per_deal_portfolio_doc``
    — without a per-deal PORTFOLIO_PNL, the engine ingests the library
    median into ``base["overrides"]`` and tags the source with
    ``SOURCE_PORTFOLIO_PNL``.
 8. ``test_per_deal_portfolio_doc_overrides_library_median`` — when a
    per-deal PORTFOLIO_PNL document covers the same ratio, the
    per-deal value WINS over the library median.
 9. ``test_library_filtered_by_chain_scale_match`` — an entry whose
    ``chain_scales_covered`` doesn't include the subject's chain scale
    is excluded.
10. ``test_library_vintage_3_year_lookback`` — entries older than
    ``current_year - 3`` are excluded.
11. ``test_endpoint_get_filters_by_chain_scale`` — the GET endpoint's
    ``?chain_scale=`` query param filters returned rows.
12. ``test_upload_creates_doc_and_entry_atomically`` — multipart upload
    runs extraction and creates the entry.
13. ``test_upload_rejects_if_doc_extraction_fails`` — extraction error
    surfaces 422 and the entry is NOT created.
14. ``test_engine_falls_through_to_cbre_when_library_empty`` — when
    the library has no qualifying entries, the engine doesn't tag
    portfolio_pnl provenance for any ratio.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text


# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-portfolio-library.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


_TENANT = "00000000-0000-0000-0000-000000000001"
_OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so the DB is deterministic."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "extraction_results",
            "documents",
            "portfolio_library",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


def _client(tenant: str = _TENANT):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-Id": tenant},
    )


def _entry_body(
    name: str,
    *,
    vintage_year: int | None = None,
    chain_scales: list[str] | None = None,
    expense_ratios: dict[str, float] | None = None,
    asset_count: int = 6,
    total_rooms_modeled: int = 1200,
    revenue_mix: dict[str, float] | None = None,
    source_document_id: str | None = None,
) -> dict:
    return {
        "name": name,
        "description": f"Test entry: {name}",
        "vintage_year": vintage_year or datetime.now(UTC).year,
        "asset_count": asset_count,
        "total_rooms_modeled": total_rooms_modeled,
        "chain_scales_covered": chain_scales or ["Upper Upscale"],
        "msa_coverage": None,
        "expense_ratios": expense_ratios or {
            "rooms_dept_pct": 0.28,
            "fb_dept_pct": 0.68,
            "admin_pct": 0.08,
            "sales_pct": 0.07,
            "utilities_pct": 0.04,
            "property_tax_pct": 0.03,
            "insurance_pct": 0.012,
            "mgmt_fee_pct": 0.03,
        },
        "revenue_mix": revenue_mix,
        "source_document_id": source_document_id,
    }


async def _insert_deal_row(
    *,
    deal_id: UUID,
    tenant_id: str = _TENANT,
    keys: int = 200,
    positioning: str | None = "Upper Upscale",
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    positioning, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Underwriting', 0.0, :keys,
                    :positioning, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant_id,
                "name": "Test Hotel",
                "keys": keys,
                "positioning": positioning,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_portfolio_pnl_extraction(
    *,
    deal_id: UUID,
    fields: list[dict],
    tenant_id: str = _TENANT,
) -> None:
    """Insert an EXTRACTED PORTFOLIO_PNL document with the given fields."""
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'portfolio.pdf', 'PORTFOLIO_PNL',
                    'EXTRACTED', :ts, 1, '{}'
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "ts": datetime.now(UTC),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "fields": json.dumps(fields),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_create_entry_persists() -> None:
    """POST /portfolio-library persists; GET /portfolio-library/{id} round-trips."""
    async with _client() as c:
        r = await c.post("/portfolio-library", json=_entry_body("Apollo Select 2024"))
        assert r.status_code == 201, r.text
        created = r.json()
        entry_id = created["id"]
        UUID(entry_id)
        assert created["name"] == "Apollo Select 2024"
        assert created["is_active"] is True
        assert created["chain_scales_covered"] == ["Upper Upscale"]
        assert created["expense_ratios"]["rooms_dept_pct"] == pytest.approx(0.28)

        r = await c.get(f"/portfolio-library/{entry_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Apollo Select 2024"
        assert body["expense_ratios"]["fb_dept_pct"] == pytest.approx(0.68)


@pytest.mark.asyncio
async def test_list_entries_tenant_scoped() -> None:
    """Two tenants never see each other's library."""
    async with _client(_TENANT) as a:
        r = await a.post("/portfolio-library", json=_entry_body("Tenant A entry"))
        assert r.status_code == 201
    async with _client(_OTHER_TENANT) as b:
        r = await b.post("/portfolio-library", json=_entry_body("Tenant B entry"))
        assert r.status_code == 201

    async with _client(_TENANT) as a:
        rows = (await a.get("/portfolio-library")).json()
        names = {r["name"] for r in rows}
        assert names == {"Tenant A entry"}
    async with _client(_OTHER_TENANT) as b:
        rows = (await b.get("/portfolio-library")).json()
        names = {r["name"] for r in rows}
        assert names == {"Tenant B entry"}


@pytest.mark.asyncio
async def test_unique_name_per_tenant() -> None:
    """Inserting two entries with the same (tenant, name) returns 409."""
    async with _client() as c:
        r1 = await c.post("/portfolio-library", json=_entry_body("Same name"))
        assert r1.status_code == 201
        r2 = await c.post("/portfolio-library", json=_entry_body("Same name"))
        assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_deactivate_excludes_from_engine_resolution() -> None:
    """An inactive entry is not pulled into the library median."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    async with _client() as c:
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Library entry A",
                expense_ratios={"rooms_dept_pct": 0.25},
            ),
        )
        entry_id = r.json()["id"]
        # Sanity check: while active, the engine picks it up.
        deal_id = uuid4()
        await _insert_deal_row(deal_id=deal_id)
        factory = get_session_factory()
        async with factory() as session:
            base = await _load_engine_inputs(
                session, str(deal_id), tenant_id=_TENANT
            )
        assert base["__sources__"].get("rooms_dept_pct") == SOURCE_PORTFOLIO_PNL
        assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.25)

        # Now deactivate — the engine must STOP using this entry.
        r = await c.post(f"/portfolio-library/{entry_id}/deactivate")
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False

    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    # No active entries ⇒ portfolio_pnl provenance must not appear for
    # rooms_dept_pct.
    assert base["__sources__"].get("rooms_dept_pct") != SOURCE_PORTFOLIO_PNL


@pytest.mark.asyncio
async def test_reactivate_includes_again() -> None:
    """Reactivation re-includes the entry in the median."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    async with _client() as c:
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Reactivation entry",
                expense_ratios={"rooms_dept_pct": 0.22},
            ),
        )
        entry_id = r.json()["id"]
        await c.post(f"/portfolio-library/{entry_id}/deactivate")
        await c.post(f"/portfolio-library/{entry_id}/activate")

    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    assert base["__sources__"].get("rooms_dept_pct") == SOURCE_PORTFOLIO_PNL
    assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.22)


@pytest.mark.asyncio
async def test_delete_blocked_when_referenced_by_deal() -> None:
    """Hard delete is blocked when ``source_document_id`` is still owned
    by a deal doc."""
    from app.database import get_session_factory

    # Insert a deal + doc whose id we'll reference from the library.
    deal_id = uuid4()
    doc_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'portfolio.pdf', 'PORTFOLIO_PNL',
                    'EXTRACTED', :ts, 1, '{}'
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()

    async with _client() as c:
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Referenced entry",
                source_document_id=str(doc_id),
            ),
        )
        assert r.status_code == 201, r.text
        entry_id = r.json()["id"]
        # Hard delete should be refused with 409.
        r = await c.delete(f"/portfolio-library/{entry_id}")
        assert r.status_code == 409, r.text
        # Deactivate is the supported path.
        r = await c.post(f"/portfolio-library/{entry_id}/deactivate")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_engine_uses_library_median_when_no_per_deal_portfolio_doc() -> None:
    """Without a per-deal PORTFOLIO_PNL doc, the engine's overrides dict
    carries the library median tagged ``SOURCE_PORTFOLIO_PNL``."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    async with _client() as c:
        # 3 library entries with rooms_dept_pct of 0.20, 0.25, 0.30 ⇒ median 0.25.
        for i, value in enumerate((0.20, 0.25, 0.30)):
            r = await c.post(
                "/portfolio-library",
                json=_entry_body(
                    f"Library entry {i}",
                    expense_ratios={"rooms_dept_pct": value},
                ),
            )
            assert r.status_code == 201, r.text

    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.25)
    assert base["__sources__"]["rooms_dept_pct"] == SOURCE_PORTFOLIO_PNL


@pytest.mark.asyncio
async def test_per_deal_portfolio_doc_overrides_library_median() -> None:
    """A per-deal PORTFOLIO_PNL doc wins over the library median for the
    same ratio."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    async with _client() as c:
        # Library says 0.25.
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Library wins?",
                expense_ratios={"rooms_dept_pct": 0.25},
            ),
        )
        assert r.status_code == 201, r.text

    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)

    # Per-deal PORTFOLIO_PNL says 0.30. Should beat library median.
    await _insert_portfolio_pnl_extraction(
        deal_id=deal_id,
        fields=[
            {"field_name": "portfolio_pnl.rooms_dept_pct", "value": 0.30},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    assert base["overrides"]["rooms_dept_pct"] == pytest.approx(0.30)
    assert base["__sources__"]["rooms_dept_pct"] == SOURCE_PORTFOLIO_PNL


@pytest.mark.asyncio
async def test_library_filtered_by_chain_scale_match() -> None:
    """An entry whose ``chain_scales_covered`` doesn't include the subject's
    chain scale is excluded."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    async with _client() as c:
        # Boutique entry — chain scale doesn't match subject.
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Independent boutique",
                chain_scales=["Independent"],
                expense_ratios={"rooms_dept_pct": 0.42},
            ),
        )
        assert r.status_code == 201, r.text

    # Subject deal is Upper Upscale, library entry covers Independent.
    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id, positioning="Upper Upscale")
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    # No qualifying entries ⇒ portfolio_pnl provenance must NOT appear.
    assert base["__sources__"].get("rooms_dept_pct") != SOURCE_PORTFOLIO_PNL


@pytest.mark.asyncio
async def test_library_vintage_3_year_lookback() -> None:
    """Entries with vintage_year older than current_year - 3 are excluded."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    current_year = datetime.now(UTC).year
    too_old_year = current_year - 10

    async with _client() as c:
        r = await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Stale roll-up",
                vintage_year=too_old_year,
                expense_ratios={"rooms_dept_pct": 0.20},
            ),
        )
        assert r.status_code == 201, r.text

    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )
    # Stale entry must NOT contribute to the median.
    assert base["__sources__"].get("rooms_dept_pct") != SOURCE_PORTFOLIO_PNL


@pytest.mark.asyncio
async def test_endpoint_get_filters_by_chain_scale() -> None:
    """``GET /portfolio-library?chain_scale=upscale`` filters returned rows."""
    async with _client() as c:
        await c.post(
            "/portfolio-library",
            json=_entry_body("Upscale roll-up", chain_scales=["Upscale"]),
        )
        await c.post(
            "/portfolio-library",
            json=_entry_body(
                "Boutique roll-up", chain_scales=["Independent"]
            ),
        )
        rows = (await c.get("/portfolio-library")).json()
        assert len(rows) == 2
        rows = (
            await c.get("/portfolio-library", params={"chain_scale": "Upscale"})
        ).json()
        names = {r["name"] for r in rows}
        assert names == {"Upscale roll-up"}


@pytest.mark.asyncio
async def test_upload_creates_doc_and_entry_atomically(monkeypatch) -> None:
    """POST /portfolio-library/upload runs extraction and creates the entry."""
    from app.api import portfolio_library as pl_mod

    async def fake_extract(*, filename: str, content: bytes):
        return (
            {"rooms_dept_pct": 0.27, "fb_dept_pct": 0.70, "admin_pct": 0.085},
            {"rooms_revenue_pct": 0.72, "fb_revenue_pct": 0.20},
        )

    monkeypatch.setattr(pl_mod, "extract_portfolio_ratios", fake_extract)

    async with _client() as c:
        files = {"file": ("apollo_q4.pdf", b"%PDF-1.7\n%fake\n", "application/pdf")}
        data = {
            "name": "Apollo Full-Service 2024",
            "vintage_year": str(datetime.now(UTC).year),
            "asset_count": "8",
            "total_rooms_modeled": "2400",
            "chain_scales_covered": json.dumps(["Upper Upscale", "Upscale"]),
            "description": "Q4 portfolio benchmark",
        }
        r = await c.post("/portfolio-library/upload", files=files, data=data)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Apollo Full-Service 2024"
        assert body["expense_ratios"]["rooms_dept_pct"] == pytest.approx(0.27)
        assert body["revenue_mix"]["rooms_revenue_pct"] == pytest.approx(0.72)
        assert "Upper Upscale" in body["chain_scales_covered"]


@pytest.mark.asyncio
async def test_upload_rejects_if_doc_extraction_fails(monkeypatch) -> None:
    """When extraction fails the upload returns 422 and creates NO entry."""
    from app.api import portfolio_library as pl_mod

    async def fake_extract_fails(*, filename: str, content: bytes):
        raise pl_mod.PortfolioExtractionError("no portfolio_pnl.* fields found")

    monkeypatch.setattr(pl_mod, "extract_portfolio_ratios", fake_extract_fails)

    async with _client() as c:
        files = {"file": ("bad.pdf", b"%PDF\n", "application/pdf")}
        data = {
            "name": "Failed upload",
            "vintage_year": str(datetime.now(UTC).year),
            "asset_count": "5",
            "total_rooms_modeled": "1000",
            "chain_scales_covered": json.dumps(["Upper Upscale"]),
        }
        r = await c.post("/portfolio-library/upload", files=files, data=data)
        assert r.status_code == 422, r.text

        # Nothing was created.
        rows = (await c.get("/portfolio-library")).json()
        assert rows == []


@pytest.mark.asyncio
async def test_engine_falls_through_to_cbre_when_library_empty() -> None:
    """When the library is empty the engine doesn't tag portfolio_pnl for
    any ratio — the precedence chain falls through (CBRE → HOST → seed)."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        SOURCE_PORTFOLIO_PNL,
        _load_engine_inputs,
    )

    deal_id = uuid4()
    await _insert_deal_row(deal_id=deal_id)

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(
            session, str(deal_id), tenant_id=_TENANT
        )

    sources = base.get("__sources__") or {}
    portfolio_tagged = [
        k for k, v in sources.items() if v == SOURCE_PORTFOLIO_PNL
    ]
    assert portfolio_tagged == [], (
        f"unexpected portfolio_pnl tags with empty library: {portfolio_tagged}"
    )
