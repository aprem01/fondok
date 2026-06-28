"""API tests for the Q&A re-ingestion endpoints (Wave 1 #5).

Endpoints under test:

  * ``POST   /analysis/{deal_id}/broker_responses``
  * ``GET    /analysis/{deal_id}/qa_history``
  * ``PATCH  /analysis/{deal_id}/broker_responses/{qa_pair_id}/apply``

We stub the QA Resolver agent so no real LLM traffic flies; the persistence,
tenant scoping, and parent broker_question state transition are the real
contract surface here.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-qa-api.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-qa-api-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")
os.environ["DEFAULT_DEAL_BUDGET_USD"] = "0"

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "broker_qa_pairs",
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


async def _seed_broker_question(
    *,
    deal_id: str,
    tenant_id: str,
    state: str = "sent",
) -> str:
    """Insert a single broker_question row in ``state`` and return its id.

    We don't go through the /refresh endpoint because that exercises the
    deterministic engine — these tests only care about the Q&A flow.
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    qid = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO broker_questions (
                    id, deal_id, tenant_id, line_item, period_key,
                    variance_pct, actual_prior, actual_current,
                    threshold_pct, severity, question_text, state
                ) VALUES (
                    :id, :deal, :tenant, :line_item, :period_key,
                    :variance_pct, :actual_prior, :actual_current,
                    :threshold_pct, 'WARN', :qtext, :state
                )
                """
            ),
            {
                "id": qid,
                "deal": deal_id,
                "tenant": tenant_id,
                "line_item": "fb_revenue",
                "period_key": "2024_vs_2025",
                "variance_pct": -0.16,
                "actual_prior": 1_200_000.0,
                "actual_current": 1_000_000.0,
                "threshold_pct": 0.15,
                "qtext": (
                    "F&B revenue declined 16% YoY (2024 → 2025). "
                    "Driver?"
                ),
                "state": state,
            },
        )
        await session.commit()
    return qid


def _stub_resolver(monkeypatch: pytest.MonkeyPatch, **envelope: Any) -> None:
    """Replace ``run_qa_resolver`` so the API tests don't hit the LLM."""
    from app.agents import qa_resolver as qr
    from app.agents.qa_resolver import QAResolverOutput
    from fondok_schemas import ModelCall
    from fondok_schemas.broker_qa import ProposedOverride
    from datetime import UTC, datetime

    proposed = [
        ProposedOverride(**o) for o in envelope.get("proposed_overrides", [])
    ]
    verdict = envelope.get("verdict", "resolved")
    summary = envelope.get("summary", "Stub summary.")
    audit_note = envelope.get("audit_note", "Per broker reply: stub.")
    success = envelope.get("success", True)
    error = envelope.get("error", None)

    async def stub(_payload: Any) -> QAResolverOutput:
        return QAResolverOutput(
            deal_id=_payload.deal_id,
            verdict=verdict if success else None,
            summary=summary if success else "",
            proposed_overrides=proposed if success else [],
            audit_note=audit_note if success else "",
            success=success,
            error=error,
            model_calls=[
                ModelCall(
                    model="claude-sonnet-4-6-stub",
                    input_tokens=10,
                    output_tokens=10,
                    cost_usd=0.0,
                    trace_id=_payload.deal_id,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    agent_name="qa_resolver",
                )
            ] if success else [],
        )

    # Replace the symbol the API endpoint actually calls.
    monkeypatch.setattr(qr, "run_qa_resolver", stub)
    import app.api.analysis as analysis_mod

    # When the endpoint does `from ..agents.qa_resolver import run_qa_resolver`
    # inside the function body, the import binds at call time → patching
    # the source module is sufficient.
    _ = analysis_mod  # ensure module is imported


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
async def test_submit_creates_qa_pair_and_flips_question_to_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(
        monkeypatch,
        verdict="resolved",
        summary="Broker explained the F&B drop.",
        audit_note="Per broker reply: contract reset Nov-24.",
        proposed_overrides=[
            {
                "field_path": "p_and_l_usali.operating_revenue.fb_revenue",
                "value": 1_150_000.0,
                "rationale": "Pre-closure baseline named by broker.",
                "confidence": "high",
            }
        ],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "QA Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        qid = await _seed_broker_question(
            deal_id=deal_id, tenant_id=TENANT_A, state="sent"
        )

        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={
                "broker_question_id": qid,
                "broker_response": (
                    "F&B contract reset November 2024; self-managed since."
                ),
            },
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["resolver_verdict"] == "resolved"
        assert body["resolver_summary"].startswith("Broker explained")
        assert body["applied_overrides"] is None  # not yet applied
        assert len(body["proposed_overrides"]) == 1
        assert body["audit_note"].startswith("Per broker reply:")

        # The parent question should now be in 'answered' state with
        # the broker_response copied over.
        r = await client.get(
            f"/analysis/{deal_id}/broker_questions",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        rows = r.json()
        match = next(q for q in rows if q["id"] == qid)
        assert match["state"] == "answered"
        assert "self-managed" in (match["broker_response"] or "")


@pytest.mark.asyncio
async def test_qa_history_is_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant B cannot see Tenant A's QA pairs (404 on the deal scope)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Tenant A Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        qid = await _seed_broker_question(
            deal_id=deal_id, tenant_id=TENANT_A, state="sent"
        )
        # Submit + create a QA pair under tenant A.
        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "ok"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201

        # Tenant A: sees one entry.
        r = await client.get(
            f"/analysis/{deal_id}/qa_history",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        assert len(r.json()) == 1

        # Tenant B: 404 on the same deal id.
        r = await client.get(
            f"/analysis/{deal_id}/qa_history",
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_submit_rejects_dismissed_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submitting a reply to a ``dismissed`` question → 409."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Dismissed Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        qid = await _seed_broker_question(
            deal_id=deal_id, tenant_id=TENANT_A, state="dismissed"
        )

        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "hi"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_qa_history_filter_by_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?state=resolved`` filters to verdicts that match exactly."""
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

        # Submit two pairs with different verdicts.
        for verdict in ("resolved", "still_concerning"):
            qid = await _seed_broker_question(
                deal_id=deal_id, tenant_id=TENANT_A, state="sent"
            )
            _stub_resolver(monkeypatch, verdict=verdict)
            r = await client.post(
                f"/analysis/{deal_id}/broker_responses",
                json={"broker_question_id": qid, "broker_response": "ok"},
                headers={"X-Tenant-Id": TENANT_A},
            )
            assert r.status_code == 201, r.text

        r = await client.get(
            f"/analysis/{deal_id}/qa_history?state=resolved",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["resolver_verdict"] == "resolved"


@pytest.mark.asyncio
async def test_budget_exhausted_returns_402(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the resolver raises BudgetExceededError the endpoint surfaces 402."""
    from httpx import ASGITransport, AsyncClient

    from app.budget import BudgetExceededError
    from app.main import app

    async def boom(_payload: Any) -> Any:
        raise BudgetExceededError(
            deal_id=_payload.deal_id, spent_usd=21.0, budget_usd=20.0
        )

    from app.agents import qa_resolver as qr

    monkeypatch.setattr(qr, "run_qa_resolver", boom)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "Broke Hotel"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = r.json()["id"]
        qid = await _seed_broker_question(
            deal_id=deal_id, tenant_id=TENANT_A, state="sent"
        )

        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "ok"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 402, r.text
        assert "budget" in r.json()["detail"].lower()
