"""Tests for the per-deal cost aggregation + ``/costs`` endpoint."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

# Force SQLite for the worker before app modules import — matches test_smoke.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


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
