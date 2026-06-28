"""Tests for the apply-overrides flow.

Verifies that PATCH ``/analysis/{deal}/broker_responses/{qa_id}/apply``:

  1. Merges chosen overrides into ``deals.field_overrides`` as structured
     ``FieldOverrideRecord`` dicts (value + note + overridden_by +
     overridden_at).
  2. Records the chosen subset on the QA pair's ``applied_overrides``.
  3. Distinguishes "apply none" (empty list) from "pending decision"
     (None / NULL).
  4. Skipped overrides are not merged — only the chosen indexes.
  5. Pre-existing overrides on the deal are preserved when new ones are
     applied (the merge is path-keyed).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-qa-apply.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-qa-apply-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")
os.environ["DEFAULT_DEAL_BUDGET_USD"] = "0"

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT = "11111111-1111-1111-1111-11111111aaaa"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("broker_qa_pairs", "broker_questions", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


async def _create_deal(client: Any, *, name: str) -> str:
    r = await client.post(
        "/deals",
        json={"name": name},
        headers={"X-Tenant-Id": TENANT},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _seed_question(deal_id: str) -> str:
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
                    :id, :deal, :tenant, 'fb_revenue', '2024_vs_2025',
                    -0.16, 1200000, 1000000, 0.15, 'WARN',
                    'FB drop?', 'sent'
                )
                """
            ),
            {"id": qid, "deal": deal_id, "tenant": TENANT},
        )
        await session.commit()
    return qid


def _stub_resolver(monkeypatch: pytest.MonkeyPatch, proposed: list[dict[str, Any]]) -> None:
    from datetime import UTC, datetime

    from app.agents import qa_resolver as qr
    from app.agents.qa_resolver import QAResolverOutput
    from fondok_schemas import ModelCall
    from fondok_schemas.broker_qa import ProposedOverride

    async def stub(_payload: Any) -> QAResolverOutput:
        return QAResolverOutput(
            deal_id=_payload.deal_id,
            verdict="resolved",
            summary="Broker explained.",
            proposed_overrides=[ProposedOverride(**o) for o in proposed],
            audit_note="Per broker reply: see above.",
            success=True,
            error=None,
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
            ],
        )

    monkeypatch.setattr(qr, "run_qa_resolver", stub)


async def _read_field_overrides(deal_id: str) -> dict[str, Any]:
    """Read the deal's field_overrides column directly."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT field_overrides FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
        assert row is not None
        raw = row._mapping["field_overrides"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        if isinstance(raw, dict):
            return raw
        return {}


# ─────────────────────────── tests ────────────────────────────


@pytest.mark.asyncio
async def test_apply_subset_merges_field_override_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying indexes [0] persists a structured FieldOverrideRecord."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(
        monkeypatch,
        proposed=[
            {
                "field_path": "p_and_l_usali.operating_revenue.fb_revenue",
                "value": 1_150_000.0,
                "rationale": "Pre-closure baseline named by broker.",
                "confidence": "high",
            },
            {
                "field_path": "p_and_l_usali.fixed_charges.insurance",
                "value": 720_000.0,
                "rationale": "Renewal quote attached to reply.",
                "confidence": "medium",
            },
        ],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal(client, name="Apply Hotel")
        qid = await _seed_question(deal_id)

        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "see attached"},
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 201, r.text
        qa_id = r.json()["id"]

        # Apply only the first proposed override.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_responses/{qa_id}/apply",
            json={"override_indexes_to_apply": [0]},
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        applied = body["applied_overrides"]
        assert applied is not None and len(applied) == 1
        assert applied[0]["field_path"] == (
            "p_and_l_usali.operating_revenue.fb_revenue"
        )

        overrides = await _read_field_overrides(deal_id)
        rec = overrides["p_and_l_usali.operating_revenue.fb_revenue"]
        assert rec["value"] == 1_150_000.0
        assert rec["note"] == "Pre-closure baseline named by broker."
        assert rec["overridden_by"] == "broker_qa_resolver"
        assert rec["overridden_at"]  # ISO timestamp stamped
        # The second proposed override was NOT chosen → not in overrides.
        assert "p_and_l_usali.fixed_charges.insurance" not in overrides


@pytest.mark.asyncio
async def test_apply_empty_list_marks_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sending [] flips applied_overrides from None to [] but doesn't touch
    the deal's field_overrides.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(
        monkeypatch,
        proposed=[
            {
                "field_path": "p_and_l_usali.operating_revenue.fb_revenue",
                "value": 1_150_000.0,
                "rationale": "Pre-closure baseline.",
                "confidence": "high",
            }
        ],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal(client, name="Skip Hotel")
        qid = await _seed_question(deal_id)

        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "ok"},
            headers={"X-Tenant-Id": TENANT},
        )
        qa_id = r.json()["id"]

        # Skip everything.
        r = await client.patch(
            f"/analysis/{deal_id}/broker_responses/{qa_id}/apply",
            json={"override_indexes_to_apply": []},
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Explicit "skipped" — empty list, NOT None.
        assert body["applied_overrides"] == []

        overrides = await _read_field_overrides(deal_id)
        assert overrides == {}


@pytest.mark.asyncio
async def test_apply_preserves_existing_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior override at a different path stays intact when new ones land."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(
        monkeypatch,
        proposed=[
            {
                "field_path": "p_and_l_usali.fixed_charges.insurance",
                "value": 720_000.0,
                "rationale": "Renewal quote.",
                "confidence": "high",
            }
        ],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal(client, name="Preserve Hotel")
        # Seed a prior unrelated override directly.
        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "field_overrides": {
                    "property_overview.year_built": {
                        "value": 2005,
                        "note": "Per OM lobby refresh.",
                        "overridden_by": "user:analyst",
                    }
                }
            },
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 200, r.text

        qid = await _seed_question(deal_id)
        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "renewal info"},
            headers={"X-Tenant-Id": TENANT},
        )
        qa_id = r.json()["id"]

        r = await client.patch(
            f"/analysis/{deal_id}/broker_responses/{qa_id}/apply",
            json={"override_indexes_to_apply": [0]},
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 200, r.text

        overrides = await _read_field_overrides(deal_id)
        # Prior override preserved.
        assert "property_overview.year_built" in overrides
        assert overrides["property_overview.year_built"]["value"] == 2005
        # New override merged.
        assert "p_and_l_usali.fixed_charges.insurance" in overrides
        assert (
            overrides["p_and_l_usali.fixed_charges.insurance"]["value"] == 720_000.0
        )


@pytest.mark.asyncio
async def test_apply_invalid_index_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out-of-range index → 400 with a clear error."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    _stub_resolver(
        monkeypatch,
        proposed=[
            {
                "field_path": "p_and_l_usali.operating_revenue.fb_revenue",
                "value": 1_150_000.0,
                "rationale": "ok",
                "confidence": "high",
            }
        ],
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal(client, name="Range Hotel")
        qid = await _seed_question(deal_id)
        r = await client.post(
            f"/analysis/{deal_id}/broker_responses",
            json={"broker_question_id": qid, "broker_response": "x"},
            headers={"X-Tenant-Id": TENANT},
        )
        qa_id = r.json()["id"]

        r = await client.patch(
            f"/analysis/{deal_id}/broker_responses/{qa_id}/apply",
            json={"override_indexes_to_apply": [7]},  # only 1 proposed
            headers={"X-Tenant-Id": TENANT},
        )
        assert r.status_code == 400, r.text
        assert "out of range" in r.json()["detail"]
