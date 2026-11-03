"""Incident 2026-11-03 — no database transaction is held across a long external call.

The extraction pipeline committed its EXTRACTING stamp, then read the
document row on the SAME session — which autobegan a transaction holding
an ACCESS SHARE lock on ``documents`` — and kept it open (``idle in
transaction``) through the 15-60 minute LLM extraction. A deploy's boot
DDL on ``documents`` queued behind that lock, every later query on
``documents`` queued behind the DDL, and the worker's pool ran dry.

The rule these tests pin: while an extractor / LLM / embedding / webhook /
object-store call is in flight, the worker holds NO pooled connection —
an AsyncSession only checks a connection out while it has a transaction
open, so ``engine.pool.checkedout() == 0`` is the strict form of
"no session is in a transaction". (The Postgres-level twin — no
``idle in transaction`` backend, a concurrent ``ALTER TABLE documents``
goes straight through — is ``test_long_transactions_postgres.py``.)

Also here: a successful (re-)extraction clears the failure fields a
watchdog timeout or an earlier failed attempt left on the row, and a
re-run of the Critic replaces the deal's findings instead of dying on
``critic_findings_pkey``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-no-long-txn.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ["EXTRACTION_WATCHDOG_ENABLED"] = "False"
os.environ["DIGEST_SCHEDULER_ENABLED"] = "False"

from sqlalchemy import text  # noqa: E402

from app.api import documents as docs_module  # noqa: E402


def _held_connections() -> int:
    """Pooled connections checked out right now (sessions with an open
    transaction, or connections someone forgot to return)."""
    from app.database import get_engine

    return get_engine().pool.checkedout()


@pytest.fixture
async def db_env():
    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = str(get_settings().DEFAULT_TENANT_ID)
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Long Txn Hotel', 'Draft', :ts, :ts)"
            ),
            {"id": deal_id, "tenant": tenant_id, "ts": "2026-11-03 00:00:00"},
        )
        await session.commit()
    assert _held_connections() == 0
    yield factory, tenant_id, deal_id


async def _seed_doc(
    factory: Any,
    *,
    tenant_id: str,
    deal_id: str,
    status: str = "UPLOADED",
    extra_data: dict[str, Any] | None = None,
) -> str:
    doc_id = str(uuid4())
    payload: dict[str, Any] = {
        "parser": "test-fixture",
        "total_pages": 1,
        "pages": [{"page_num": 1, "text": "Rooms revenue 1,000", "tables": [], "metadata": {}}],
        **(extra_data or {}),
    }
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                "status, uploaded_at, status_changed_at, content_hash, storage_key, "
                "parser, extraction_data) VALUES (:id, :deal, :tenant, :fn, 'T12', "
                ":status, :ts, :ts, :ch, :sk, 'test-fixture', :data)"
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": f"{doc_id}.xlsx",
                "status": status,
                "ts": "2026-11-03T19:00:00+00:00",
                "ch": uuid4().hex * 2,
                "sk": f"raw/{doc_id}",
                "data": json.dumps(payload),
            },
        )
        await session.commit()
    return doc_id


async def _doc_row(factory: Any, doc_id: str, tenant_id: str) -> dict[str, Any]:
    async with factory() as session:
        r = (
            await session.execute(
                text(
                    "SELECT status, extraction_data FROM documents "
                    "WHERE id = :id AND tenant_id = :t"
                ),
                {"id": doc_id, "t": tenant_id},
            )
        ).first()
    assert r is not None
    m = dict(r._mapping)
    raw = m["extraction_data"]
    m["extraction_data"] = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    return m


async def _audit_rows(factory: Any, *, tenant_id: str, doc_id: str) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, before, after FROM audit_log WHERE tenant_id = :t "
                    "AND resource_id = :rid ORDER BY created_at"
                ),
                {"t": tenant_id, "rid": doc_id},
            )
        ).fetchall()
    out = []
    for r in rows:
        m = dict(r._mapping)
        for k in ("before", "after"):
            if isinstance(m[k], str) and m[k]:
                m[k] = json.loads(m[k])
        out.append(m)
    return out


_FIELDS = [
    {
        "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
        "value": 1000.0,
        "unit": "USD",
        "source_page": 1,
        "confidence": 0.9,
        "raw_text": "Rooms revenue 1,000",
    }
]


def _backend(extract: Any) -> Any:
    return docs_module.ExtractionBackend(
        name="txn-probe", extract=extract, critic_narrative_pass=False
    )


# ───────────────────── the extraction pipeline ─────────────────────


async def test_pipeline_holds_no_connection_while_the_extractor_runs(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(factory, tenant_id=tenant_id, deal_id=deal_id)
    seen: list[int] = []

    async def _extract(request: Any):
        seen.append(_held_connections())
        return list(_FIELDS), {"overall": 0.9}, "txn-probe", None

    previous = docs_module.set_extraction_backend(_backend(_extract))
    try:
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=tenant_id, force=True
        )
    finally:
        docs_module.set_extraction_backend(previous)

    assert seen == [0], f"pooled connections held during the extractor call: {seen}"
    assert (await _doc_row(factory, doc, tenant_id))["status"] == "EXTRACTED"
    assert _held_connections() == 0


async def test_extraction_request_carries_no_session(db_env) -> None:
    """A backend gets a session FACTORY for short independent transactions,
    never the pipeline's own session."""
    names = {f.name for f in docs_module.ExtractionRequest.__dataclass_fields__.values()}
    assert "session" not in names
    assert "session_factory" in names


@pytest.mark.real_extraction_backend
async def test_llm_backend_holds_no_connection_across_the_graph(db_env, monkeypatch) -> None:
    """The production backend's sibling-reuse lookup and template learning
    each run in their own short transaction — the Router → Extractor graph
    (the 15-60 min call) runs with nothing checked out."""
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(factory, tenant_id=tenant_id, deal_id=deal_id)
    seen: list[int] = []
    learned: list[int] = []

    async def _graph(**kwargs):
        seen.append(_held_connections())
        return list(_FIELDS), {"overall": 0.9}, "router:T12;extractor", "T12"

    from app.services import sibling_template

    real_learn = sibling_template.maybe_learn_mapping

    async def _learn(session, **kwargs):
        await real_learn(session, **kwargs)
        learned.append(1)

    monkeypatch.setattr(docs_module, "_run_graph_extraction", _graph)
    monkeypatch.setattr(sibling_template, "maybe_learn_mapping", _learn)
    await docs_module._run_extraction_pipeline(
        deal_id=deal_id, doc_id=doc, tenant_id=tenant_id
    )

    assert seen == [0], f"pooled connections held during the LLM graph: {seen}"
    assert learned == [1]
    assert (await _doc_row(factory, doc, tenant_id))["status"] == "EXTRACTED"
    assert _held_connections() == 0


async def test_pipeline_holds_no_connection_during_the_raw_store_fetch(db_env, monkeypatch) -> None:
    """The P&L quality passes fetch the original workbook from the raw store
    (S3 in production) — no transaction may be open across that either."""
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory,
        tenant_id=tenant_id,
        deal_id=deal_id,
        extra_data={
            "pages": [
                {
                    "page_num": 1,
                    "text": "Summary\nRooms Revenue 1,000",
                    "tables": [],
                    "metadata": {"sheet_name": "Summary"},
                }
            ]
        },
    )
    seen: list[int] = []

    class _Store:
        async def get(self, key: str) -> bytes:
            seen.append(_held_connections())
            raise FileNotFoundError(key)

    from app.extraction import usali_summary_reconcile

    monkeypatch.setattr(docs_module, "get_raw_store", lambda _s: _Store())
    monkeypatch.setattr(usali_summary_reconcile, "find_summary_page", lambda _p: 0)

    async def _extract(request: Any):
        return list(_FIELDS), {"overall": 0.9}, "txn-probe", "T12"

    previous = docs_module.set_extraction_backend(_backend(_extract))
    try:
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=tenant_id, force=True
        )
    finally:
        docs_module.set_extraction_backend(previous)

    assert seen and set(seen) == {0}, f"connections held during the raw-store fetch: {seen}"


# ───────────── a completed extraction leaves a consistent row ─────────────


async def test_completion_after_a_watchdog_timeout_clears_the_failure_and_audits(db_env) -> None:
    """Doc 21eece07: the watchdog failed the row at 30 min, the job finished
    at 60 min and set EXTRACTED — but ``error_kind='timeout'`` stayed on the
    row. Completion wins: the failure fields go, an audit row says so."""
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(factory, tenant_id=tenant_id, deal_id=deal_id)

    async def _extract(request: Any):
        # The watchdog fires while the extractor is still running.
        updated = await docs_module._mark_document_failed(
            doc_id=request.doc_id,
            tenant_id=request.tenant_id,
            error_kind="timeout",
            error_message="Timed out after 31 min in Extracting — ...",
            error_raw="watchdog: EXTRACTING for 31.0 min (threshold 30 min)",
            expected_statuses={"EXTRACTING"},
        )
        assert updated
        return list(_FIELDS), {"overall": 0.9}, "txn-probe", None

    previous = docs_module.set_extraction_backend(_backend(_extract))
    try:
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=tenant_id, force=True, job_id="job-late"
        )
    finally:
        docs_module.set_extraction_backend(previous)

    row = await _doc_row(factory, doc, tenant_id)
    assert row["status"] == "EXTRACTED"
    ed = row["extraction_data"]
    for key in docs_module.EXTRACTION_FAILURE_KEYS:
        assert key not in ed, f"stale failure field {key!r} left on a completed row"
    assert ed["pages"], "the parse payload survives the clean-up"

    audits = [a for a in await _audit_rows(factory, tenant_id=tenant_id, doc_id=doc)
              if a["action"] == docs_module.AUDIT_COMPLETED_AFTER_FAILURE]
    assert len(audits) == 1
    assert audits[0]["before"]["status"] == "FAILED"
    assert audits[0]["before"]["error_kind"] == "timeout"
    assert audits[0]["after"]["status"] == "EXTRACTED"


async def test_successful_retry_clears_an_earlier_attempts_failure_fields(db_env) -> None:
    """A retry through ``POST .../extract`` keeps ``extraction_data`` (no
    re-parse), so the previous attempt's error used to survive a success."""
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory,
        tenant_id=tenant_id,
        deal_id=deal_id,
        status="FAILED",
        extra_data={
            "error_kind": "other",
            "error_message": "boom",
            "error_raw": "RuntimeError: boom",
            "failed_from_status": "EXTRACTING",
            "failed_job_id": "job-old",
        },
    )

    async def _extract(request: Any):
        return list(_FIELDS), {"overall": 0.9}, "txn-probe", None

    previous = docs_module.set_extraction_backend(_backend(_extract))
    try:
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=tenant_id, force=True
        )
    finally:
        docs_module.set_extraction_backend(previous)

    row = await _doc_row(factory, doc, tenant_id)
    assert row["status"] == "EXTRACTED"
    for key in docs_module.EXTRACTION_FAILURE_KEYS:
        assert key not in row["extraction_data"]
    # The pipeline itself moved the row through EXTRACTING; nobody failed
    # it under the running job, so there is nothing to audit.
    actions = [a["action"] for a in await _audit_rows(factory, tenant_id=tenant_id, doc_id=doc)]
    assert docs_module.AUDIT_COMPLETED_AFTER_FAILURE not in actions


# ─────────────────────────────── critic ───────────────────────────────


def _critic_output(deal_id: str, titles: list[str]) -> Any:
    from fondok_schemas.critic import CriticReport, Severity

    from app.agents.critic import CriticOutput, _make_finding

    findings = [
        _make_finding(
            deal_uuid=UUID(deal_id),
            rule_id="MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE",
            title=t,
            narrative=f"{t} narrative",
            severity=Severity.WARN,
        )
        for t in titles
    ]
    return CriticOutput(
        deal_id=deal_id,
        report=CriticReport(
            deal_id=UUID(deal_id), findings=findings, summary="s", warn_count=len(findings)
        ),
    )


async def test_critic_rerun_replaces_findings_instead_of_duplicate_key(db_env, monkeypatch) -> None:
    """Deal 76d10a64: the re-extract's critic save failed on
    ``critic_findings_pkey`` — finding ids are stable per (deal, rule,
    title), so every run after the first collided and the whole report
    (``critic_reports`` row included) rolled back."""
    factory, tenant_id, deal_id = db_env
    from app.agents import critic

    outputs = iter(
        [
            _critic_output(deal_id, ["NOI up, OpEx flat", "Second finding"]),
            _critic_output(deal_id, ["NOI up, OpEx flat"]),
        ]
    )
    held: list[int] = []

    async def _inputs(session, *, deal_id: str, tenant_id: str):
        await session.execute(text("SELECT 1 FROM documents WHERE tenant_id = :t"), {"t": tenant_id})
        return {"noi": 1.0}, {"noi": 2.0}, {}, 100

    async def _run_critic(payload, *, run_narrative_pass: bool = True):
        held.append(_held_connections())
        return next(outputs)

    monkeypatch.setattr(docs_module, "_load_critic_inputs", _inputs)
    monkeypatch.setattr(critic, "run_critic", _run_critic)
    # The inputs above are placeholders; the critic agent itself is stubbed.
    monkeypatch.setattr(critic, "CriticInput", lambda **kw: kw)

    for _ in range(2):
        async with factory() as session:
            await docs_module._persist_critic_report(
                session, deal_id=deal_id, tenant_id=tenant_id
            )

    assert held == [0, 0], f"connections held during the critic's LLM pass: {held}"
    async with factory() as session:
        reports = (
            await session.execute(
                text("SELECT count(*) FROM critic_reports WHERE deal_id = :d AND tenant_id = :t"),
                {"d": deal_id, "t": tenant_id},
            )
        ).scalar_one()
        titles = [
            r[0]
            for r in (
                await session.execute(
                    text("SELECT title FROM critic_findings WHERE deal_id = :d AND tenant_id = :t"),
                    {"d": deal_id, "t": tenant_id},
                )
            ).fetchall()
        ]
    assert reports == 2, "both runs' reports are saved"
    assert titles == ["NOI up, OpEx flat"], "the findings are the latest run's set"


# ─────────────────────────────── digest ───────────────────────────────


async def test_digest_dispatch_holds_no_connection(db_env, monkeypatch) -> None:
    """The Slack / email webhooks are external calls (5-8 s timeouts each)."""
    _factory, tenant_id, _deal_id = db_env
    from app.services import digest_scheduler

    held: list[int] = []

    async def _due(session, *, now, tenant_id=None):
        await session.execute(text("SELECT 1 FROM deals WHERE tenant_id = :t"), {"t": tid})
        return [{"id": str(uuid4()), "tenant_id": tid, "cadence": "daily", "hour_utc": 13}]

    async def _payload(session, *, tenant_id, schedule, now):
        await session.execute(text("SELECT 1 FROM deals WHERE tenant_id = :t"), {"t": tenant_id})
        return {"rows": []}

    def _dispatch(schedule, payload):
        held.append(_held_connections())

    async def _mark(session, **kwargs):
        return None

    tid = tenant_id
    monkeypatch.setattr(digest_scheduler, "_fetch_due_schedules", _due)
    monkeypatch.setattr(digest_scheduler, "build_digest_payload", _payload)
    monkeypatch.setattr(digest_scheduler, "dispatch_digest", _dispatch)
    monkeypatch.setattr(digest_scheduler, "_mark_dispatched", _mark)

    assert await digest_scheduler.tick_once() == 1
    assert held == [0], f"connections held during the digest webhook: {held}"


# ───────────────────────── boot migrations ─────────────────────────


def _pg_error(sqlstate: str) -> Exception:
    """A SQLAlchemy DBAPIError wrapping the asyncpg error Postgres raises."""
    import asyncpg.exceptions as pg_exc
    from sqlalchemy.exc import DBAPIError

    cls = pg_exc.PostgresError.get_message_class_for_sqlstate(sqlstate)
    orig = cls("canceling statement due to lock timeout")
    return DBAPIError("ALTER TABLE documents ...", {}, orig)


def test_a_lock_timeout_is_deferred_not_fatal() -> None:
    from app import migrations as mig

    assert mig._migration_deferred_reason(_pg_error("55P03")) == "lock wait timed out"
    assert mig._migration_deferred_reason(_pg_error("42501")) is None  # permission: the SQL's fault
    assert mig.MIGRATION_LOCK_TIMEOUT_MS == 5000


async def test_a_fatal_entry_that_times_out_on_a_lock_does_not_stop_the_boot(monkeypatch) -> None:
    """A FATAL_MIGRATIONS entry refused by the database stops the boot; the
    same entry giving up on a lock wait is deferred (busy table, not bad SQL)."""
    from app import migrations as mig

    calls: list[str] = []

    class _Conn:
        async def execute(self, stmt, *a, **k):
            sql = str(stmt)
            calls.append(sql)
            if "FAIL_WITH" in sql:
                raise _pg_error(sql.split("FAIL_WITH ")[1].strip())

    class _Begin:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

    async def _noop(engine, *, is_sqlite):
        return None

    monkeypatch.setattr(mig, "get_migrations_engine", lambda: _Engine())
    monkeypatch.setattr(mig, "backfill_deal_ai_confidence", _noop)
    monkeypatch.setattr(mig, "repair_display_string_edits", _noop)
    monkeypatch.setattr(mig, "FATAL_MIGRATIONS", frozenset({"t.fatal"}))

    monkeypatch.setattr(mig, "SQLITE_MIGRATIONS", [("t.fatal", "SELECT 1 -- FAIL_WITH 55P03"), ("t.next", "SELECT 2")])
    await mig.run_startup_migrations()  # deferred, boot continues
    assert any("SELECT 2" in c for c in calls), "the entries after a deferred one still run"

    monkeypatch.setattr(mig, "SQLITE_MIGRATIONS", [("t.fatal", "SELECT 1 -- FAIL_WITH 42501")])
    with pytest.raises(mig.FatalMigrationError):
        await mig.run_startup_migrations()
