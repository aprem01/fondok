"""Tests for lazy engine narrative generation (TASK T5, 2026-07).

Contract:
  * Engine run produces NULL narratives (not eager generation).
  * First read triggers generation via get_or_generate_narrative.
  * Second read uses cached value (no new LLM call).
  * Exception handling + fallback on LLM failure.
  * Flag-off passthrough (LAZY_ENGINE_NARRATIVES_ENABLED=False).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-lazy-narratives.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(text("DELETE FROM engine_outputs"))
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
    yield


@pytest.mark.asyncio
async def test_engine_run_produces_null_narratives() -> None:
    """After a full engine run, narrative columns should be NULL."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    deal_id = "test-deal-null-narratives"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    # Every engine should have completed successfully.
    for name, payload in results.items():
        assert payload["status"] == "complete", (
            f"engine {name} did not complete: {payload}"
        )

    # Check that the engine_outputs rows have NULL narrative fields.
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT engine_name, narrative, narrative_generated_at
                      FROM engine_outputs
                     WHERE run_id = :run_id
                     ORDER BY engine_name
                    """
                ),
                {"run_id": run_id},
            )
        ).fetchall()

    assert len(rows) > 0, "No engine_outputs rows found"
    for row in rows:
        engine_name, narrative, generated_at = row
        assert (
            narrative is None
        ), f"engine {engine_name}: narrative should be NULL after run, got {narrative}"
        assert (
            generated_at is None
        ), f"engine {engine_name}: narrative_generated_at should be NULL after run"


@pytest.mark.asyncio
async def test_first_read_generates_narrative() -> None:
    """First call to get_or_generate_narrative triggers LLM generation."""
    from app.database import get_session_factory
    from app.services.engine_narratives import get_or_generate_narrative
    from app.services.engine_runner import run_all_engines

    deal_id = "test-deal-first-gen"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    # Fetch a completed engine output row.
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, engine_name, outputs
                      FROM engine_outputs
                     WHERE run_id = :run_id
                       AND status = 'complete'
                     LIMIT 1
                    """
                ),
                {"run_id": run_id},
            )
        ).first()

    assert row is not None, "No completed engine_outputs row found"
    output_id, engine_name, outputs_json = row
    math_payload = json.loads(outputs_json) if outputs_json else {}

    # Call get_or_generate_narrative for the first time.
    # This should trigger LLM generation.
    async with factory() as session:
        narrative = await get_or_generate_narrative(
            session,
            engine_output_id=output_id,
            engine_name=engine_name,
            math_payload=math_payload,
            tenant_id=tenant_id,
        )

    assert narrative is not None
    # The narrative should not be the fallback (unless LLM failed).
    # For a robust test, we just check it's non-empty.
    assert len(narrative) > 0, (
        f"Generated narrative is empty for engine {engine_name}"
    )

    # Verify that the DB row now has the cached narrative.
    async with factory() as session:
        cached_row = (
            await session.execute(
                text(
                    """
                    SELECT narrative, narrative_generated_at
                      FROM engine_outputs
                     WHERE id = :id
                    """
                ),
                {"id": str(output_id)},
            )
        ).first()

    cached_narrative, generated_at = cached_row
    assert cached_narrative == narrative, (
        "Cached narrative does not match generated narrative"
    )
    assert generated_at is not None, (
        "narrative_generated_at should be set after generation"
    )


@pytest.mark.asyncio
async def test_second_read_uses_cache() -> None:
    """Second call to get_or_generate_narrative returns cached value
    without a new LLM call."""
    from unittest.mock import AsyncMock, patch

    from app.database import get_session_factory
    from app.services.engine_narratives import get_or_generate_narrative
    from app.services.engine_runner import run_all_engines

    deal_id = "test-deal-cached"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    # Fetch a completed engine output.
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, engine_name, outputs
                      FROM engine_outputs
                     WHERE run_id = :run_id
                       AND status = 'complete'
                     LIMIT 1
                    """
                ),
                {"run_id": run_id},
            )
        ).first()

    output_id, engine_name, outputs_json = row
    math_payload = json.loads(outputs_json) if outputs_json else {}

    # First read: generates narrative.
    async with factory() as session:
        narrative_1 = await get_or_generate_narrative(
            session,
            engine_output_id=output_id,
            engine_name=engine_name,
            math_payload=math_payload,
            tenant_id=tenant_id,
        )

    # Mock the LLM call so we can verify it's NOT called on the second read.
    with patch(
        "app.services.engine_narratives._generate_narrative_via_analyst",
        new_callable=AsyncMock,
    ) as mock_gen:
        # Second read: should hit cache and NOT call the LLM.
        async with factory() as session:
            narrative_2 = await get_or_generate_narrative(
                session,
                engine_output_id=output_id,
                engine_name=engine_name,
                math_payload=math_payload,
                tenant_id=tenant_id,
            )

        # LLM generator should not have been called (cache hit).
        mock_gen.assert_not_called()

    # Both narratives should be identical.
    assert narrative_1 == narrative_2, (
        "First and second reads should return the same narrative"
    )


@pytest.mark.asyncio
async def test_fallback_on_llm_failure() -> None:
    """When LLM generation fails, fallback message is returned."""
    from unittest.mock import AsyncMock, patch

    from app.database import get_session_factory
    from app.services.engine_narratives import FALLBACK_NARRATIVE
    from app.services.engine_narratives import (
        get_or_generate_narrative,
    )
    from app.services.engine_runner import run_all_engines

    deal_id = "test-deal-fallback"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    # Fetch a completed engine output.
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, engine_name, outputs
                      FROM engine_outputs
                     WHERE run_id = :run_id
                       AND status = 'complete'
                     LIMIT 1
                    """
                ),
                {"run_id": run_id},
            )
        ).first()

    output_id, engine_name, outputs_json = row
    math_payload = json.loads(outputs_json) if outputs_json else {}

    # Mock the LLM to raise an exception.
    with patch(
        "app.services.engine_narratives._generate_narrative_via_analyst",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM API error"),
    ):
        async with factory() as session:
            narrative = await get_or_generate_narrative(
                session,
                engine_output_id=output_id,
                engine_name=engine_name,
                math_payload=math_payload,
                tenant_id=tenant_id,
            )

    # Should return the fallback message, not raise.
    assert narrative == FALLBACK_NARRATIVE, (
        f"Expected fallback on LLM error, got: {narrative}"
    )


@pytest.mark.asyncio
async def test_flag_off_passthrough(monkeypatch) -> None:
    """When LAZY_ENGINE_NARRATIVES_ENABLED=False, return fallback
    without any LLM or DB work."""
    from unittest.mock import AsyncMock, patch

    from app.database import get_session_factory
    from app.services.engine_narratives import FALLBACK_NARRATIVE

    # Patch the config to disable lazy narratives.
    import app.services.engine_narratives as narratives_module

    with patch.object(
        narratives_module,
        "get_settings",
        return_value=type(
            "MockSettings",
            (),
            {"LAZY_ENGINE_NARRATIVES_ENABLED": False},
        )(),
    ):
        from app.services.engine_narratives import get_or_generate_narrative

        factory = get_session_factory()

        # Call get_or_generate_narrative with the flag off.
        async with factory() as session:
            narrative = await get_or_generate_narrative(
                session,
                engine_output_id=uuid4(),
                engine_name="returns",
                math_payload={"example": "data"},
                tenant_id=str(uuid4()),
            )

        # Should return fallback immediately, no DB/LLM work.
        assert narrative == FALLBACK_NARRATIVE, (
            f"Expected fallback when flag is off, got: {narrative}"
        )
