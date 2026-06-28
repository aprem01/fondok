"""Saved pipeline views + scheduled digests (Wave 4 W4.5).

Drives the W4.5 surface end-to-end against a real SQLite DB:

* ``/pipeline-views`` CRUD + default-pinning semantics.
* ``/pipeline-digests`` CRUD + ``/run-now`` dispatch.
* ``services.pipeline_digest.compute_next_run_at`` cadence math.
* ``services.pipeline_digest.dispatch_digest`` fan-out, including
  the no-op + 500-mocked-webhook + email-backend-default paths.
* ``services.digest_scheduler.tick_once`` — picks up overdue
  schedules without a real sleep.

Pattern mirrors ``test_pipeline.py``: pin the SQLite DB *before* any
``app.*`` import so the cached Settings / engine pick up the right
DSN, then reset the schema between tests via the autouse fixture.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

# Pin per-test SQLite DB BEFORE any app.* import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-pipeline-digests.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")
# Tests drive the scheduler tick by hand — keep the background loop off.
os.environ["DIGEST_SCHEDULER_ENABLED"] = "False"
# Force the email backend to log_only for every test (avoids touching
# any optional SendGrid credentials a dev env might have).
os.environ["EMAIL_BACKEND"] = "log_only"


_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.pipeline import invalidate as pipeline_invalidate

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "pipeline_digest_schedules",
            "saved_pipeline_views",
            "audit_log",
            "engine_outputs",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    pipeline_invalidate()
    yield


# ─────────────────────────── helpers ───────────────────────────


async def _insert_deal(
    session,
    *,
    name: str,
    tenant: str = _DEFAULT_TENANT,
    keys: int = 200,
    purchase_price: float = 30_000_000.0,
    state: str = "VALIDATING",
    target_irr: float | None = None,
    updated_at: datetime | None = None,
    status: str = "Active",
    deal_stage: str = "Active",
) -> str:
    from sqlalchemy import text

    deal_id = str(uuid4())
    now = (updated_at or datetime.now(UTC)).isoformat()
    await session.execute(
        text(
            """
            INSERT INTO deals (
                id, tenant_id, name, city, keys, service, status,
                deal_stage, risk, ai_confidence, return_profile, brand,
                positioning, purchase_price, sourcing_channel,
                target_irr, state, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, NULL, :keys, NULL, :status,
                :deal_stage, NULL, 0.0, NULL, NULL,
                NULL, :pp, NULL,
                :target_irr, :state, :created_at, :updated_at
            )
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant,
            "name": name,
            "keys": keys,
            "status": status,
            "deal_stage": deal_stage,
            "pp": purchase_price,
            "target_irr": target_irr,
            "state": state,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.commit()
    return deal_id


async def _insert_engine_output(
    session,
    *,
    deal_id: str,
    engine_name: str,
    outputs: dict,
    inputs: dict | None = None,
    tenant: str = _DEFAULT_TENANT,
) -> None:
    from sqlalchemy import text

    ts = datetime.now(UTC).isoformat()
    await session.execute(
        text(
            """
            INSERT INTO engine_outputs (
                id, deal_id, tenant_id, run_id, engine_name, status,
                inputs, outputs, error, started_at, completed_at,
                runtime_ms
            ) VALUES (
                :id, :deal, :tenant, :run, :engine, 'complete',
                :inputs, :outputs, NULL, :ts, :ts, 10
            )
            """
        ),
        {
            "id": str(uuid4()),
            "deal": deal_id,
            "tenant": tenant,
            "run": str(uuid4()),
            "engine": engine_name,
            "inputs": json.dumps(inputs or {}),
            "outputs": json.dumps(outputs),
            "ts": ts,
        },
    )
    await session.commit()


async def _seed_deal_with_irr(
    session,
    *,
    name: str,
    irr: float,
    target_irr: float | None = None,
    price_per_key: float = 150_000.0,
    state: str = "VALIDATING",
    updated_at: datetime | None = None,
) -> str:
    deal_id = await _insert_deal(
        session,
        name=name,
        state=state,
        target_irr=target_irr,
        updated_at=updated_at,
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="returns",
        outputs={
            "deal_id": deal_id,
            "levered_irr": irr,
            "unlevered_irr": irr - 0.04,
            "equity_multiple": 2.0,
            "year_one_coc": 0.05,
            "avg_coc": 0.06,
            "gross_sale_price": 60_000_000.0,
            "selling_costs": 1_200_000.0,
            "net_proceeds": 25_000_000.0,
            "hold_years": 5,
        },
        inputs={"assumptions": {"exit_cap_rate": 0.075}},
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="capital",
        outputs={
            "deal_id": deal_id,
            "total_capital": 30_000_000.0,
            "price_per_key": price_per_key,
            "sources": [],
            "uses": [],
            "debt_amount": 20_000_000.0,
            "equity_amount": 10_000_000.0,
            "ltc": 0.66,
        },
        inputs={"renovation_budget": 0.0},
    )
    await _insert_engine_output(
        session,
        deal_id=deal_id,
        engine_name="expense",
        outputs={
            "deal_id": deal_id,
            "noi_cagr": 0.03,
            "sourced_from_t12": [],
            "years": [
                {"year": 1, "noi": 4_500_000.0, "noi_institutional": 4_500_000.0},
                {"year": 2, "noi": 5_000_000.0, "noi_institutional": 5_000_000.0},
            ],
        },
    )
    return deal_id


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-Id": _DEFAULT_TENANT},
    )


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_create_saved_view_persists() -> None:
    """POST /pipeline-views round-trips through the DB."""
    async with _client() as client:
        resp = await client.post(
            "/pipeline-views",
            json={
                "name": "US Deals over $30M",
                "description": "Active US pipeline above 30M purchase price",
                "filter": {
                    "state": ["VALIDATING", "READY"],
                    "min_irr": 0.15,
                    "sort": "irr_desc",
                },
                "is_owner_default": False,
                "created_by": "user_abc",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "US Deals over $30M"
        assert body["filter"]["min_irr"] == 0.15
        assert body["filter"]["state"] == ["VALIDATING", "READY"]
        assert body["filter"]["sort"] == "irr_desc"
        assert body["created_by"] == "user_abc"

        # Re-fetch — confirms persistence (not just echo)
        got = await client.get(f"/pipeline-views/{body['id']}")
        assert got.status_code == 200
        assert got.json()["name"] == "US Deals over $30M"


@pytest.mark.asyncio
async def test_default_view_unique_per_tenant() -> None:
    """Pinning a new default unpins the previous one for the same actor."""
    async with _client() as client:
        a = await client.post(
            "/pipeline-views",
            json={
                "name": "View A",
                "filter": {"sort": "irr_desc"},
                "is_owner_default": True,
                "created_by": "user_1",
            },
        )
        b = await client.post(
            "/pipeline-views",
            json={
                "name": "View B",
                "filter": {"sort": "name_asc"},
                "is_owner_default": True,
                "created_by": "user_1",
            },
        )
        assert a.status_code == 201
        assert b.status_code == 201

        # B should be the only default now.
        rows = (await client.get("/pipeline-views")).json()
        names_to_default = {r["name"]: r["is_owner_default"] for r in rows}
        assert names_to_default["View A"] is False
        assert names_to_default["View B"] is True

        # set-default endpoint also flips A back, unpinning B
        flipped = await client.post(
            f"/pipeline-views/{a.json()['id']}/set-default"
        )
        assert flipped.status_code == 200
        assert flipped.json()["is_owner_default"] is True
        rows2 = {r["name"]: r["is_owner_default"]
                 for r in (await client.get("/pipeline-views")).json()}
        assert rows2["View A"] is True
        assert rows2["View B"] is False


@pytest.mark.asyncio
async def test_unique_name_per_tenant() -> None:
    """Two views with the same name in the same tenant -> 409."""
    async with _client() as client:
        first = await client.post(
            "/pipeline-views",
            json={"name": "DupName", "filter": {}, "created_by": "u"},
        )
        second = await client.post(
            "/pipeline-views",
            json={"name": "DupName", "filter": {}, "created_by": "u"},
        )
        assert first.status_code == 201
        assert second.status_code == 409


@pytest.mark.asyncio
async def test_apply_filter_returns_subset_of_pipeline() -> None:
    """Saved filter applied to snapshot returns the expected subset."""
    from app.database import get_session_factory
    from app.services.pipeline import build_pipeline_snapshot
    from app.services.pipeline_digest import apply_filter_dict

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_irr(
            session, name="High IRR", irr=0.22, state="VALIDATING"
        )
        await _seed_deal_with_irr(
            session, name="Low IRR", irr=0.08, state="VALIDATING"
        )
        await _seed_deal_with_irr(
            session, name="Wrong State", irr=0.20, state="ONBOARDING"
        )

    async with factory() as session:
        snap = await build_pipeline_snapshot(
            session, tenant_id=UUID(_DEFAULT_TENANT)
        )
    # Only High IRR matches: >= 0.15 AND state in [VALIDATING]
    filtered = apply_filter_dict(
        snap, filter_dict={"state": ["VALIDATING"], "min_irr": 0.15}
    )
    names = {r["name"] for r in filtered}
    assert names == {"High IRR"}


@pytest.mark.asyncio
async def test_digest_schedule_cadence_daily_next_run() -> None:
    """Daily cadence rolls to tomorrow when today's hour has passed."""
    from app.services.pipeline_digest import compute_next_run_at

    now = datetime(2026, 6, 28, 18, 0, tzinfo=UTC)
    # hour_utc=13 already passed today (18:00) → tomorrow at 13:00
    nxt = compute_next_run_at(cadence="daily", hour_utc=13, weekday=None, now=now)
    assert nxt == datetime(2026, 6, 29, 13, 0, tzinfo=UTC)

    # hour_utc=22 still ahead today → today at 22:00
    nxt2 = compute_next_run_at(cadence="daily", hour_utc=22, weekday=None, now=now)
    assert nxt2 == datetime(2026, 6, 28, 22, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_digest_schedule_cadence_weekly_weekday_respected() -> None:
    """Weekly cadence lands on the requested weekday + hour."""
    from app.services.pipeline_digest import compute_next_run_at

    # Sunday 2026-06-28 (weekday 6) at 10:00 UTC
    now = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)

    # Tuesday (weekday=1) at 13 UTC: next Tuesday is 2026-06-30 13:00
    nxt = compute_next_run_at(cadence="weekly", hour_utc=13, weekday=1, now=now)
    assert nxt.weekday() == 1
    assert nxt == datetime(2026, 6, 30, 13, 0, tzinfo=UTC)

    # Monday (weekday=0) at 13 UTC: same week → 2026-06-29 13:00
    nxt2 = compute_next_run_at(cadence="weekly", hour_utc=13, weekday=0, now=now)
    assert nxt2.weekday() == 0
    assert nxt2 == datetime(2026, 6, 29, 13, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_digest_payload_includes_recently_mutated_when_flag_set() -> None:
    """include_recently_mutated=true populates the section; false omits it."""
    from app.database import get_session_factory
    from app.services.pipeline_digest import build_digest_payload

    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        await _seed_deal_with_irr(
            session, name="JustNow", irr=0.18, updated_at=now
        )
        await _seed_deal_with_irr(
            session,
            name="Old Deal",
            irr=0.15,
            updated_at=now - timedelta(days=10),
        )

    schedule_on = {
        "id": str(uuid4()),
        "tenant_id": _DEFAULT_TENANT,
        "cadence": "daily",
        "include_kpi_summary": True,
        "include_recently_mutated": True,
        "include_deals_meeting_target": False,
        "include_full_table": False,
    }
    async with factory() as session:
        payload_on = await build_digest_payload(
            session,
            tenant_id=_DEFAULT_TENANT,
            schedule=schedule_on,
            now=now,
        )
    names_on = {r["name"] for r in payload_on.recently_mutated}
    assert "JustNow" in names_on
    assert "Old Deal" not in names_on  # cutoff for daily = 24h

    schedule_off = {**schedule_on, "include_recently_mutated": False}
    async with factory() as session:
        payload_off = await build_digest_payload(
            session,
            tenant_id=_DEFAULT_TENANT,
            schedule=schedule_off,
            now=now,
        )
    assert payload_off.recently_mutated == []


@pytest.mark.asyncio
async def test_digest_payload_kpi_block_matches_pipeline_summary() -> None:
    """KPI block in the digest mirrors services.pipeline.build_summary."""
    from app.database import get_session_factory
    from app.services.pipeline import build_pipeline_snapshot, build_summary
    from app.services.pipeline_digest import (
        apply_filter_dict,
        build_digest_payload,
    )

    factory = get_session_factory()
    async with factory() as session:
        await _seed_deal_with_irr(
            session, name="A", irr=0.20, target_irr=0.18
        )
        await _seed_deal_with_irr(
            session, name="B", irr=0.12, target_irr=0.18
        )
        await _seed_deal_with_irr(
            session, name="C", irr=0.16
        )

    schedule = {
        "id": str(uuid4()),
        "tenant_id": _DEFAULT_TENANT,
        "cadence": "daily",
        "include_kpi_summary": True,
        "include_recently_mutated": False,
        "include_deals_meeting_target": False,
        "include_full_table": False,
    }
    async with factory() as session:
        payload = await build_digest_payload(
            session,
            tenant_id=_DEFAULT_TENANT,
            schedule=schedule,
        )
        snap = await build_pipeline_snapshot(
            session, tenant_id=UUID(_DEFAULT_TENANT)
        )

    expected = build_summary(apply_filter_dict(snap, filter_dict=None))
    assert payload.kpi_block is not None
    assert payload.kpi_block["deal_count"] == expected["deal_count"]
    assert payload.kpi_block["median_irr"] == expected["median_irr"]
    assert (
        payload.kpi_block["deals_meeting_target_irr"]
        == expected["deals_meeting_target_irr"]
    )


@pytest.mark.asyncio
async def test_format_slack_message_block_kit_shape() -> None:
    """format_slack_message returns valid Slack Block Kit shape."""
    from app.services.pipeline_digest import (
        DigestPayload,
        format_slack_message,
    )

    payload = DigestPayload(
        title="Pipeline digest — 2026-06-28",
        subtitle="All active deals · 3 deal(s)",
        generated_at=datetime.now(UTC),
        cadence="daily",
        deal_count=3,
        kpi_block={
            "deal_count": 3,
            "median_irr": 0.18,
            "median_per_key": 150_000.0,
            "median_cap_rate": 0.075,
            "deals_meeting_target_irr": 2,
            "deals_with_target_irr": 3,
        },
        recently_mutated=[
            {"name": "Foo", "levered_irr": 0.20, "equity_multiple": 2.1}
        ],
        deals_meeting_target=[
            {
                "name": "Bar",
                "levered_irr": 0.22,
                "target_irr": 0.18,
                "equity_multiple": 2.4,
            }
        ],
    )
    msg = format_slack_message(payload)
    assert isinstance(msg, dict)
    assert msg["text"] == payload.title
    assert isinstance(msg["blocks"], list)
    assert len(msg["blocks"]) >= 4
    # Header block must be first
    assert msg["blocks"][0]["type"] == "header"
    # Every section block must have a text element with a known type
    for blk in msg["blocks"]:
        assert blk["type"] in (
            "header", "section", "divider", "context"
        )
        if blk["type"] == "section":
            assert "text" in blk
            assert blk["text"]["type"] in ("mrkdwn", "plain_text")


@pytest.mark.asyncio
async def test_run_now_endpoint_fires_dispatch_path() -> None:
    """POST /pipeline-digests/{id}/run-now invokes dispatch and stamps last_run_at."""
    async with _client() as client:
        # Create a schedule with a slack webhook URL
        created = await client.post(
            "/pipeline-digests",
            json={
                "name": "Daily AM",
                "cadence": "daily",
                "hour_utc": 13,
                "delivery": "slack",
                "slack_webhook_url": "https://hooks.slack.com/services/T0/B0/abc",
                "include_kpi_summary": True,
            },
        )
        assert created.status_code == 201, created.text
        schedule_id = created.json()["id"]

        # Patch the post boundary so we don't hit the network
        with patch(
            "app.services.pipeline_digest._post_slack",
            return_value=(True, None),
        ) as mock_post:
            resp = await client.post(
                f"/pipeline-digests/{schedule_id}/run-now"
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["slack_attempted"] is True
        assert body["slack_succeeded"] is True
        assert mock_post.call_count == 1

        # last_run_at is now set
        got = await client.get("/pipeline-digests")
        rows = got.json()
        assert any(r["id"] == schedule_id and r["last_run_at"] for r in rows)


@pytest.mark.asyncio
async def test_dispatch_no_op_when_no_webhook_or_email_backend() -> None:
    """A schedule with delivery=slack but no webhook → no-op result."""
    from app.services.pipeline_digest import (
        DigestPayload,
        dispatch_digest,
    )

    payload = DigestPayload(
        title="x",
        subtitle="y",
        generated_at=datetime.now(UTC),
        cadence="daily",
    )
    result = dispatch_digest(
        {
            "id": "abc",
            "delivery": "slack",
            "slack_webhook_url": "",  # missing
            "email_recipients": [],
        },
        payload,
    )
    assert result.no_op_reason == "slack webhook missing"
    assert result.slack_succeeded is False
    assert result.email_attempted is False


@pytest.mark.asyncio
async def test_dispatch_logs_failure_but_does_not_raise_when_webhook_500s() -> None:
    """A 500 from Slack is logged + reported via result, never raised."""
    import urllib.error

    from app.services.pipeline_digest import (
        DigestPayload,
        dispatch_digest,
    )

    payload = DigestPayload(
        title="x",
        subtitle="y",
        generated_at=datetime.now(UTC),
        cadence="daily",
    )

    def _raise_500(*_args, **_kwargs):
        # Mirrors what urlopen does for a 5xx response
        raise urllib.error.HTTPError(
            "http://x", 500, "server error", {}, None
        )

    with patch(
        "app.services.pipeline_digest.urlrequest.urlopen",
        side_effect=_raise_500,
    ):
        result = dispatch_digest(
            {
                "id": "abc",
                "delivery": "slack",
                "slack_webhook_url": "https://hooks.slack.com/x",
                "email_recipients": [],
            },
            payload,
        )
    assert result.slack_attempted is True
    assert result.slack_succeeded is False
    assert result.slack_error is not None
    assert "500" in result.slack_error or "HTTPError" in result.slack_error


@pytest.mark.asyncio
async def test_email_backend_log_only_default() -> None:
    """EMAIL_BACKEND=log_only is the default and 'sends' successfully."""
    from app.config import get_settings
    from app.services.pipeline_digest import (
        DigestPayload,
        dispatch_digest,
    )

    settings = get_settings()
    assert settings.EMAIL_BACKEND == "log_only"

    payload = DigestPayload(
        title="x",
        subtitle="y",
        generated_at=datetime.now(UTC),
        cadence="daily",
    )
    result = dispatch_digest(
        {
            "id": "abc",
            "delivery": "email",
            "slack_webhook_url": "",
            "email_recipients": ["analyst@fondok.app"],
        },
        payload,
    )
    assert result.email_attempted is True
    assert result.email_succeeded is True
    assert result.email_error is None


@pytest.mark.asyncio
async def test_scheduler_picks_up_overdue_schedule_in_under_a_minute() -> None:
    """tick_once dispatches schedules whose next_run_at is in the past."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.services.digest_scheduler import tick_once

    factory = get_session_factory()
    # Seed a deal so the digest has something to summarize
    async with factory() as session:
        await _seed_deal_with_irr(session, name="Solo", irr=0.18)

    # Insert a schedule whose next_run_at is in the past (5m ago)
    past = datetime.now(UTC) - timedelta(minutes=5)
    schedule_id = str(uuid4())
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO pipeline_digest_schedules (
                    id, tenant_id, name, saved_view_id, cadence, weekday,
                    hour_utc, delivery, slack_webhook_url, email_recipients,
                    include_kpi_summary, include_recently_mutated,
                    include_deals_meeting_target, include_full_table,
                    is_active, next_run_at, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, NULL, 'daily', NULL,
                    13, 'slack', :slack, '[]',
                    1, 0, 0, 0,
                    1, :next_run, :now, :now
                )
                """
            ),
            {
                "id": schedule_id,
                "tenant": _DEFAULT_TENANT,
                "name": "Overdue",
                "slack": "https://hooks.slack.com/services/T0/B0/x",
                "next_run": past.isoformat(),
                "now": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

    with patch(
        "app.services.pipeline_digest._post_slack",
        return_value=(True, None),
    ) as mock_post:
        count = await tick_once()

    assert count == 1
    assert mock_post.call_count == 1

    # next_run_at advanced into the future
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT next_run_at, last_run_at FROM "
                    "pipeline_digest_schedules WHERE id = :id"
                ),
                {"id": schedule_id},
            )
        ).first()
    assert row is not None
    nxt = row._mapping["next_run_at"]
    last = row._mapping["last_run_at"]
    assert last is not None
    # Coerce SQLite TEXT to datetime
    if isinstance(nxt, str):
        nxt = datetime.fromisoformat(nxt.replace("Z", "+00:00"))
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=UTC)
    assert nxt > datetime.now(UTC)
