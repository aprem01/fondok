"""IC-memo generation honours the analyst's persisted ``memo_*`` overrides.

The IC Memo tab persists the committee decision into
``deals.field_overrides`` (verdict / thesis / highlights / risks). These
tests pin that ``GET /deals/{id}/memo`` layers those edits authoritatively
on top of the generated sections — and that with no ``memo_*`` override the
memo is byte-identical to what the Analyst produced (strictly opt-in).

Two layers of coverage:

* Pure-function tests of :func:`app.memo_overrides.apply_memo_overrides`
  (deterministic, no DB, no LLM).
* Endpoint tests that seed the in-process memo cache directly and read
  it back through ``GET /memo`` with / without overrides on the deal row.

Hermetic: no Anthropic call, no Railway dependency, per-test SQLite row.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import — same pattern as
# test_memo_generation.py / test_memo_edits.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-memo-analyst-overrides.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-000000000001"
_OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
_TENANT_HEADERS = {"X-Tenant-Id": _TENANT}


# ─────────────────── generated (base) memo the analyst emits ───────────────────

_GEN_THESIS = "Generated thesis prose grounded in the locked spread."
_GEN_MARKET = "Generated market context — submarket RevPAR and comp set."
_GEN_REC = "Approve with conditions subject to PIP completion in Year 2."
_GEN_RISK = "Generated risk prose covering variance flags and capex."


def _base_sections() -> list[dict]:
    """Four real-shaped sections mirroring what the streaming analyst
    writes into the memo cache (subset of the required six)."""
    return [
        {
            "section_id": "investment_thesis",
            "title": "Investment Thesis",
            "body": _GEN_THESIS,
            "citations": [
                {"document_id": "doc-01", "page": 1, "field": None, "excerpt": None}
            ],
        },
        {
            "section_id": "market_analysis",
            "title": "Market Analysis",
            "body": _GEN_MARKET,
            "citations": [],
        },
        {
            "section_id": "risk_factors",
            "title": "Risk Assessment",
            "body": _GEN_RISK,
            "citations": [
                {"document_id": "doc-01", "page": 3, "field": None, "excerpt": None}
            ],
        },
        {
            "section_id": "recommendation",
            "title": "Recommendation",
            "body": _GEN_REC,
            "citations": [],
        },
    ]


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.streaming import reset_broadcast_for_test, reset_memo_cache_for_test

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("memo_edits", "audit_log", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    reset_broadcast_for_test()
    reset_memo_cache_for_test()
    yield
    reset_broadcast_for_test()
    reset_memo_cache_for_test()


async def _insert_deal(
    deal_id: UUID,
    *,
    tenant: str = _TENANT,
    field_overrides: dict | None = None,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence,
                    field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Draft', 0.0,
                    :fo, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant,
                "name": "memo-override-test deal",
                "fo": json.dumps(field_overrides) if field_overrides is not None else None,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _seed_memo_cache(deal_id: UUID, sections: list[dict]) -> None:
    """Write ``sections`` into the in-process memo cache and mark done —
    mirrors the streaming analyst's success path without any LLM call."""
    from app.streaming.broadcast import get_memo_cache

    cache = get_memo_cache()
    for sec in sections:
        await cache.record_section(str(deal_id), sec)
    await cache.mark_done(
        str(deal_id), generated_at=datetime.now(UTC).isoformat()
    )


def _by_id(sections: list[dict]) -> dict[str, dict]:
    return {s["section_id"]: s for s in sections}


# ═══════════════════ pure-function unit tests ═══════════════════


def test_apply_overrides_noop_returns_same_object() -> None:
    """No ``memo_*`` key → the exact input list is returned unchanged."""
    from app.memo_overrides import apply_memo_overrides

    sections = _base_sections()
    # An unrelated override must not trigger the layer.
    assert apply_memo_overrides(sections, {"purchase_price": 42}) is sections
    assert apply_memo_overrides(sections, {}) is sections
    assert apply_memo_overrides(sections, None) is sections


def test_apply_overrides_empty_values_are_noop() -> None:
    """Empty strings / lists and bad verdicts collapse to the no-op path."""
    from app.memo_overrides import apply_memo_overrides

    sections = _base_sections()
    empties = {
        "memo_thesis": "   ",
        "memo_highlights": [],
        "memo_risks": [{"t": ""}, {"ai": True}],
        "memo_recommendation_override": "Maybe",  # not in vocabulary
        "memo_thesis_edited": True,
    }
    assert apply_memo_overrides(sections, empties) is sections


def test_apply_overrides_maps_each_key() -> None:
    from app.memo_overrides import apply_memo_overrides

    sections = _base_sections()
    overrides = {
        "memo_thesis": "Analyst thesis wins.",
        "memo_highlights": [
            {"t": "Entry basis $276K/key", "ai": True},
            {"t": "Kimpton ADR premium", "ai": False},
        ],
        "memo_risks": [
            {"t": "Broker NOI 20% above T-12", "ai": True},
            {"t": "PIP execution risk", "ai": False},
        ],
        "memo_recommendation_override": "Do Not Proceed",
    }
    out = apply_memo_overrides(sections, overrides)
    got = _by_id(out)

    # Thesis: analyst body + appended highlights block.
    thesis_body = got["investment_thesis"]["body"]
    assert thesis_body.startswith("Analyst thesis wins.")
    assert "Key highlights:" in thesis_body
    assert "• Entry basis $276K/key" in thesis_body
    assert "• Kimpton ADR premium" in thesis_body

    # Recommendation: authoritative verdict headline, rationale preserved.
    rec_body = got["recommendation"]["body"]
    assert rec_body.startswith("IC recommendation: Do Not Proceed.")
    assert _GEN_REC in rec_body

    # Risks: replaced by the analyst's curated bullet list.
    assert got["risk_factors"]["body"] == "• Broker NOI 20% above T-12\n• PIP execution risk"

    # Untouched section is passed through by reference (byte-identical).
    assert out[1] is sections[1]
    assert got["market_analysis"]["body"] == _GEN_MARKET

    # Original input is not mutated.
    assert _by_id(sections)["investment_thesis"]["body"] == _GEN_THESIS
    # Citations are preserved on overridden sections.
    assert got["risk_factors"]["citations"] == sections[2]["citations"]


def test_apply_overrides_highlights_only_appends_to_generated_thesis() -> None:
    from app.memo_overrides import apply_memo_overrides

    sections = _base_sections()
    out = apply_memo_overrides(
        sections, {"memo_highlights": [{"t": "Strong basis", "ai": True}]}
    )
    body = _by_id(out)["investment_thesis"]["body"]
    assert body.startswith(_GEN_THESIS)
    assert body.endswith("Key highlights:\n• Strong basis")


# ═══════════════════ endpoint integration tests ═══════════════════


@pytest.mark.asyncio
async def test_get_memo_reflects_analyst_overrides() -> None:
    """With ``memo_*`` set on the deal, ``GET /memo`` returns the analyst's
    authoritative verdict / thesis / highlights / risks."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        field_overrides={
            "memo_thesis": "Analyst thesis is authoritative.",
            "memo_thesis_edited": True,
            "memo_highlights": [
                {"t": "Entry basis $276K/key", "ai": True},
                {"t": "14% ADR premium", "ai": False},
            ],
            "memo_risks": [
                {"t": "Broker NOI 20% above T-12", "ai": True},
                {"t": "PIP execution risk", "ai": False},
            ],
            "memo_recommendation_override": "Do Not Proceed",
        },
    )
    await _seed_memo_cache(deal_id, _base_sections())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    got = _by_id(body["sections"])

    assert got["investment_thesis"]["body"].startswith(
        "Analyst thesis is authoritative."
    )
    assert "• Entry basis $276K/key" in got["investment_thesis"]["body"]
    assert "• 14% ADR premium" in got["investment_thesis"]["body"]

    assert got["recommendation"]["body"].startswith(
        "IC recommendation: Do Not Proceed."
    )
    assert _GEN_REC in got["recommendation"]["body"]

    assert got["risk_factors"]["body"] == (
        "• Broker NOI 20% above T-12\n• PIP execution risk"
    )

    # Untouched section unchanged; citations survive the override layer.
    assert got["market_analysis"]["body"] == _GEN_MARKET
    assert len(body["citations"]) == 2


@pytest.mark.asyncio
async def test_get_memo_unchanged_without_overrides() -> None:
    """Opt-in: with no ``memo_*`` key the memo is byte-identical to what
    the analyst generated — an unrelated override must not perturb it."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id, field_overrides={"purchase_price": 36_400_000})
    await _seed_memo_cache(deal_id, _base_sections())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo")

    assert r.status_code == 200, r.text
    got = _by_id(r.json()["sections"])
    assert got["investment_thesis"]["body"] == _GEN_THESIS
    assert got["market_analysis"]["body"] == _GEN_MARKET
    assert got["risk_factors"]["body"] == _GEN_RISK
    assert got["recommendation"]["body"] == _GEN_REC


@pytest.mark.asyncio
async def test_get_memo_overrides_are_tenant_scoped() -> None:
    """A caller only ever sees their own tenant's overrides. The override
    read is filtered by tenant, so cross-tenant edits can never leak."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    # Deal owned by _TENANT with overrides; cache seeded for it.
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        tenant=_TENANT,
        field_overrides={"memo_recommendation_override": "Proceed"},
    )
    await _seed_memo_cache(deal_id, _base_sections())

    # A different tenant asking for the same deal id gets a 404 (the deal
    # isn't theirs) — never the other tenant's overridden memo.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-Id": _OTHER_TENANT},
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo")
    assert r.status_code == 404, r.text

    # The owning tenant sees the authoritative verdict.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r2 = await client.get(f"/deals/{deal_id}/memo")
    assert r2.status_code == 200, r2.text
    rec = _by_id(r2.json()["sections"])["recommendation"]["body"]
    assert rec.startswith("IC recommendation: Proceed.")
