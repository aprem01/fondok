"""Idempotent in-process migrations.

The worker applies these on startup so a Railway/Fly redeploy can ship
schema changes without an Alembic step. Every entry here MUST be:

    * Idempotent (re-running is a no-op)
    * Backwards-compatible (an older worker pod must still boot)
    * Cheap (a few ALTER TABLEs at startup, not a multi-minute lock)

Each entry is a single SQL statement. ``CREATE TABLE IF NOT EXISTS`` /
``CREATE INDEX IF NOT EXISTS`` / ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` are the safe forms. The append-only audit_log trigger is
guarded by a pg_trigger lookup so re-creation is a no-op.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from .config import get_settings
from .database import get_engine

logger = logging.getLogger(__name__)


# Postgres-flavored DDL. SQLite (dev) skips these — see
# ``run_startup_migrations`` for the dialect guard.
MIGRATIONS: list[tuple[str, str]] = [
    (
        "deals.create_table",
        """
        CREATE TABLE IF NOT EXISTS deals (
            id              UUID PRIMARY KEY,
            tenant_id       UUID NOT NULL,
            name            TEXT NOT NULL,
            city            TEXT,
            keys            INTEGER,
            service         TEXT,
            status          TEXT NOT NULL DEFAULT 'Draft',
            deal_stage      TEXT,
            risk            TEXT,
            ai_confidence   NUMERIC(5,2),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "deals.idx_tenant",
        """
        CREATE INDEX IF NOT EXISTS idx_deals_tenant
        ON deals (tenant_id, created_at DESC)
        """,
    ),
    (
        "documents.create_table",
        """
        CREATE TABLE IF NOT EXISTS documents (
            id               UUID PRIMARY KEY,
            deal_id          UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            tenant_id        UUID NOT NULL,
            filename         TEXT NOT NULL,
            doc_type         TEXT,
            status           TEXT NOT NULL DEFAULT 'uploaded',
            uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            extraction_data  JSONB
        )
        """,
    ),
    (
        "documents.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_documents_deal
        ON documents (deal_id, uploaded_at DESC)
        """,
    ),
    (
        "documents.idx_tenant",
        """
        CREATE INDEX IF NOT EXISTS idx_documents_tenant
        ON documents (tenant_id)
        """,
    ),
    (
        "audit_log.create_table",
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id             UUID PRIMARY KEY,
            tenant_id      UUID NOT NULL,
            deal_id        UUID,
            actor_id       TEXT,
            action         TEXT NOT NULL,
            resource_type  TEXT NOT NULL,
            resource_id    TEXT,
            payload        JSONB,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "audit_log.idx_tenant_created",
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_created
        ON audit_log (tenant_id, created_at DESC)
        """,
    ),
    (
        "audit_log.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_deal
        ON audit_log (deal_id)
        """,
    ),
    (
        "audit_log.append_only_fn",
        """
        CREATE OR REPLACE FUNCTION audit_log_block_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (operation: %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "audit_log.append_only_trigger",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_no_update_delete'
            ) THEN
                CREATE TRIGGER audit_log_no_update_delete
                BEFORE UPDATE OR DELETE ON audit_log
                FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
            END IF;
        END
        $$
        """,
    ),
]


async def run_startup_migrations() -> None:
    """Apply every entry in ``MIGRATIONS`` against the live DB.

    Skips silently when the active engine is SQLite (dev mode) — the
    Postgres-flavored DDL above (UUID, JSONB, plpgsql) won't parse, and
    local dev doesn't need the production schema to test routing.
    """
    settings = get_settings()
    if settings.async_database_url.startswith("sqlite"):
        logger.info(
            "migrations: sqlite detected — skipping Postgres DDL "
            "(set DATABASE_URL=postgresql+asyncpg://... for prod schema)"
        )
        return

    engine = get_engine()
    async with engine.begin() as conn:
        for name, sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                logger.info("migration applied: %s", name)
            except Exception as exc:
                logger.exception("migration failed: %s — %s", name, exc)


__all__ = ["MIGRATIONS", "run_startup_migrations"]
