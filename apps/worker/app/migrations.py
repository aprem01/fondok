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

For SQLite (dev) we run a slimmer DDL set so the documents/extraction
flow works locally without Postgres provisioning.
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
        "deals.add_return_profile",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS return_profile TEXT",
    ),
    (
        "deals.add_brand",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS brand TEXT",
    ),
    (
        "deals.add_positioning",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS positioning TEXT",
    ),
    (
        "deals.add_purchase_price",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS purchase_price NUMERIC(14,2)",
    ),
    (
        "deals.add_assignee_id",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS assignee_id UUID",
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
            status           TEXT NOT NULL DEFAULT 'UPLOADED',
            uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            content_hash     TEXT,
            storage_key      TEXT,
            size_bytes       BIGINT,
            page_count       INTEGER,
            parser           TEXT,
            extraction_data  JSONB
        )
        """,
    ),
    (
        "documents.add_content_hash",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT",
    ),
    (
        "documents.add_storage_key",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_key TEXT",
    ),
    (
        "documents.add_size_bytes",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT",
    ),
    (
        "documents.add_page_count",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER",
    ),
    (
        "documents.add_parser",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser TEXT",
    ),
    (
        "extraction_results.create_table",
        """
        CREATE TABLE IF NOT EXISTS extraction_results (
            id                 UUID PRIMARY KEY,
            document_id        UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            deal_id            UUID NOT NULL,
            tenant_id          UUID NOT NULL,
            fields             JSONB,
            confidence_report  JSONB,
            agent_version      TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "extraction_results.idx_document",
        """
        CREATE INDEX IF NOT EXISTS idx_extraction_results_document
        ON extraction_results (document_id, created_at DESC)
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
    # Tamper-evident hashes — added by the centralized log_audit helper
    # so a Blackstone IT review can prove the row's payload hasn't been
    # silently rewritten downstream.
    (
        "audit_log.add_input_hash",
        "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS input_hash TEXT",
    ),
    (
        "audit_log.add_output_hash",
        "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS output_hash TEXT",
    ),
    (
        "memo_edits.create_table",
        """
        CREATE TABLE IF NOT EXISTS memo_edits (
            id              UUID PRIMARY KEY,
            tenant_id       UUID NOT NULL,
            deal_id         UUID NOT NULL,
            section_id      TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            original_body   TEXT NOT NULL,
            new_body        TEXT NOT NULL,
            comment         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "memo_edits.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_memo_edits_deal
        ON memo_edits (deal_id, created_at DESC)
        """,
    ),
    (
        "memo_edits.idx_deal_section",
        """
        CREATE INDEX IF NOT EXISTS idx_memo_edits_deal_section
        ON memo_edits (deal_id, section_id, created_at DESC)
        """,
    ),
    (
        "memo_edits.append_only_fn",
        """
        CREATE OR REPLACE FUNCTION memo_edits_block_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'memo_edits is append-only (operation: %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "memo_edits.append_only_trigger",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'memo_edits_no_update_delete'
            ) THEN
                CREATE TRIGGER memo_edits_no_update_delete
                BEFORE UPDATE OR DELETE ON memo_edits
                FOR EACH ROW EXECUTE FUNCTION memo_edits_block_mutation();
            END IF;
        END
        $$
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
        "model_calls.create_table",
        """
        CREATE TABLE IF NOT EXISTS model_calls (
            id                          UUID PRIMARY KEY,
            deal_id                     UUID NOT NULL,
            tenant_id                   UUID,
            agent_name                  TEXT NOT NULL,
            model                       TEXT NOT NULL,
            input_tokens                INTEGER NOT NULL DEFAULT 0,
            output_tokens               INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens           INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens       INTEGER NOT NULL DEFAULT 0,
            cost_usd                    NUMERIC(10, 4) NOT NULL DEFAULT 0,
            latency_ms                  INTEGER,
            trace_id                    TEXT,
            status                      TEXT NOT NULL DEFAULT 'ok',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "model_calls.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_model_calls_deal
        ON model_calls (deal_id, created_at DESC)
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
    (
        "verification_reports.create_table",
        """
        CREATE TABLE IF NOT EXISTS verification_reports (
            id            UUID PRIMARY KEY,
            deal_id       UUID NOT NULL,
            tenant_id     UUID NOT NULL,
            pass_rate     NUMERIC(5,4) NOT NULL DEFAULT 0,
            report_json   JSONB NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "verification_reports.idx_deal_created",
        """
        CREATE INDEX IF NOT EXISTS idx_verification_reports_deal_created
        ON verification_reports (deal_id, created_at DESC)
        """,
    ),
    (
        "verification_reports.idx_tenant",
        """
        CREATE INDEX IF NOT EXISTS idx_verification_reports_tenant
        ON verification_reports (tenant_id)
        """,
    ),
    (
        "critic_findings.create_table",
        """
        CREATE TABLE IF NOT EXISTS critic_findings (
            id                    UUID PRIMARY KEY,
            deal_id               UUID NOT NULL,
            tenant_id             UUID NOT NULL,
            rule_id               TEXT NOT NULL,
            title                 TEXT NOT NULL,
            narrative             TEXT NOT NULL,
            severity              TEXT NOT NULL,
            cited_fields          JSONB,
            cited_pages           JSONB,
            impact_estimate_usd   NUMERIC,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "critic_findings.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_critic_findings_deal
        ON critic_findings (deal_id, created_at DESC)
        """,
    ),
    (
        "critic_findings.idx_tenant",
        """
        CREATE INDEX IF NOT EXISTS idx_critic_findings_tenant
        ON critic_findings (tenant_id)
        """,
    ),
    (
        "critic_reports.create_table",
        """
        CREATE TABLE IF NOT EXISTS critic_reports (
            id            UUID PRIMARY KEY,
            deal_id       UUID NOT NULL,
            tenant_id     UUID NOT NULL,
            summary       TEXT,
            report_json   JSONB NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "critic_reports.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_critic_reports_deal
        ON critic_reports (deal_id, created_at DESC)
        """,
    ),
]


# SQLite-flavored DDL for dev / unit tests. UUIDs are stored as TEXT,
# JSONB collapses to TEXT (we encode JSON ourselves), no triggers.
SQLITE_MIGRATIONS: list[tuple[str, str]] = [
    (
        "deals.create_table",
        """
        CREATE TABLE IF NOT EXISTS deals (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            name            TEXT NOT NULL,
            city            TEXT,
            keys            INTEGER,
            service         TEXT,
            status          TEXT NOT NULL DEFAULT 'Draft',
            deal_stage      TEXT,
            risk            TEXT,
            ai_confidence   REAL,
            return_profile  TEXT,
            brand           TEXT,
            positioning     TEXT,
            purchase_price  REAL,
            assignee_id     TEXT,
            created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "deals.idx_tenant",
        "CREATE INDEX IF NOT EXISTS idx_deals_tenant ON deals (tenant_id, created_at DESC)",
    ),
    # SQLite-side additive ALTERs for legacy DBs created before the new
    # columns landed. SQLite raises a duplicate-column error if the
    # column already exists; the migration runner swallows it.
    (
        "deals.add_return_profile_sqlite",
        "ALTER TABLE deals ADD COLUMN return_profile TEXT",
    ),
    (
        "deals.add_brand_sqlite",
        "ALTER TABLE deals ADD COLUMN brand TEXT",
    ),
    (
        "deals.add_positioning_sqlite",
        "ALTER TABLE deals ADD COLUMN positioning TEXT",
    ),
    (
        "deals.add_purchase_price_sqlite",
        "ALTER TABLE deals ADD COLUMN purchase_price REAL",
    ),
    (
        "deals.add_assignee_id_sqlite",
        "ALTER TABLE deals ADD COLUMN assignee_id TEXT",
    ),
    (
        "documents.create_table",
        """
        CREATE TABLE IF NOT EXISTS documents (
            id               TEXT PRIMARY KEY,
            deal_id          TEXT NOT NULL,
            tenant_id        TEXT NOT NULL,
            filename         TEXT NOT NULL,
            doc_type         TEXT,
            status           TEXT NOT NULL DEFAULT 'UPLOADED',
            uploaded_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            content_hash     TEXT,
            storage_key      TEXT,
            size_bytes       INTEGER,
            page_count       INTEGER,
            parser           TEXT,
            extraction_data  TEXT
        )
        """,
    ),
    (
        "documents.idx_deal",
        "CREATE INDEX IF NOT EXISTS idx_documents_deal ON documents (deal_id, uploaded_at DESC)",
    ),
    (
        "documents.idx_tenant",
        "CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents (tenant_id)",
    ),
    (
        "extraction_results.create_table",
        """
        CREATE TABLE IF NOT EXISTS extraction_results (
            id                 TEXT PRIMARY KEY,
            document_id        TEXT NOT NULL,
            deal_id            TEXT NOT NULL,
            tenant_id          TEXT NOT NULL,
            fields             TEXT,
            confidence_report  TEXT,
            agent_version      TEXT,
            created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "extraction_results.idx_document",
        """
        CREATE INDEX IF NOT EXISTS idx_extraction_results_document
        ON extraction_results (document_id, created_at DESC)
        """,
    ),
    (
        "model_calls.create_table",
        """
        CREATE TABLE IF NOT EXISTS model_calls (
            id                       TEXT PRIMARY KEY,
            deal_id                  TEXT NOT NULL,
            tenant_id                TEXT,
            agent_name               TEXT NOT NULL,
            model                    TEXT NOT NULL,
            input_tokens             INTEGER NOT NULL DEFAULT 0,
            output_tokens            INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens        INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens    INTEGER NOT NULL DEFAULT 0,
            cost_usd                 REAL NOT NULL DEFAULT 0,
            latency_ms               INTEGER,
            trace_id                 TEXT,
            status                   TEXT NOT NULL DEFAULT 'ok',
            created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "model_calls.idx_deal",
        "CREATE INDEX IF NOT EXISTS idx_model_calls_deal ON model_calls (deal_id, created_at DESC)",
    ),
    (
        "audit_log.create_table",
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id             TEXT PRIMARY KEY,
            tenant_id      TEXT NOT NULL,
            deal_id        TEXT,
            actor_id       TEXT,
            action         TEXT NOT NULL,
            resource_type  TEXT NOT NULL,
            resource_id    TEXT,
            payload        TEXT,
            created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        "CREATE INDEX IF NOT EXISTS idx_audit_log_deal ON audit_log (deal_id)",
    ),
    # SQLite duplicate-column errors are swallowed by the migration
    # runner — that's how we get an "ADD COLUMN IF NOT EXISTS" effect
    # on a backend that doesn't support the IF NOT EXISTS guard.
    (
        "audit_log.add_input_hash_sqlite",
        "ALTER TABLE audit_log ADD COLUMN input_hash TEXT",
    ),
    (
        "audit_log.add_output_hash_sqlite",
        "ALTER TABLE audit_log ADD COLUMN output_hash TEXT",
    ),
    (
        "memo_edits.create_table",
        """
        CREATE TABLE IF NOT EXISTS memo_edits (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            deal_id         TEXT NOT NULL,
            section_id      TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            original_body   TEXT NOT NULL,
            new_body        TEXT NOT NULL,
            comment         TEXT,
            created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "memo_edits.idx_deal",
        "CREATE INDEX IF NOT EXISTS idx_memo_edits_deal ON memo_edits (deal_id, created_at DESC)",
    ),
    (
        "memo_edits.idx_deal_section",
        """
        CREATE INDEX IF NOT EXISTS idx_memo_edits_deal_section
        ON memo_edits (deal_id, section_id, created_at DESC)
        """,
    ),
    (
        "verification_reports.create_table",
        """
        CREATE TABLE IF NOT EXISTS verification_reports (
            id            TEXT PRIMARY KEY,
            deal_id       TEXT NOT NULL,
            tenant_id     TEXT NOT NULL,
            pass_rate     REAL NOT NULL DEFAULT 0,
            report_json   TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "verification_reports.idx_deal_created",
        """
        CREATE INDEX IF NOT EXISTS idx_verification_reports_deal_created
        ON verification_reports (deal_id, created_at DESC)
        """,
    ),
    (
        "critic_findings.create_table",
        """
        CREATE TABLE IF NOT EXISTS critic_findings (
            id                    TEXT PRIMARY KEY,
            deal_id               TEXT NOT NULL,
            tenant_id             TEXT NOT NULL,
            rule_id               TEXT NOT NULL,
            title                 TEXT NOT NULL,
            narrative             TEXT NOT NULL,
            severity              TEXT NOT NULL,
            cited_fields          TEXT,
            cited_pages           TEXT,
            impact_estimate_usd   REAL,
            created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "critic_findings.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_critic_findings_deal
        ON critic_findings (deal_id, created_at DESC)
        """,
    ),
    (
        "critic_reports.create_table",
        """
        CREATE TABLE IF NOT EXISTS critic_reports (
            id            TEXT PRIMARY KEY,
            deal_id       TEXT NOT NULL,
            tenant_id     TEXT NOT NULL,
            summary       TEXT,
            report_json   TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "critic_reports.idx_deal",
        """
        CREATE INDEX IF NOT EXISTS idx_critic_reports_deal
        ON critic_reports (deal_id, created_at DESC)
        """,
    ),
]


async def run_startup_migrations() -> None:
    """Apply every entry in ``MIGRATIONS`` against the live DB.

    On SQLite we apply the lighter ``SQLITE_MIGRATIONS`` set so the
    documents + extractions flow works for local dev / unit tests.
    LangGraph's checkpointer manages its own schema (Postgres only).
    """
    settings = get_settings()
    is_sqlite = settings.async_database_url.startswith("sqlite")
    entries = SQLITE_MIGRATIONS if is_sqlite else MIGRATIONS

    if is_sqlite:
        logger.info(
            "migrations: sqlite detected — applying lite schema "
            "(deals/documents/extraction_results)"
        )

    engine = get_engine()
    async with engine.begin() as conn:
        for name, sql in entries:
            try:
                await conn.execute(text(sql))
                logger.info("migration applied: %s", name)
            except Exception as exc:
                logger.exception("migration failed: %s — %s", name, exc)


__all__ = ["MIGRATIONS", "SQLITE_MIGRATIONS", "run_startup_migrations"]
