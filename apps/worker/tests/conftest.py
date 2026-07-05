"""Pytest configuration and fixtures for Fondok worker tests."""

import asyncio

import pytest
from sqlalchemy import text

from app.database import get_engine
from app.migrations import SQLITE_MIGRATIONS


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Set up the shared test schema by running the sqlite migrations.

    HOTFIX 2026-07-05: the original version (d632c37) passed raw
    strings to ``conn.execute`` — SQLAlchemy 2.x requires ``text()``,
    so every statement raised AttributeError. Setup swallowed those in
    a bare ``except`` (i.e. the whole fixture silently no-op'd), while
    teardown had NO try/except and crashed the alphabetically-last
    test module's teardown. It also iterated the Postgres-flavored
    ``MIGRATIONS`` list against sqlite; the sqlite set exists
    precisely for this.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        for _name, stmt in SQLITE_MIGRATIONS:
            try:
                await conn.execute(text(stmt))
            except Exception:
                # Idempotency: table/column may already exist because a
                # test module created its own schema first. Best-effort.
                pass
    yield
    # Teardown: best-effort drops. Never raise out of a session
    # finalizer — a failed DROP must not fail the last test module.
    drop_tables = [
        "template_mappings",
        "engine_outputs",
        "extraction_results",
        "documents",
        "deals",
        "audit_log",
        "model_calls",
        "broker_questions",
        "broker_qa_pairs",
        "scenarios",
        "saved_pipeline_views",
        "pipeline_digest_schedules",
        "portfolio_library",
        "field_catalog",
        "pending_batches",
        "due_diligence_questions",
    ]
    try:
        async with engine.begin() as conn:
            for table in drop_tables:
                try:
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                except Exception:
                    pass
    except Exception:
        pass
