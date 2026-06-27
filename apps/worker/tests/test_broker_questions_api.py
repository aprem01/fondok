"""API tests for the broker-questions endpoints in ``api/analysis.py``.

Endpoints under test:

* ``GET    /analysis/{deal_id}/broker_questions``
* ``PATCH  /analysis/{deal_id}/broker_questions/{question_id}``
* ``POST   /analysis/{deal_id}/broker_questions/refresh``

These pin the persistence behavior of Wave 1 #4: refresh inserts rows
from the deterministic engine, doesn't duplicate on re-run, enforces
state-transition rules, and is tenant-scoped.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-broker-questions.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-broker-questions-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "broker_questions",
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


async def _seed_historical_pnls(
    *,
    deal_id: str,
    tenant_id: str,
    years: dict[int, dict[str, float]],
) -> None:
    """Insert one documents row + one extraction_results row per year.

    Each ``years[year]`` is a flat dict of ``{line_item: amount}``; we
    convert to the extraction-field list shape the engine loader
    expects and stash the year in both ``documents.fiscal_year`` and
    the synthesized ``period_label`` field.
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        for year, line_items in years.items():
            doc_id = str(uuid4())
            extr_id = str(uuid4())
            await session.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, deal_id, tenant_id, filename, doc_type,
                        status, fiscal_year
                    ) VALUES (
                        :id, :deal, :tenant, :fname, 'T12', 'Extracted', :year
                    )
                    """
                ),
                {
                    "id": doc_id,
                    "deal": deal_id,
                    "tenant": tenant_id,
                    "fname": f"pnl-{year}.pdf",
                    "year": year,
                },
            )
            fields = [
                {
                    "field_name": name,
                    "value": value,
                    "source_page": 1,
                    "confidence": 0.9,
                }
                for name, value in line_items.items()
            ]
            # Also seed the period_label so the loader's secondary year
            # resolution path is exercised by at least one row.
            fields.append(
                {
                    "field_name": "p_and_l_usali.period_label",
                    "value": f"FY{year}",
                    "source_page": 1,
                    "confidence": 0.95,
                }
            )
            await session.execute(
                text(
                    """
                    INSERT INTO extraction_results (
                        id, document_id, deal_id, tenant_id, fields
                    ) VALUES (
                        :id, :doc, :deal, :tenant, :fields
                    )
                    """
                ),
                {
                    "id": extr_id,
                    "doc": doc_id,
                    "deal": deal_id,
                    "tenant": tenant_id,
                    "fields": json.dumps(fields),
                },
            )
        await session.commit()


def _baseline_year(**overrides: float) -> dict[str, float]:
    base: dict[str, float] = {
        "rooms_revenue": 5_000_000.0,
        "rooms_dept_expense": 1_500_000.0,
        "fb_revenue": 1_000_000.0,
        "fb_dept_expense": 700_000.0,
        "other_operated_revenue": 200_000.0,
        "other_operated_expense": 120_000.0,
        "noi": 1_800_000.0,
        "gop": 2_400_000.0,
        "total_revenue": 6_300_000.0,
    }
    base.update(overrides)
    return base


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
async def test_refresh_creates_question_rows_for_above_threshold() -> None:
    """POST /refresh runs the engine and inserts rows for any line
    whose YoY delta crossed its threshold.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create a deal under TENANT_A.
        r = await client.post(
            "/deals",
            json={"name": "Brkr-Q Hotel", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        # Seed 2024 + 2025 with F&B revenue down 20% (trips 15% threshold).
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0),  # -20%
            },
        )

        # Refresh.
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)

        fb_questions = [q for q in body if q["line_item"] == "fb_revenue"]
        assert len(fb_questions) == 1
        fb = fb_questions[0]
        assert fb["period_key"] == "2024_vs_2025"
        assert fb["severity"] == "WARN"
        assert fb["state"] == "pending"
        assert "F&B revenue declined" in fb["question_text"]
        assert "20.0% YoY" in fb["question_text"]
        assert fb["actual_prior"] == pytest.approx(1_000_000.0)
        assert fb["actual_current"] == pytest.approx(800_000.0)


@pytest.mark.asyncio
async def test_refresh_does_not_duplicate_on_rerun() -> None:
    """Hitting /refresh twice doesn't double-insert rows for the same
    (line, period_key) — the dedupe check is on open rows.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Dedupe Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0),  # -20%
            },
        )

        for _ in range(2):
            r = await client.post(
                f"/analysis/{deal_id}/broker_questions/refresh",
                json={},
                headers={"X-Tenant-Id": TENANT_A},
            )
            assert r.status_code == 200, r.text

        r = await client.get(
            f"/analysis/{deal_id}/broker_questions",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        body = r.json()
        fb_questions = [q for q in body if q["line_item"] == "fb_revenue"]
        # No duplicate — still exactly one open F&B question.
        assert len(fb_questions) == 1


@pytest.mark.asyncio
async def test_patch_state_transitions() -> None:
    """pending → sent → answered is allowed; illegal jumps 409;
    dismissing without a reason 400s.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Patch Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0),
            },
        )
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        fb_id = next(
            q["id"] for q in r.json() if q["line_item"] == "fb_revenue"
        )

        # Illegal: pending → answered (must go through sent).
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={
                "next_state": "answered",
                "broker_response": "we cut a banquet line",
            },
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 409, r.text

        # Legal: pending → sent.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={"next_state": "sent"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "sent"

        # Legal: sent → answered (with broker_response).
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={
                "next_state": "answered",
                "broker_response": "Banquet contract loss in Q2; one-time.",
            },
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "answered"
        assert "Banquet contract loss" in body["broker_response"]

        # Terminal: answered → anywhere is rejected.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={"next_state": "sent"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_dismiss_requires_reason() -> None:
    """A dismissal without ``dismissal_reason`` is rejected with 400."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Dismiss Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0),
            },
        )
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        fb_id = next(
            q["id"] for q in r.json() if q["line_item"] == "fb_revenue"
        )

        # Missing reason → 400.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={"next_state": "dismissed"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 400, r.text

        # With reason → 200.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{fb_id}",
            json={
                "next_state": "dismissed",
                "dismissal_reason": "Pricing reset year; expected one-time.",
            },
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "dismissed"


@pytest.mark.asyncio
async def test_list_filters_by_state() -> None:
    """GET /broker_questions?state=pending narrows to open rows only."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Filter Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        # Two trips: F&B revenue down 20% AND NOI down 10%.
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0, noi=1_620_000.0),
            },
        )
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        all_q = r.json()
        assert len(all_q) >= 2

        # Dismiss one of them.
        target_id = all_q[0]["id"]
        await client.patch(
            f"/analysis/{deal_id}/broker_questions/{target_id}",
            json={"next_state": "dismissed", "dismissal_reason": "noise"},
            headers={"X-Tenant-Id": TENANT_A},
        )

        # Filter pending only.
        r = await client.get(
            f"/analysis/{deal_id}/broker_questions?state=pending",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert all(q["state"] == "pending" for q in body)
        assert target_id not in {q["id"] for q in body}

        # Filter dismissed.
        r = await client.get(
            f"/analysis/{deal_id}/broker_questions?state=dismissed",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == target_id


@pytest.mark.asyncio
async def test_tenant_scoping_blocks_cross_tenant_access() -> None:
    """A deal owned by TENANT_A is 404 to TENANT_B on every endpoint."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create deal under A.
        r = await client.post(
            "/deals",
            json={"name": "Tenant A Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        await _seed_historical_pnls(
            deal_id=deal_id,
            tenant_id=TENANT_A,
            years={
                2024: _baseline_year(),
                2025: _baseline_year(fb_revenue=800_000.0),
            },
        )
        # Refresh as A → produces a row.
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        a_questions = r.json()
        assert len(a_questions) >= 1
        qid = a_questions[0]["id"]

        # Tenant B: list should 404 (deal not in tenant).
        r = await client.get(
            f"/analysis/{deal_id}/broker_questions",
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404, r.text

        # Tenant B: refresh should 404.
        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404, r.text

        # Tenant B: patch on a question that belongs to A → 404.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_questions/{qid}",
            json={"next_state": "sent"},
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_refresh_emits_nothing_when_no_pnls() -> None:
    """A deal with no PNL extractions returns an empty list, not 500."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Empty Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]

        r = await client.post(
            f"/analysis/{deal_id}/broker_questions/refresh",
            json={},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200, r.text
        assert r.json() == []
