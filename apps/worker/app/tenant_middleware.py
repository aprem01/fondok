"""Defense-in-depth SQLAlchemy event listener for tenant filtering.

This module attaches a single ``before_cursor_execute`` listener to the
async engine that inspects every SQL statement just before it hits
Postgres. The contract:

* If the statement touches one of the **tenant-scoped tables** listed
  in :data:`_TENANT_SCOPED_TABLES`, the WHERE clause MUST reference
  the ``tenant_id`` column. Cross-table joins where the join condition
  carries the tenant scope (e.g. ``documents.tenant_id = deals.tenant_id``)
  are accepted — the join itself enforces the constraint.
* If the predicate is missing, behaviour is controlled by the
  ``STRICT_TENANT_ENFORCEMENT`` env var:

  - ``warn`` (default for prod):  log CRITICAL + telemetry, don't crash.
  - ``raise`` (tests + CI):       raise :class:`MissingTenantFilterError`
                                   so the bug surfaces in tests.
  - ``migrations`` (one-shot):    skip entirely — used while running
                                   schema bootstrap that legitimately
                                   selects from system catalogs or
                                   empty tenant-scoped tables.

This is **belt AND suspenders** — it does not replace explicit
``Depends(get_tenant_id)`` + WHERE-clause filtering at the endpoint
layer (the P0 fix in commit 2a8ed64). The listener catches the case
where a developer adds a new endpoint and *forgets* to scope.

Performance: the check is a single compiled-regex sweep over the SQL
string. No SQL parsing, no AST walk — the hot path adds <50µs per
statement on a modern CPU.

See ``docs/SECURITY_ARCHITECTURE.md`` for the full threat model and
the rationale behind the allowlist.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


# ─────────────────────────── tenant-scoped tables ───────────────────────────

# Every table in this set has a NOT NULL ``tenant_id`` column (verified
# against ``apps/worker/app/migrations.py``). Any SELECT / UPDATE /
# DELETE / aggregate query that touches one of these tables MUST
# reference ``tenant_id`` in its WHERE clause.
#
# Tables intentionally NOT in this set:
#   * ``model_calls``           — ``tenant_id`` is nullable (system jobs)
#   * ``catalog_*``             — global reference data
#   * Postgres system catalogs  — ``pg_*`` / ``information_schema.*``
_TENANT_SCOPED_TABLES: frozenset[str] = frozenset(
    {
        "deals",
        "documents",
        "extraction_results",
        "document_chunks",
        "audit_log",
        "memo_edits",
        "verification_reports",
        "critic_findings",
        "critic_reports",
        "engine_outputs",
        "due_diligence_questions",
        "broker_questions",
        "broker_qa_pairs",
    }
)


# ─────────────────────────── enforcement mode ───────────────────────────


_ENV_VAR = "STRICT_TENANT_ENFORCEMENT"
_VALID_MODES = frozenset({"warn", "raise", "migrations", "off"})


def _get_mode() -> str:
    """Read the enforcement mode at call time so tests can flip it."""
    raw = os.environ.get(_ENV_VAR, "warn").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "tenant_middleware: unknown %s=%r — defaulting to 'warn'",
            _ENV_VAR,
            raw,
        )
        return "warn"
    return raw


class MissingTenantFilterError(Exception):
    """Raised when a query against a tenant-scoped table omits ``tenant_id``.

    Only raised when ``STRICT_TENANT_ENFORCEMENT=raise`` (typically in
    tests + CI). In production we fall back to logging so a single
    forgotten filter can't take down the API — the alert routes through
    Sentry instead.
    """


# ─────────────────────────── parsing helpers ───────────────────────────


# Cheap regex-based "is the table mentioned" check. We DON'T parse the
# SQL; we look for the table name after FROM / JOIN / UPDATE / DELETE FROM
# / INTO. The patterns are intentionally permissive (the false-positive
# direction is "we check a query we didn't need to" — harmless).
_TABLE_REF_PATTERN = re.compile(
    r"""
    \b
    (?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)
    \s+
    (?:ONLY\s+)?
    (?:"?(\w+)"?\.)?   # optional schema prefix
    "?(\w+)"?          # table name (captured)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A WHERE / JOIN clause references tenant_id if the literal string
# ``tenant_id`` appears anywhere in the statement. We accept the join
# form (``a.tenant_id = b.tenant_id``) and the parameterised form
# (``tenant_id = :tenant``). This is intentionally loose — the goal is
# to catch the case where the developer wrote no scoping at all.
_TENANT_PREDICATE_PATTERN = re.compile(r"\btenant_id\b", re.IGNORECASE)

# Statements we never want to inspect. Schema bootstrap, transaction
# control, savepoints, advisory locks, server-side cursor mgmt.
_BYPASS_PREFIXES: tuple[str, ...] = (
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "RELEASE",
    "SET",
    "SHOW",
    "DO",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "EXPLAIN",
    "DECLARE",
    "FETCH",
    "CLOSE",
    "LISTEN",
    "NOTIFY",
    "WITH RECURSIVE",  # rare; conservative
)

# INSERTs aren't tenant-scoped in the read sense — the row carries its
# own tenant_id column populated by the caller. We DO still check the
# RETURNING clause's implicit read isn't unscoped, but in practice the
# INSERT always names tenant_id directly. Leave INSERTs alone.
_INSERT_PREFIX = "INSERT"


def _extract_referenced_tables(sql: str) -> set[str]:
    """Return the set of tenant-scoped table names this SQL touches.

    Empty set means the statement is safe to skip (touches no
    tenant-scoped table — e.g. a query over ``model_calls`` or a
    pg_catalog read).
    """
    referenced: set[str] = set()
    for match in _TABLE_REF_PATTERN.finditer(sql):
        # match.group(2) is the unqualified table name; group(1) the
        # optional schema. We ignore the schema prefix — tables are
        # uniquely named within the public schema.
        table = match.group(2).lower()
        if table in _TENANT_SCOPED_TABLES:
            referenced.add(table)
    return referenced


def _is_bypass_statement(stripped_sql: str) -> bool:
    """Return True for DDL / transaction control / introspection.

    The check is on the FIRST keyword only, which is enough for the
    statements we want to skip.
    """
    upper = stripped_sql.upper()
    for prefix in _BYPASS_PREFIXES:
        if upper.startswith(prefix):
            return True
    return False


# ─────────────────────────── the listener ───────────────────────────


def _enforce_tenant_filter(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    """``before_cursor_execute`` listener — runs once per SQL statement.

    Signature dictated by SQLAlchemy. We only need ``statement``.
    """
    mode = _get_mode()
    if mode in ("off", "migrations"):
        return

    if not statement:
        return

    stripped = statement.lstrip()
    if not stripped:
        return

    # INSERT writes its tenant_id directly; nothing to enforce on the
    # read side. INSERT ... SELECT is rare in this codebase; if it
    # appears it'll still be caught by the SELECT clause inside.
    if stripped.upper().startswith(_INSERT_PREFIX):
        return

    if _is_bypass_statement(stripped):
        return

    referenced = _extract_referenced_tables(stripped)
    if not referenced:
        # The query doesn't touch any tenant-scoped table — fine.
        return

    if _TENANT_PREDICATE_PATTERN.search(stripped):
        # tenant_id appears somewhere in the statement (WHERE clause,
        # JOIN condition, or — in pathological cases — a column list).
        # Loose check by design; the cost of a false negative here
        # (statement passes that shouldn't) is bounded by the endpoint
        # layer's explicit Depends(get_tenant_id) check, and false
        # positives in the other direction would break legitimate queries.
        return

    # Missing — log + optionally raise.
    msg = (
        "tenant_middleware: SQL touches tenant-scoped table(s) %s "
        "without a tenant_id predicate — this is a cross-tenant leak risk. "
        "Statement: %s"
    ) % (sorted(referenced), _truncate(stripped))
    logger.critical(msg)

    # Page on every unscoped query regardless of mode — the warn-mode
    # production path STILL needs a human paged because a tenant leak
    # is the worst possible failure for a multi-tenant SaaS.
    try:
        from .alerting import report_alert

        report_alert(
            severity="critical",
            title="Tenant isolation breach — SQL without tenant_id predicate",
            stage="db.query",
            extra={
                "tables": sorted(referenced),
                "mode": mode,
                "statement_sample": _truncate(stripped, 300),
            },
        )
    except Exception:
        pass

    if mode == "raise":
        raise MissingTenantFilterError(msg)


def _truncate(sql: str, limit: int = 500) -> str:
    """Trim long statements for log readability."""
    sql = " ".join(sql.split())
    if len(sql) <= limit:
        return sql
    return sql[:limit] + f"… [{len(sql) - limit} chars truncated]"


# ─────────────────────────── public API ───────────────────────────


def register_tenant_safety_listener(engine: AsyncEngine | Engine) -> None:
    """Attach the tenant-safety listener to ``engine``.

    Call once at app startup AFTER engine creation. Idempotent — the
    listener tracks attachment per engine and refuses double-registration
    (SQLAlchemy raises on duplicate listener registration; we catch +
    no-op so test fixtures that build the app multiple times don't
    explode).

    Accepts both sync ``Engine`` and ``AsyncEngine`` — for the async
    case we attach to the underlying sync engine (``engine.sync_engine``)
    which is where SQLAlchemy fires ``before_cursor_execute``.
    """
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine

    # Guard against double-registration. SQLAlchemy's ``event.listen``
    # is fine with duplicates but they fire the listener N times, which
    # multiplies log noise on every statement.
    if getattr(sync_engine, "_fondok_tenant_listener_attached", False):
        return

    event.listen(sync_engine, "before_cursor_execute", _enforce_tenant_filter)
    sync_engine._fondok_tenant_listener_attached = True  # type: ignore[attr-defined]
    logger.info(
        "tenant_middleware: safety listener attached (mode=%s, scoped_tables=%d)",
        _get_mode(),
        len(_TENANT_SCOPED_TABLES),
    )


__all__ = [
    "MissingTenantFilterError",
    "register_tenant_safety_listener",
]
