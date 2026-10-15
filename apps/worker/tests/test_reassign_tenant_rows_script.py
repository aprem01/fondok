"""``scripts/reassign_default_tenant_rows.py`` — the tenant fold script.

Review finding (2026-10-14): ``documents`` has no ``created_at`` (only
``uploaded_at``), so with ``--created-after`` set the documents count
errored, the table was SKIPPED, and deals / extractions / audit rows moved
without their documents — the folded deals opened with an empty Data Room.

Pinned here:

* every tenant-scoped table has a timestamp column in the map, and that
  column really exists — checked against the SQLite schema the migrations
  build AND the Postgres DDL text (``document_chunks`` is Postgres-only);
* a windowed plan counts ``documents`` on ``uploaded_at`` and moves them;
* any table that cannot be counted makes the run refuse ``--commit`` and
  move nothing.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_WORKER_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _WORKER_ROOT / "scripts" / "reassign_default_tenant_rows.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("reassign_default_tenant_rows", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("reassign_default_tenant_rows", mod)
    spec.loader.exec_module(mod)
    return mod


script = _load_script()


# ─────────────────────────── the column map vs the migrations ───────────────────────────


def test_every_tenant_scoped_table_has_a_timestamp_column_in_the_map() -> None:
    assert set(script.TABLE_TIMESTAMP_COLUMNS) >= set(script.TENANT_SCOPED_TABLES)
    # The three tables with no ``created_at`` are mapped to their real column.
    assert script.timestamp_column("documents") == "uploaded_at"
    assert script.timestamp_column("engine_outputs") == "started_at"
    assert script.timestamp_column("pending_batches") == "submitted_at"
    with pytest.raises(KeyError):
        script.timestamp_column("not_a_table")


_CREATE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(?P<table>\w+)\s*\((?P<body>.*?)\n\s*\)", re.S | re.I)


def _ddl_columns(entries: list[tuple[str, str]]) -> dict[str, set[str]]:
    """``{table: {column, ...}}`` from CREATE TABLE statements (first word of each body line)."""
    out: dict[str, set[str]] = {}
    for _name, stmt in entries:
        m = _CREATE_RE.search(stmt)
        if not m:
            continue
        cols = set()
        for line in m.group("body").splitlines():
            word = line.strip().split(" ")[0].strip(",")
            if word and word.upper() not in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}:
                cols.add(word)
        out.setdefault(m.group("table"), set()).update(cols)
    return out


def test_column_map_matches_the_postgres_ddl() -> None:
    from app.migrations import MIGRATIONS

    cols = _ddl_columns(MIGRATIONS)
    for table in script.TENANT_SCOPED_TABLES:
        assert table in cols, f"{table}: no CREATE TABLE in MIGRATIONS"
        assert "tenant_id" in cols[table], f"{table}: no tenant_id column"
        col = script.timestamp_column(table)
        assert col in cols[table], f"{table}: {col!r} is not a column in the Postgres DDL ({sorted(cols[table])})"


@pytest.fixture
async def sqlite_engine():
    """A private SQLite DB built from the worker's own SQLite migrations —
    never the suite's shared engine."""
    from app.migrations import SQLITE_MIGRATIONS

    db = Path(tempfile.mkdtemp()) / "reassign.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    for _name, stmt in SQLITE_MIGRATIONS:
        # Same best-effort loop as conftest: some ADD COLUMN entries repeat a
        # column the CREATE TABLE already carries (run_startup_migrations
        # guards them with PRAGMA table_info).
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:  # noqa: BLE001
            pass
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_column_map_matches_the_sqlite_schema(sqlite_engine) -> None:
    """Every table the SQLite migrations build has the mapped column
    (``document_chunks`` is Postgres-only — pgvector — so it has no SQLite table)."""
    async with sqlite_engine.connect() as conn:
        for table in script.TENANT_SCOPED_TABLES:
            rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            names = {r[1] for r in rows}
            if not names:
                assert table == "document_chunks", f"{table}: missing from the SQLite schema"
                continue
            assert "tenant_id" in names, table
            col = script.timestamp_column(table)
            assert col in names, f"{table}: {col!r} not in SQLite columns {sorted(names)}"


# ─────────────────────────── the window bound per dialect ───────────────────────────


class _FakeEngine:
    def __init__(self, dialect: str) -> None:
        self.dialect = type("D", (), {"name": dialect})()


def test_window_bound_is_a_tz_aware_datetime_on_postgres_and_iso_text_on_sqlite() -> None:
    """asyncpg refuses a ``str`` for a TIMESTAMPTZ parameter (``DataError:
    expected a datetime.date or datetime.datetime instance, got 'str'``), so
    a windowed run against prod used to fail its count pass for every table
    and refuse to commit. Postgres gets a datetime; SQLite keeps the text."""
    pg = script._window_value(_FakeEngine("postgresql"), "2026-06-01")
    assert isinstance(pg, datetime) and pg.tzinfo is not None
    assert pg == datetime(2026, 6, 1, tzinfo=UTC)
    pg_dt = script._window_value(_FakeEngine("postgresql"), "2026-06-01T12:30:00+02:00")
    assert pg_dt.utcoffset() is not None and pg_dt.isoformat() == "2026-06-01T12:30:00+02:00"
    assert script._window_value(_FakeEngine("sqlite"), " 2026-06-01 ") == "2026-06-01"
    with pytest.raises(ValueError):
        script._window_value(_FakeEngine("postgresql"), "June 1st")


def test_window_sql_binds_through_the_dialect_helper() -> None:
    params: dict = {}
    where = script._window_sql(_FakeEngine("postgresql"), "documents", "2026-06-01", "2026-07-01", params)
    assert where == ["uploaded_at >= :since", "uploaded_at < :until"]
    assert params == {
        "since": datetime(2026, 6, 1, tzinfo=UTC),
        "until": datetime(2026, 7, 1, tzinfo=UTC),
    }
    assert script._window_sql(_FakeEngine("postgresql"), "deals", None, None, {}) == []


# ─────────────────────────── the plan and the commit ───────────────────────────

SOURCE = UUID("00000000-0000-0000-0000-000000000000")
ORG = "org_FoldTargetInstitution"


async def _seed(engine, *, tenant: UUID, when: datetime) -> tuple[str, str]:
    deal_id, doc_id = str(uuid4()), str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Fold Me', 'Draft', :when, :when)"
            ),
            {"id": deal_id, "tenant": str(tenant), "when": when.isoformat()},
        )
        await conn.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
                "VALUES (:id, :deal, :tenant, 'om.pdf', 'OM', 'EXTRACTED', :when)"
            ),
            {"id": doc_id, "deal": deal_id, "tenant": str(tenant), "when": when.isoformat()},
        )
    return deal_id, doc_id


async def _tenant_of(engine, table: str, row_id: str) -> str:
    async with engine.connect() as conn:
        return str((await conn.execute(text(f"SELECT tenant_id FROM {table} WHERE id = :id"), {"id": row_id})).scalar_one())


@pytest.mark.asyncio
async def test_windowed_plan_counts_documents_on_uploaded_at(sqlite_engine) -> None:
    target = script._coerce_tenant_id(ORG)
    assert target is not None
    now = datetime.now(UTC)
    await _seed(sqlite_engine, tenant=SOURCE, when=now - timedelta(days=1))  # inside the window
    await _seed(sqlite_engine, tenant=SOURCE, when=now - timedelta(days=400))  # before it

    since = (now - timedelta(days=30)).date().isoformat()
    plan, errors = await script.plan_counts(
        sqlite_engine, ["deals", "documents"], source=SOURCE, target=target, since=since, until=None
    )
    assert errors == []
    assert plan == [("deals", 1, 0), ("documents", 1, 0)]


@pytest.mark.asyncio
async def test_plan_reports_an_unmapped_or_missing_table_as_an_error(sqlite_engine) -> None:
    target = script._coerce_tenant_id(ORG)
    plan, errors = await script.plan_counts(
        sqlite_engine, ["deals", "no_such_table", "document_chunks"], source=SOURCE, target=target, since="2026-01-01", until=None
    )
    assert [t for t, *_ in plan] == ["deals"]
    assert {t for t, _ in errors} == {"no_such_table", "document_chunks"}
    # Unmapped-but-real table name, windowed → KeyError from the map, not a silent created_at guess.
    _plan, errors2 = await script.plan_counts(
        sqlite_engine, ["tenants"], source=SOURCE, target=target, since="2026-01-01", until=None
    )
    assert errors2 and errors2[0][1].startswith("KeyError")


class _UndisposableEngine:
    """``main()`` disposes the engine it made; the fixture's must outlive the
    call for the assertions, so hand the script a thin proxy."""

    def __init__(self, engine) -> None:
        self._engine = engine

    @property
    def dialect(self):
        # The script picks the window bind type off ``engine.dialect.name``.
        return self._engine.dialect

    def begin(self):
        return self._engine.begin()

    def connect(self):
        return self._engine.connect()

    async def dispose(self) -> None:
        return None


async def _run_main(monkeypatch, engine, argv: list[str]) -> int:
    proxy = _UndisposableEngine(engine)
    monkeypatch.setattr(script, "create_async_engine", lambda *_a, **_k: proxy)
    monkeypatch.setattr(sys, "argv", ["reassign_default_tenant_rows.py", *argv])
    return await script.main()


@pytest.mark.asyncio
async def test_commit_is_refused_when_any_table_errors_and_nothing_moves(sqlite_engine, monkeypatch, capsys) -> None:
    now = datetime.now(UTC)
    deal_id, doc_id = await _seed(sqlite_engine, tenant=SOURCE, when=now - timedelta(days=1))
    rc = await _run_main(
        monkeypatch, sqlite_engine,
        ["--clerk-org", ORG, "--created-after", (now - timedelta(days=30)).date().isoformat(),
         "--tables", "deals,documents,no_such_table", "--commit"],
    )
    assert rc == 3
    out = capsys.readouterr()
    assert "refusing to commit" in out.err
    assert "no_such_table" in out.out and "ERROR" in out.out
    assert "APPLYING" not in out.out
    assert await _tenant_of(sqlite_engine, "deals", deal_id) == str(SOURCE)
    assert await _tenant_of(sqlite_engine, "documents", doc_id) == str(SOURCE)


@pytest.mark.asyncio
async def test_commit_moves_documents_with_the_deal_inside_the_window(sqlite_engine, monkeypatch, capsys) -> None:
    target = script._coerce_tenant_id(ORG)
    now = datetime.now(UTC)
    deal_id, doc_id = await _seed(sqlite_engine, tenant=SOURCE, when=now - timedelta(days=1))
    old_deal, old_doc = await _seed(sqlite_engine, tenant=SOURCE, when=now - timedelta(days=400))

    # Dry run prints the per-table plan with the window column and changes nothing.
    rc = await _run_main(
        monkeypatch, sqlite_engine,
        ["--clerk-org", ORG, "--created-after", (now - timedelta(days=30)).date().isoformat(), "--tables", "deals,documents"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== PLAN" in out and "uploaded_at" in out and "Dry-run only" in out
    assert await _tenant_of(sqlite_engine, "documents", doc_id) == str(SOURCE)

    rc = await _run_main(
        monkeypatch, sqlite_engine,
        ["--clerk-org", ORG, "--created-after", (now - timedelta(days=30)).date().isoformat(), "--tables", "deals,documents", "--commit"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "moved 1" in out
    assert await _tenant_of(sqlite_engine, "deals", deal_id) == str(target)
    assert await _tenant_of(sqlite_engine, "documents", doc_id) == str(target)
    # Rows before the window stay put — on BOTH tables.
    assert await _tenant_of(sqlite_engine, "deals", old_deal) == str(SOURCE)
    assert await _tenant_of(sqlite_engine, "documents", old_doc) == str(SOURCE)
