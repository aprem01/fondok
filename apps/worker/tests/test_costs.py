"""Tests for the per-deal cost aggregation + ``/costs`` endpoint."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

# Force SQLite for the worker before app modules import — use a dedicated
# DB file so the persistence tests we added below don't pollute the
# observability-test row window (which queries the most recent N rows).
_TEST_DB = Path(__file__).resolve().parent / "fondok-costs.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}")


@pytest.fixture(autouse=True)
async def _isolate_costs_db(monkeypatch: pytest.MonkeyPatch):
    """Force the dedicated costs DB even when another test file set
    ``DATABASE_URL`` first via module-level ``setdefault``. We rebuild
    the engine each test so the override takes effect immediately, and
    we truncate ``model_calls`` after every test so the observability
    test file (which queries ``?n=N`` over the most recent rows) never
    sees our synthetic inserts when both files share a sqlite DB."""
    import app.database as db
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}")
    get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    yield
    # Best-effort cleanup so we don't leak rows into other test files.
    try:
        from sqlalchemy import text
        engine = db.get_engine()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM model_calls"))
    except Exception:
        pass
    db._engine = None
    db._session_factory = None
    get_settings.cache_clear()


def _call(
    *,
    agent: str = "extractor",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 1_000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_create: int = 0,
    cost_usd: float | None = None,
    latency_ms: int = 800,
    when: datetime | None = None,
) -> dict:
    """Synthesize a model_call dict in the shape the aggregator expects."""
    return {
        "agent_name": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "trace_id": f"trace-{uuid4().hex[:8]}",
        "status": "ok",
        "created_at": when or datetime.now(UTC),
    }


def test_aggregate_costs_empty() -> None:
    """No calls should still return a fully-formed zeroed report."""
    from app.costs import aggregate_costs

    deal_id = uuid4()
    report = aggregate_costs([], deal_id=deal_id)

    assert str(report.deal_id) == str(deal_id)
    assert report.total_cost_usd == Decimal("0")
    assert report.budget_usd > Decimal("0")
    assert report.cache_hit_rate == 0.0
    assert report.by_agent == []
    assert report.by_model == {}
    assert report.timeline == []
    assert report.generated_at.tzinfo is not None


def test_aggregate_costs_real() -> None:
    """5 synthetic calls across 3 agents — totals + bucketing must match."""
    from app.costs import aggregate_costs

    calls = [
        _call(agent="router", model="claude-haiku-4-5",
              input_tokens=2_000, output_tokens=200, latency_ms=300),
        _call(agent="extractor", model="claude-sonnet-4-6",
              input_tokens=10_000, output_tokens=1_500, latency_ms=1_200),
        _call(agent="extractor", model="claude-sonnet-4-6",
              input_tokens=8_000, output_tokens=1_200, cache_read=4_000,
              latency_ms=900),
        _call(agent="normalizer", model="claude-sonnet-4-6",
              input_tokens=5_000, output_tokens=800, latency_ms=700),
        _call(agent="analyst", model="claude-opus-4-7",
              input_tokens=15_000, output_tokens=4_000, cache_create=2_000,
              latency_ms=4_500),
    ]

    report = aggregate_costs(calls, deal_id=uuid4(), budget_usd=20.0)

    # 4 distinct agents (router/extractor/normalizer/analyst).
    assert len(report.by_agent) == 4
    agents = {a.agent: a for a in report.by_agent}

    assert agents["extractor"].calls == 2
    assert agents["extractor"].input_tokens == 18_000
    assert agents["extractor"].output_tokens == 2_700
    assert agents["extractor"].cache_read_tokens == 4_000
    assert agents["extractor"].avg_latency_ms == pytest.approx(1050.0, abs=0.5)

    assert agents["analyst"].calls == 1
    assert agents["analyst"].cache_creation_tokens == 2_000

    # Model buckets: haiku, sonnet, opus.
    assert set(report.by_model.keys()) == {"haiku", "sonnet", "opus"}
    assert report.by_model["sonnet"].calls == 3
    assert report.by_model["opus"].calls == 1
    assert report.by_model["haiku"].calls == 1

    # Spot-check pricing math: opus call = 15k * $15 + 4k * $75 per million.
    opus_expected = (15_000 * 15.00 + 4_000 * 75.00) / 1_000_000
    assert float(report.by_model["opus"].cost_usd) == pytest.approx(
        opus_expected, abs=1e-4
    )

    # Total cost should be the sum of agent costs.
    agent_sum = sum(float(a.cost_usd) for a in report.by_agent)
    assert float(report.total_cost_usd) == pytest.approx(agent_sum, abs=1e-4)

    # Cache hit rate = cache_read / (input + cache_read) = 4000 / (40000 + 4000)
    expected_hit = 4_000 / (40_000 + 4_000)
    assert report.cache_hit_rate == pytest.approx(expected_hit, abs=1e-4)

    # Timeline is bounded and most-recent-first.
    assert len(report.timeline) == 5
    timestamps = [t.completed_at for t in report.timeline]
    assert timestamps == sorted(timestamps, reverse=True)

    # Total spend is well under the $20 budget for this synthetic deal.
    assert float(report.total_cost_usd) < 20.0


def test_aggregate_uses_stored_cost_when_present() -> None:
    """If the call carries a stored cost_usd, prefer it over recomputing."""
    from app.costs import aggregate_costs

    calls = [_call(cost_usd=0.1234, input_tokens=999, output_tokens=999)]
    report = aggregate_costs(calls, deal_id=uuid4())
    assert float(report.total_cost_usd) == pytest.approx(0.1234, abs=1e-6)


def test_model_bucket_unknown_falls_back_to_other() -> None:
    """Unknown model strings should bucket under ``other`` and price at $0."""
    from app.costs import aggregate_costs

    calls = [_call(model="gpt-9-mystery", input_tokens=10_000, output_tokens=500)]
    report = aggregate_costs(calls, deal_id=uuid4())
    assert "other" in report.by_model
    assert float(report.by_model["other"].cost_usd) == 0.0


@pytest.mark.asyncio
async def test_costs_endpoint_returns_well_formed_report() -> None:
    """``GET /deals/{id}/costs`` should return a typed report even with no rows."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = str(uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(f"/deals/{deal_id}/costs")
        assert r.status_code == 200
        body = r.json()
        assert body["deal_id"] == deal_id
        assert "total_cost_usd" in body
        assert "budget_usd" in body
        assert "by_agent" in body
        assert "by_model" in body
        assert "timeline" in body
        assert "cache_hit_rate" in body
        assert isinstance(body["by_agent"], list)
        assert isinstance(body["by_model"], dict)


# ─────────────────────── persistence tests ───────────────────────


async def _ensure_schema() -> None:
    """Run startup migrations directly — AsyncClient/ASGITransport
    doesn't drive the FastAPI lifespan, so the ``model_calls`` table
    won't exist on a fresh sqlite file otherwise."""
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


def _model_call_obj(
    *,
    agent: str = "extractor",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 1_000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_create: int = 0,
    cost_usd: float = 0.0,
):
    """Build a real ``ModelCall`` instance the agents would emit."""
    from fondok_schemas import ModelCall

    now = datetime.now(UTC)
    return ModelCall(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        trace_id=f"trace-{uuid4().hex[:8]}",
        started_at=now,
        completed_at=now,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
        agent_name=agent,
    )


@pytest.mark.asyncio
async def test_persist_model_calls_writes_rows() -> None:
    """``persist_model_calls_standalone`` must INSERT to model_calls."""
    from sqlalchemy import text

    from app.cost_persistence import persist_model_calls_standalone
    from app.database import get_session_factory

    await _ensure_schema()

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    calls = [
        _model_call_obj(agent="router", model="claude-haiku-4-5",
                        input_tokens=1_500, output_tokens=120),
        _model_call_obj(agent="extractor", model="claude-sonnet-4-6",
                        input_tokens=12_000, output_tokens=2_000,
                        cache_read=4_000),
        _model_call_obj(agent="analyst", model="claude-opus-4-7",
                        input_tokens=18_000, output_tokens=5_000,
                        cache_create=2_500),
    ]

    inserted = await persist_model_calls_standalone(
        deal_id=deal_id, tenant_id=tenant_id, calls=calls
    )
    assert inserted == 3

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT agent_name, input_tokens, output_tokens, "
                    "cache_read_tokens, cache_creation_tokens, cost_usd "
                    "FROM model_calls WHERE deal_id = :d ORDER BY agent_name"
                ),
                {"d": deal_id},
            )
        ).all()

    by_agent = {r._mapping["agent_name"]: r._mapping for r in rows}
    assert set(by_agent.keys()) == {"analyst", "extractor", "router"}

    # Cost is recomputed from tokens — must be > 0 for every row.
    for agent, row in by_agent.items():
        assert float(row["cost_usd"]) > 0, f"{agent} priced at $0"

    # Cache fields round-tripped intact.
    assert by_agent["extractor"]["cache_read_tokens"] == 4_000
    assert by_agent["analyst"]["cache_creation_tokens"] == 2_500


@pytest.mark.asyncio
async def test_persist_model_calls_empty_is_noop() -> None:
    """Empty list must not touch the DB and must return 0."""
    from app.cost_persistence import persist_model_calls_standalone

    n = await persist_model_calls_standalone(
        deal_id=str(uuid4()), tenant_id=str(uuid4()), calls=[]
    )
    assert n == 0


@pytest.mark.asyncio
async def test_persist_then_aggregate_matches_endpoint() -> None:
    """End-to-end: persist calls then assert /deals/{id}/costs sees them."""
    from httpx import ASGITransport, AsyncClient

    from app.cost_persistence import persist_model_calls_standalone
    from app.main import app

    await _ensure_schema()

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    calls = [
        _model_call_obj(agent="router", model="claude-haiku-4-5",
                        input_tokens=2_000, output_tokens=200),
        _model_call_obj(agent="extractor", model="claude-sonnet-4-6",
                        input_tokens=10_000, output_tokens=1_500),
    ]
    inserted = await persist_model_calls_standalone(
        deal_id=deal_id, tenant_id=tenant_id, calls=calls
    )
    assert inserted == 2

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(f"/deals/{deal_id}/costs")
        assert r.status_code == 200
        body = r.json()
        assert body["deal_id"] == deal_id
        assert float(body["total_cost_usd"]) > 0
        agents = {a["agent"] for a in body["by_agent"]}
        assert "router" in agents
        assert "extractor" in agents
