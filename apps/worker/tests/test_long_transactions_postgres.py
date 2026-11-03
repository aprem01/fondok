"""Incident 2026-11-03 against a REAL Postgres — no lock held across a long call,
and a boot migration that meets a held lock gives up fast instead of queueing
the table.

Opt-in: set ``FONDOK_TEST_POSTGRES_URL`` to the DSN of a THROWAWAY database
(``postgresql://user:pw@host:port/db``, a role that may run the startup DDL;
the ``pgvector/pgvector:pg16`` image provides the ``vector`` extension).
Skipped cleanly when unset — the same switch as ``test_rls_postgres.py``.

What it proves:

1. while the extraction pipeline's extractor runs, the database has NO
   ``idle in transaction`` backend and an ``ALTER TABLE documents`` from
   another connection (``lock_timeout`` 2 s) goes straight through — the
   exact statement that queued behind the pipeline in production;
2. the same while the context store embeds a parsed document's chunks and
   while the embedding repair job embeds pending chunks;
3. with another session holding ACCESS SHARE on ``documents`` (what the
   pipeline used to do), a startup migration on ``documents`` — even a
   ``FATAL_MIGRATIONS`` entry — gives up after ``MIGRATION_LOCK_TIMEOUT_MS``
   (SQLSTATE 55P03), is deferred, the boot completes, a reader queued behind
   the waiting DDL is released when it gives up, and the next boot (lock
   gone) applies it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

PG_URL = os.environ.get("FONDOK_TEST_POSTGRES_URL", "").strip()

pytestmark = [
    pytest.mark.skipif(
        not PG_URL,
        reason="FONDOK_TEST_POSTGRES_URL not set — Postgres long-transaction test skipped",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]

TENANT = "cccccccc-0000-4000-8000-00000000000c"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _connect():
    import asyncpg

    return await asyncpg.connect(_plain(PG_URL))


async def _idle_in_transaction(conn: Any) -> list[str]:
    rows = await conn.fetch(
        "SELECT left(query, 120) AS q FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
        "AND state LIKE 'idle in transaction%'"
    )
    return [r["q"] for r in rows]


async def _concurrent_ddl_goes_through(conn: Any) -> float:
    """The statement that queued in production, bounded by a 2 s lock wait.
    Returns its wall time; raises LockNotAvailableError when a lock is held."""
    t0 = time.monotonic()
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '2s'")
        await conn.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS txn_probe INT")
        await conn.execute("ALTER TABLE documents DROP COLUMN IF EXISTS txn_probe")
    return time.monotonic() - t0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_db():
    import app.database as database
    from app.config import get_settings
    from app.migrations import run_startup_migrations

    mp = pytest.MonkeyPatch()
    saved = (database._engine, database._session_factory, database._migrations_engine)
    database._engine = database._session_factory = database._migrations_engine = None
    mp.setenv("DATABASE_URL", _plain(PG_URL))
    mp.delenv("DATABASE_URL_MIGRATIONS", raising=False)
    mp.setenv("DIGEST_SCHEDULER_ENABLED", "False")
    mp.setenv("EXTRACTION_WATCHDOG_ENABLED", "False")
    get_settings.cache_clear()
    try:
        await run_startup_migrations()
        conn = await _connect()
        try:
            deal_id = str(uuid4())
            await conn.execute(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES ($1, $2, 'PG Txn Hotel', 'Draft', now(), now())",
                deal_id,
                TENANT,
            )
        finally:
            await conn.close()
        yield deal_id
    finally:
        await database.dispose_engine()
        database._engine, database._session_factory, database._migrations_engine = saved
        mp.undo()
        get_settings.cache_clear()


async def _seed_doc(deal_id: str) -> str:
    doc_id = str(uuid4())
    conn = await _connect()
    try:
        await conn.execute(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
            "uploaded_at, status_changed_at, content_hash, storage_key, parser, extraction_data) "
            "VALUES ($1, $2, $3, 'x.xlsx', 'T12', 'UPLOADED', now(), now(), $4, $5, "
            "'test-fixture', $6::jsonb)",
            doc_id,
            deal_id,
            TENANT,
            uuid4().hex * 2,
            f"raw/{doc_id}",
            json.dumps(
                {
                    "parser": "test-fixture",
                    "total_pages": 1,
                    "pages": [{"page_num": 1, "text": "Rooms revenue 1,000", "tables": [], "metadata": {}}],
                    "error_kind": "timeout",
                    "error_message": "Timed out after 31 min in Extracting",
                }
            ),
        )
    finally:
        await conn.close()
    return doc_id


async def test_extractor_runs_with_no_lock_on_documents(pg_db) -> None:
    from app.api import documents as docs_module

    deal_id = pg_db
    doc = await _seed_doc(deal_id)
    probe: dict[str, Any] = {}

    async def _extract(request: Any):
        conn = await _connect()
        try:
            probe["idle_in_txn"] = await _idle_in_transaction(conn)
            probe["ddl_seconds"] = await _concurrent_ddl_goes_through(conn)
        finally:
            await conn.close()
        return (
            [{"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 1000.0,
              "unit": "USD", "source_page": 1, "confidence": 0.9, "raw_text": "Rooms revenue 1,000"}],
            {"overall": 0.9},
            "txn-probe",
            None,
        )

    previous = docs_module.set_extraction_backend(
        docs_module.ExtractionBackend(name="txn-probe", extract=_extract, critic_narrative_pass=False)
    )
    try:
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=TENANT, force=True
        )
    finally:
        docs_module.set_extraction_backend(previous)

    assert probe["idle_in_txn"] == [], probe
    assert probe["ddl_seconds"] < 2.0
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            "SELECT status, extraction_data FROM documents WHERE id = $1", doc
        )
    finally:
        await conn.close()
    assert row["status"] == "EXTRACTED"
    data = json.loads(row["extraction_data"]) if isinstance(row["extraction_data"], str) else row["extraction_data"]
    assert "error_kind" not in data and "error_message" not in data
    assert data["pages"]


async def test_embedding_calls_run_with_no_transaction_open(pg_db, monkeypatch) -> None:
    from app.database import get_session_factory
    from app.extraction import embeddings
    from app.extraction.context_store import index_parsed_document
    from app.extraction.models import ParsedDocument, ParsedPage
    from app.services.embedding_repair import embed_pending_chunks

    deal_id = pg_db
    doc = await _seed_doc(deal_id)
    seen: list[list[str]] = []

    async def _check() -> None:
        conn = await _connect()
        try:
            seen.append(await _idle_in_transaction(conn))
        finally:
            await conn.close()

    async def _iter_partial(texts, *, input_type):
        await _check()
        return [None] * len(texts)  # every batch failed → chunks stay NULL

    async def _batch(texts, *, input_type):
        await _check()
        return [[0.0] * embeddings.EMBEDDING_DIM for _ in texts]

    monkeypatch.setattr(embeddings, "is_enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed_iter_partial", _iter_partial)
    monkeypatch.setattr(embeddings, "embed_batch", _batch)

    from datetime import UTC, datetime

    parsed = ParsedDocument(
        filename="x.xlsx",
        content_hash=uuid4().hex * 2,
        parsed_at=datetime.now(UTC),
        parser="test-fixture",
        total_pages=1,
        pages=[ParsedPage(page_num=1, text="Rooms revenue 1,000. " * 40)],
    )
    from app.auth.context import current_tenant_id

    current_tenant_id.set(TENANT)
    factory = get_session_factory()
    async with factory() as s:
        n = await index_parsed_document(
            s, deal_id=deal_id, tenant_id=TENANT, document_id=doc, parsed=parsed
        )
    assert n >= 1
    async with factory() as s:
        r = await embed_pending_chunks(s, tenant_id=TENANT, deal_id=deal_id, max_chunks=100)
    assert r.embedded >= 1
    assert seen and all(x == [] for x in seen), seen


async def test_boot_migration_meets_a_held_lock_and_defers(pg_db, monkeypatch, caplog) -> None:
    import asyncpg

    from app import migrations as mig

    holder = await _connect()
    reader = await _connect()
    try:
        # What the pipeline used to do: a read on ``documents``, transaction
        # left open — ACCESS SHARE held until it ends.
        tr = holder.transaction()
        await tr.start()
        await holder.fetchval("SELECT count(*) FROM documents")

        probe = ("documents.lock_probe", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS lock_probe INT")
        monkeypatch.setattr(mig, "MIGRATIONS", [probe, ("deals.after_probe", "SELECT 1")])
        monkeypatch.setattr(mig, "FATAL_MIGRATIONS", frozenset({"documents.lock_probe"}))

        async def _noop(engine, *, is_sqlite):
            return None

        monkeypatch.setattr(mig, "backfill_deal_ai_confidence", _noop)
        monkeypatch.setattr(mig, "repair_display_string_edits", _noop)

        async def _late_reader() -> float:
            # Starts while the DDL is waiting: Postgres queues it behind the
            # DDL's ACCESS EXCLUSIVE request — until the DDL gives up.
            await asyncio.sleep(1.0)
            t0 = time.monotonic()
            await reader.fetchval("SELECT count(*) FROM documents")
            return time.monotonic() - t0

        t0 = time.monotonic()
        caplog.set_level("ERROR", logger="app.migrations")
        _, reader_wait = await asyncio.gather(mig.run_startup_migrations(), _late_reader())
        boot_seconds = time.monotonic() - t0

        limit = mig.MIGRATION_LOCK_TIMEOUT_MS / 1000
        assert limit - 0.5 < boot_seconds < limit + 3, boot_seconds  # gave up at lock_timeout
        assert reader_wait < limit, reader_wait  # released when the DDL gave up
        assert any("lock wait timed out" in r.getMessage() and "documents.lock_probe" in r.getMessage()
                   for r in caplog.records)
        has_col = await reader.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'lock_probe'"
        )
        assert has_col == 0
        # While the holder still holds its lock, other readers are not blocked.
        t1 = time.monotonic()
        await reader.fetchval("SELECT count(*) FROM documents")
        assert time.monotonic() - t1 < 0.5
        await tr.rollback()

        # Next boot, lock gone: the deferred entry applies.
        await mig.run_startup_migrations()
        has_col = await reader.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'lock_probe'"
        )
        assert has_col == 1
        await reader.execute("ALTER TABLE documents DROP COLUMN lock_probe")
    finally:
        for c in (holder, reader):
            with contextlib.suppress(asyncpg.InterfaceError):
                await c.close()


async def test_lock_timeout_does_not_leak_onto_the_pooled_connection(pg_db) -> None:
    """``SET LOCAL``: the 5 s lock_timeout is the migration transaction's
    only — the serving pool's connections keep the server default."""
    from sqlalchemy import text

    from app.database import get_engine
    from app.migrations import _migration_transaction

    engine = get_engine()
    async with _migration_transaction(engine, is_sqlite=False) as conn:
        assert (await conn.execute(text("SHOW lock_timeout"))).scalar_one() == "5s"
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW lock_timeout"))).scalar_one() == "0"


async def test_critic_rerun_on_postgres_replaces_findings(pg_db, monkeypatch) -> None:
    """Deal 76d10a64's ``critic_findings_pkey`` violation, on Postgres: the
    second run's save succeeds and the findings are its set."""
    from uuid import UUID

    from fondok_schemas.critic import CriticReport, Severity

    from app.agents import critic
    from app.agents.critic import CriticOutput, _make_finding
    from app.api import documents as docs_module
    from app.auth.context import current_tenant_id
    from app.database import get_session_factory

    deal_id = pg_db

    def _out(titles: list[str]) -> CriticOutput:
        fs = [
            _make_finding(deal_uuid=UUID(deal_id), rule_id="MULTI_FIELD_FNB_MARGIN_AGGRESSIVE",
                          title=t, narrative=t, severity=Severity.INFO, cited_fields=["a"])
            for t in titles
        ]
        return CriticOutput(deal_id=deal_id, report=CriticReport(deal_id=UUID(deal_id), findings=fs))

    outs = iter([_out(["one", "two"]), _out(["one"])])

    async def _inputs(session, *, deal_id: str, tenant_id: str):
        return {"noi": 1.0}, {"noi": 2.0}, {}, 100

    async def _run(payload, *, run_narrative_pass: bool = True):
        return next(outs)

    monkeypatch.setattr(docs_module, "_load_critic_inputs", _inputs)
    monkeypatch.setattr(critic, "run_critic", _run)
    monkeypatch.setattr(critic, "CriticInput", lambda **kw: kw)
    current_tenant_id.set(TENANT)
    conn = await _connect()
    try:
        reports_before = await conn.fetchval(
            "SELECT count(*) FROM critic_reports WHERE deal_id = $1", deal_id)
    finally:
        await conn.close()
    for _ in range(2):
        async with get_session_factory()() as s:
            await docs_module._persist_critic_report(s, deal_id=deal_id, tenant_id=TENANT)

    conn = await _connect()
    try:
        titles = [r["title"] for r in await conn.fetch(
            "SELECT title FROM critic_findings WHERE deal_id = $1", deal_id)]
        reports = await conn.fetchval("SELECT count(*) FROM critic_reports WHERE deal_id = $1", deal_id)
    finally:
        await conn.close()
    assert titles == ["one"]
    assert reports == reports_before + 2


async def test_a_redeploy_takes_no_table_lock_for_columns_that_exist(pg_db, caplog) -> None:
    """The incident's actual wait: the boot's ``ALTER TABLE documents ADD
    COLUMN IF NOT EXISTS …`` for a column production already had — Postgres
    takes ACCESS EXCLUSIVE before it notices. Against a migrated database
    with a lock held on ``documents``, the FULL real migration set now runs
    through without waiting and defers nothing."""
    from app import migrations as mig

    holder = await _connect()
    try:
        tr = holder.transaction()
        await tr.start()
        await holder.fetchval("SELECT count(*) FROM documents")
        caplog.set_level("ERROR", logger="app.migrations")
        t0 = time.monotonic()
        await mig.run_startup_migrations()
        elapsed = time.monotonic() - t0
        await tr.rollback()
    finally:
        await holder.close()
    deferred = [r.getMessage() for r in caplog.records if "not applied" in r.getMessage()]
    assert deferred == [], deferred
    assert elapsed < mig.MIGRATION_LOCK_TIMEOUT_MS / 1000, elapsed
