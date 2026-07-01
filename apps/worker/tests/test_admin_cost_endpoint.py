"""Tests for the tenant-scoped ``/admin/cost`` rollup + backfill.

Covers three properties Sam relies on when he watches the cost-opt
dashboard:

1. ``GET /admin/cost`` sums spend across windows (24h / 7d / 30d) and
   returns per-agent, per-model, and top-10-by-cost per-deal buckets.
2. ``X-Tenant-Id`` scopes the rollup — rows for a different tenant
   never leak into the response.
3. ``POST /admin/cost/backfill`` recomputes ``cost_usd`` for rows that
   were persisted with ``cost_usd = 0`` and is idempotent (a second run
   updates zero rows).

We seed the fixture rows via a direct SQL INSERT rather than the
``/observability/_test/model-call`` endpoint because that ingest path
was built before ``tenant_id`` mattered — it doesn't accept the column
in its body — and tenant scoping is exactly what these tests need to
verify. The direct INSERT mirrors the shape ``persist_model_calls``
writes so the tests exercise the same schema.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Per-test SQLite DB so we're not contending with observability's DB.
# Must be set before any ``app.*`` import binds Settings().
_TEST_DB = Path(__file__).resolve().parent / "fondok-admin-cost.db"
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


# ─────────────────────── fixtures + helpers ───────────────────────


async def _ensure_schema() -> None:
    """Run startup migrations. AsyncClient + ASGITransport does not
    drive FastAPI's lifespan, so the ``model_calls`` table won't exist
    on a fresh sqlite file without this."""
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


async def _seed(
    *,
    tenant_id: str,
    deal_id: str,
    agent_name: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: float = 0.0,
    created_at: datetime | None = None,
) -> str:
    """Insert one synthetic model_calls row and return its id.

    Kept as a helper (not a fixture) because different tests seed
    different mixes of rows and a parametrized fixture would be
    harder to read than a couple of explicit ``_seed(...)`` lines.
    """
    from sqlalchemy import text

    from app.database import get_engine

    row_id = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO model_calls
                    (id, deal_id, tenant_id, agent_name, model,
                     input_tokens, output_tokens,
                     cache_read_tokens, cache_creation_tokens,
                     cost_usd, latency_ms, trace_id, status, created_at)
                VALUES
                    (:id, :deal_id, :tenant_id, :agent_name, :model,
                     :input_tokens, :output_tokens,
                     :cache_read_tokens, :cache_creation_tokens,
                     :cost_usd, :latency_ms, :trace_id, :status, :created_at)
                """
            ),
            {
                "id": row_id,
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "agent_name": agent_name,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                "cost_usd": cost_usd,
                "latency_ms": None,
                "trace_id": None,
                "status": "ok",
                "created_at": created_at or datetime.now(UTC),
            },
        )
    return row_id


async def _wipe() -> None:
    """Truncate model_calls between tests so each case's assertions are
    exact. The default sqlite file persists across the module, so this
    is the cleanest way to isolate."""
    from sqlalchemy import text

    from app.database import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        try:
            await conn.execute(text("DELETE FROM model_calls"))
        except Exception:  # noqa: BLE001 — table may not exist yet
            pass


# ─────────────────────── tests ───────────────────────


@pytest.mark.asyncio
async def test_admin_cost_aggregates_and_scopes_by_tenant() -> None:
    """Two extractor calls + one analyst call for tenant A; one
    junk row for tenant B. Rollup returns A's spend only, breaks it
    down per agent/model/deal, and reports cache_hit_rate."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    await _wipe()

    deal_a1 = str(uuid.uuid4())
    deal_a2 = str(uuid.uuid4())
    # Tenant A — 2x extractor(Sonnet), 1x analyst(Opus).
    # Extractor rows have heavy cache hits (2000 read vs 500 plain in).
    await _seed(
        tenant_id=TENANT_A,
        deal_id=deal_a1,
        agent_name="extractor",
        model="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=2000,
        cache_creation_tokens=0,
    )
    await _seed(
        tenant_id=TENANT_A,
        deal_id=deal_a1,
        agent_name="extractor",
        model="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=2000,
        cache_creation_tokens=0,
    )
    await _seed(
        tenant_id=TENANT_A,
        deal_id=deal_a2,
        agent_name="analyst",
        model="claude-opus-4-7",
        input_tokens=1_000,
        output_tokens=500,
    )
    # Tenant B — must NOT leak into A's rollup.
    await _seed(
        tenant_id=TENANT_B,
        deal_id=str(uuid.uuid4()),
        agent_name="extractor",
        model="claude-sonnet-4-6",
        input_tokens=999_999,
        output_tokens=999_999,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/admin/cost", headers={"X-Tenant-Id": TENANT_A})
        assert r.status_code == 200, r.text
        body = r.json()

    # Tenant scoping — B's row is 999k tokens; A's whole spend is well
    # under a dollar. If B leaked in, cost_usd would jump orders of
    # magnitude.
    assert body["tenant_id"] == TENANT_A
    win30 = body["windows"]["30d"]
    assert win30["calls"] == 3, body
    # 24h window sees the same rows since we just inserted them
    assert body["windows"]["24h"]["calls"] == 3

    # Per-agent breakdown — extractor and analyst both present, sorted
    # by cost desc. Analyst is Opus so it dominates the spend.
    agents = {a["agent"]: a for a in body["by_agent"]}
    assert "extractor" in agents and "analyst" in agents
    assert agents["extractor"]["calls"] == 2
    assert agents["analyst"]["calls"] == 1
    # Analyst (Opus @ $15/$75) beats 2x extractor (Sonnet @ $3/$15
    # with cache) — sanity check the sort order.
    assert body["by_agent"][0]["agent"] == "analyst"

    # Per-model breakdown — one Sonnet bucket + one Opus bucket.
    models = {m["model"]: m for m in body["by_model"]}
    assert "claude-sonnet-4-6" in models
    assert "claude-opus-4-7" in models
    assert models["claude-sonnet-4-6"]["calls"] == 2

    # Per-deal top-10 — two deals, both present, sorted by cost desc.
    deal_ids = [d["deal_id"] for d in body["by_deal"]]
    assert deal_a1 in deal_ids and deal_a2 in deal_ids

    # Cache hit rate — extractor did 4000 cache_read against 1000
    # plain in + 0 cache_create → 4000/5000 = 0.8. The tenant-wide
    # rate is diluted by analyst's 1000 plain in (no cache).
    # → (2000+2000) / (2000+2000 + 500+500 + 1000) = 4000/6000 = 0.667
    assert 0.6 <= win30["cache_hit_rate"] <= 0.7, win30


@pytest.mark.asyncio
async def test_admin_cost_empty_returns_zeros_not_500() -> None:
    """No rows for the tenant → the endpoint returns a well-formed
    empty response. The admin UI renders 'no data yet' off this."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    await _wipe()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/admin/cost",
            headers={"X-Tenant-Id": "33333333-3333-3333-3333-333333333333"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["by_agent"] == []
    assert body["by_model"] == []
    assert body["by_deal"] == []
    assert body["windows"]["24h"]["cost_usd"] == 0
    assert body["windows"]["30d"]["cache_hit_rate"] == 0.0


@pytest.mark.asyncio
async def test_admin_cost_backfill_is_idempotent() -> None:
    """Seed rows with cost_usd = 0 (the pre-June-2026 bug), run
    backfill, verify: (a) rows now have non-zero cost, (b) a second
    run updates zero rows."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _ensure_schema()
    await _wipe()

    # Three zero-cost rows for tenant A.
    for _ in range(3):
        await _seed(
            tenant_id=TENANT_A,
            deal_id=str(uuid.uuid4()),
            agent_name="normalizer",
            model="claude-haiku-4-5",
            input_tokens=1_000,
            output_tokens=500,
            cost_usd=0.0,
        )
    # One row for tenant B — must NOT be touched.
    b_id = await _seed(
        tenant_id=TENANT_B,
        deal_id=str(uuid.uuid4()),
        agent_name="normalizer",
        model="claude-haiku-4-5",
        input_tokens=1_000,
        output_tokens=500,
        cost_usd=0.0,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First run — updates all three of tenant A's rows.
        r = await client.post(
            "/admin/cost/backfill", headers={"X-Tenant-Id": TENANT_A}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] == 3, body
        assert body["scanned"] == 3

        # Second run — everything's already priced. Zero updates.
        r2 = await client.post(
            "/admin/cost/backfill", headers={"X-Tenant-Id": TENANT_A}
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["updated"] == 0

        # Tenant B's row is still zero — the backfill did not cross
        # tenant boundaries.
        from sqlalchemy import text

        from app.database import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT cost_usd FROM model_calls WHERE id = :id"),
                    {"id": b_id},
                )
            ).first()
        assert row is not None
        assert float(row[0] or 0) == 0.0

        # And tenant A's rollup now surfaces non-zero spend.
        r3 = await client.get(
            "/admin/cost", headers={"X-Tenant-Id": TENANT_A}
        )
        assert r3.status_code == 200
        assert r3.json()["windows"]["30d"]["cost_usd"] > 0
