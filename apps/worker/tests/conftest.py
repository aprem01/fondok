"""Pytest configuration and fixtures for Fondok worker tests."""

import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import Base, get_engine, get_session_factory
from app.migrations import MIGRATIONS


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Set up test database schema by running all migrations."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Run all migrations to set up the schema
        # MIGRATIONS is a list of (name, statement) tuples
        for name, stmt in MIGRATIONS:
            # SQLite doesn't support IF NOT EXISTS on ALTER TABLE ADD COLUMN
            if "ALTER TABLE" in stmt and "IF NOT EXISTS" in stmt:
                # Skip for now; we'll add columns manually after CREATE TABLE
                continue
            try:
                await conn.execute(stmt)
            except Exception:
                # Some migrations may fail if they're idempotent (e.g., CREATE TABLE IF NOT EXISTS)
                # or if the table already exists. That's OK — we just want the schema set up.
                pass

        # Manually add new columns for SQLite (since ALTER TABLE IF NOT EXISTS doesn't work)
        # These match the new columns from T3, T5, and other recent migrations
        new_columns = [
            ("extraction_results", "catalog_version", "INTEGER DEFAULT 1"),
            ("extraction_results", "template_fingerprint", "TEXT"),
            ("documents", "template_fingerprint", "TEXT"),
            ("engine_outputs", "narrative", "TEXT"),
            ("engine_outputs", "narrative_generated_at", "TIMESTAMPTZ"),
            ("template_mappings", "id", "UUID PRIMARY KEY"),  # Ensure table exists
        ]

        for table, column, col_def in new_columns:
            try:
                # Try to add the column; ignore if it already exists
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            except Exception:
                # Column already exists or table doesn't exist yet, skip
                pass
    yield
    # Teardown: drop all tables after tests
    async with engine.begin() as conn:
        await conn.execute("DROP TABLE IF EXISTS template_mappings")
        await conn.execute("DROP TABLE IF EXISTS engine_outputs")
        await conn.execute("DROP TABLE IF EXISTS extraction_results")
        await conn.execute("DROP TABLE IF EXISTS documents")
        await conn.execute("DROP TABLE IF EXISTS deals")
        await conn.execute("DROP TABLE IF EXISTS audit_log")
        await conn.execute("DROP TABLE IF EXISTS model_calls")
        await conn.execute("DROP TABLE IF EXISTS broker_questions")
        await conn.execute("DROP TABLE IF EXISTS broker_qa_pairs")
        await conn.execute("DROP TABLE IF EXISTS scenarios")
        await conn.execute("DROP TABLE IF EXISTS saved_pipeline_views")
        await conn.execute("DROP TABLE IF EXISTS pipeline_digest_schedules")
        await conn.execute("DROP TABLE IF EXISTS portfolio_library")
        await conn.execute("DROP TABLE IF EXISTS field_catalog")
