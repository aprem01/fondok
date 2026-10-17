"""Duplicate a deal — ``POST /deals/{deal_id}/duplicate``.

Sam's QA plan: "Since re-extraction resets reviewed fields, I'll use a
duplicate Kimpton deal or preserve the existing assumptions before
testing." This route makes that copy inside the caller's tenant so the
original deal's reviewed extractions and analyst assumptions stay
untouched while the copy is re-extracted / experimented on.

What the copy carries (table by table, every table with a ``deal_id`` in
``app/migrations.py``):

==========================  ======  =========================================
table                       action  why
==========================  ======  =========================================
deals                       COPY    every column; new id, name
                                    ``"<name> (copy YYYY-MM-DD)"``, fresh
                                    created_at / updated_at.
                                    ``field_overrides`` (values AND notes) is
                                    copied verbatim apart from document ids.
documents                   COPY    new document ids, SAME ``storage_key`` /
                                    ``content_hash`` (the bytes are never
                                    re-uploaded); doc type, user doc type,
                                    doc_subtype, fiscal year, status etc. kept.
extraction_results          COPY    the LATEST row per document only — that
                                    row holds the analyst's field reviews
                                    (accepted / edited / rejected).
scenarios                   COPY    re-keyed (new ids), ``last_run_id`` NULL
                                    because no run is copied.
cell_comments               COPY    body prefixed "[Copied from <deal>]";
                                    ``hist:<doc>::`` keys re-pointed at the
                                    copied documents.
document_chunks (PG only)   COPY    server-side INSERT … SELECT, so search /
                                    memo retrieval work on the copy without
                                    re-embedding. Best effort (savepoint).
engine_outputs              SKIP    the copy shows "run the model"; the user
                                    runs it.
lineage_records             SKIP    per-run evidence; rebuilt by the run.
verification_reports        SKIP    per-run / per-memo artefacts.
critic_findings/_reports    SKIP    per-memo artefacts.
memo_edits                  SKIP    edits to a memo the copy does not have.
due_diligence_questions     SKIP    regenerated from engine outputs.
broker_questions / _qa      SKIP    correspondence history of the original;
                                    answers an analyst adopted already live
                                    in ``field_overrides`` with their notes.
model_calls                 SKIP    LLM cost history of the original.
pending_batches             SKIP    in-flight jobs of the original.
audit_log                   SKIP    the copy's history starts at
                                    ``deal.duplicated``.
==========================  ======  =========================================

Storage: raw-store keys embed the ORIGINAL deal id in their path
(``{tenant}/{deal}/{hash}-{file}``) but every reader resolves the stored
key verbatim (``RawStore.get(storage_key)``), so the copied row simply keeps
the original key — download, signed download and re-extract all read the
same object. Deleting the copy never removes those objects: hard-deleting a
deal touches no blobs, and the per-document delete skips the blob while any
other ``documents`` row still references the key.

Every statement on tenant data carries a ``tenant_id`` predicate; the
source deal must belong to the caller (404 otherwise).
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth import AuthContext, get_current_auth
from ..database import get_session
from .deals import (
    DealRecord,
    _deal_columns,
    _row_to_record,
    get_tenant_id,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: Document statuses owned by a running background task. Copying such a row
#: would strand the copy mid-pipeline (no task drives it), so we refuse.
_IN_FLIGHT_STATUSES = frozenset({"PARSING", "CLASSIFYING", "EXTRACTING"})

COPIED_COMMENT_PREFIX = "[Copied from "

#: Tables the copy deliberately leaves behind (reported in the response so
#: the UI / a reviewer can see exactly what was not carried over).
SKIPPED_TABLES: tuple[str, ...] = (
    "engine_outputs",
    "lineage_records",
    "verification_reports",
    "critic_findings",
    "critic_reports",
    "memo_edits",
    "due_diligence_questions",
    "broker_questions",
    "broker_qa_pairs",
    "model_calls",
    "pending_batches",
    "audit_log",
)


class DuplicateDealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal: DealRecord
    source_deal_id: UUID
    #: Rows copied per table.
    copied: dict[str, int]
    #: Tables intentionally not copied.
    skipped: list[str]


def _is_sqlite(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "sqlite"


async def _table_columns(
    session: AsyncSession, table: str
) -> dict[str, str] | None:
    """``{column: data_type}`` for ``table``, or None when it doesn't exist.

    Generated columns (Postgres ``document_chunks.fts``) are excluded — they
    cannot be written.
    """
    if _is_sqlite(session):
        rows = (await session.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        cols = {str(r[1]): str(r[2] or "").lower() for r in rows}
    else:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = :t AND table_schema = current_schema() "
                    "AND is_generated = 'NEVER'"
                ),
                {"t": table},
            )
        ).fetchall()
        cols = {str(r[0]): str(r[1] or "").lower() for r in rows}
    return cols or None


def _remap_text(value: str, id_map: dict[str, str]) -> str:
    for old, new in id_map.items():
        if old in value:
            value = value.replace(old, new)
    return value


async def _insert_row(
    session: AsyncSession,
    *,
    table: str,
    columns: dict[str, str],
    row: dict[str, Any],
    remap_columns: frozenset[str] = frozenset(),
    id_map: dict[str, str] | None = None,
) -> None:
    """Insert ``row`` (already carrying its new ids) into ``table``.

    Values are written back in the shape they were read: on Postgres JSON
    columns come back as Python objects and are re-serialised + CAST; UUIDs
    are bound as strings like every other insert in the worker. Columns in
    ``remap_columns`` get old→new document ids substituted in their text.
    """
    is_sqlite = _is_sqlite(session)
    names: list[str] = []
    placeholders: list[str] = []
    params: dict[str, Any] = {}
    for i, (col, dtype) in enumerate(columns.items()):
        if col not in row:
            continue
        val = row[col]
        is_json = (not is_sqlite) and dtype in ("json", "jsonb")
        if val is not None and (is_json or isinstance(val, (dict, list))):
            val = json.dumps(val, default=str)
        elif isinstance(val, _uuid.UUID):
            val = str(val)
        if id_map and col in remap_columns and isinstance(val, str):
            val = _remap_text(val, id_map)
        key = f"p{i}"
        names.append(col)
        params[key] = val
        placeholders.append(
            f"CAST(:{key} AS {dtype.upper()})" if is_json else f":{key}"
        )
    await session.execute(
        text(
            f"INSERT INTO {table} ({', '.join(names)}) "
            f"VALUES ({', '.join(placeholders)})"
        ),
        params,
    )


def _copy_name(name: str, today: str) -> str:
    return f"{name} (copy {today})"


@router.post(
    "/{deal_id}/duplicate",
    response_model=DuplicateDealResponse,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_deal(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> DuplicateDealResponse:
    """Copy a deal, its documents, reviewed extractions, assumptions and
    scenarios into a new deal in the same tenant. Engine outputs are NOT
    copied — the new deal must be re-run. See the module docstring for the
    per-table decision."""
    tenant = str(tenant_id)
    src = str(deal_id)

    source = (
        await session.execute(
            text("SELECT * FROM deals WHERE id = :id AND tenant_id = :tenant"),
            {"id": src, "tenant": tenant},
        )
    ).first()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    source_row = dict(source._mapping)

    docs = [
        dict(r._mapping)
        for r in (
            await session.execute(
                text(
                    "SELECT * FROM documents "
                    "WHERE deal_id = :deal AND tenant_id = :tenant "
                    "ORDER BY uploaded_at ASC"
                ),
                {"deal": src, "tenant": tenant},
            )
        ).fetchall()
    ]
    busy = [
        d.get("filename")
        for d in docs
        if str(d.get("status") or "").upper() in _IN_FLIGHT_STATUSES
    ]
    if busy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Some documents are still being processed "
                f"({', '.join(str(b) for b in busy)}). Wait for extraction "
                "to finish, then duplicate the deal."
            ),
        )

    new_deal = str(uuid4())
    now = datetime.now(UTC)
    copied: dict[str, int] = {}
    # old document id → new document id (also used to re-point JSON blobs
    # and ``hist:<doc>::field`` comment keys at the copied documents).
    doc_map: dict[str, str] = {str(d["id"]): str(uuid4()) for d in docs}

    # ── deals ──────────────────────────────────────────────────────────
    deal_cols = await _table_columns(session, "deals") or {}
    new_name = _copy_name(str(source_row.get("name") or "Deal"), now.date().isoformat())
    new_row = {
        **source_row,
        "id": new_deal,
        "name": new_name,
        "created_at": now,
        "updated_at": now,
    }
    await _insert_row(
        session,
        table="deals",
        columns=deal_cols,
        row=new_row,
        remap_columns=frozenset({"field_overrides"}),
        id_map=doc_map,
    )
    copied["deals"] = 1

    # ── documents + latest extraction_results ──────────────────────────
    doc_cols = await _table_columns(session, "documents") or {}
    er_cols = await _table_columns(session, "extraction_results") or {}
    copied["documents"] = 0
    copied["extraction_results"] = 0
    for d in docs:
        old_id = str(d["id"])
        await _insert_row(
            session,
            table="documents",
            columns=doc_cols,
            row={**d, "id": doc_map[old_id], "deal_id": new_deal},
            remap_columns=frozenset({"extraction_data"}),
            id_map=doc_map,
        )
        copied["documents"] += 1
        latest = (
            await session.execute(
                text(
                    "SELECT * FROM extraction_results "
                    "WHERE document_id = :doc AND deal_id = :deal "
                    "AND tenant_id = :tenant "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"doc": old_id, "deal": src, "tenant": tenant},
            )
        ).first()
        if latest is not None:
            er = dict(latest._mapping)
            await _insert_row(
                session,
                table="extraction_results",
                columns=er_cols,
                row={
                    **er,
                    "id": str(uuid4()),
                    "document_id": doc_map[old_id],
                    "deal_id": new_deal,
                },
                remap_columns=frozenset({"fields", "confidence_report"}),
                id_map=doc_map,
            )
            copied["extraction_results"] += 1

    # ── scenarios ──────────────────────────────────────────────────────
    sc_cols = await _table_columns(session, "scenarios")
    copied["scenarios"] = 0
    has_base = False
    if sc_cols:
        for r in (
            await session.execute(
                text(
                    "SELECT * FROM scenarios "
                    "WHERE deal_id = :deal AND tenant_id = :tenant "
                    "ORDER BY created_at ASC"
                ),
                {"deal": src, "tenant": tenant},
            )
        ).fetchall():
            s = dict(r._mapping)
            has_base = has_base or bool(s.get("is_base"))
            await _insert_row(
                session,
                table="scenarios",
                columns=sc_cols,
                row={**s, "id": str(uuid4()), "deal_id": new_deal, "last_run_id": None},
                remap_columns=frozenset({"overrides"}),
                id_map=doc_map,
            )
            copied["scenarios"] += 1
    if not has_base:
        # Legacy source without a Base scenario — give the copy the one every
        # freshly created deal gets.
        from .scenarios import create_base_scenario_for_deal

        await create_base_scenario_for_deal(
            session, deal_id=new_deal, tenant_id=tenant
        )

    # ── cell comments (marked as copied) ───────────────────────────────
    cc_cols = await _table_columns(session, "cell_comments")
    copied["cell_comments"] = 0
    if cc_cols:
        prefix = f"{COPIED_COMMENT_PREFIX}{source_row.get('name') or 'original deal'}] "
        for r in (
            await session.execute(
                text(
                    "SELECT * FROM cell_comments "
                    "WHERE deal_id = :deal AND tenant_id = :tenant "
                    "ORDER BY created_at ASC"
                ),
                {"deal": src, "tenant": tenant},
            )
        ).fetchall():
            c = dict(r._mapping)
            await _insert_row(
                session,
                table="cell_comments",
                columns=cc_cols,
                row={
                    **c,
                    "id": str(uuid4()),
                    "deal_id": new_deal,
                    "body": prefix + str(c.get("body") or ""),
                },
                remap_columns=frozenset({"cell_key"}),
                id_map=doc_map,
            )
            copied["cell_comments"] += 1

    # ── document chunks (Postgres only; server-side copy) ──────────────
    if not _is_sqlite(session) and doc_map:
        chunk_cols = await _table_columns(session, "document_chunks")
        if chunk_cols:
            rest = [
                c for c in chunk_cols if c not in ("id", "deal_id", "document_id")
            ]
            n = 0
            try:
                async with session.begin_nested():
                    for old_id, new_id in doc_map.items():
                        res = await session.execute(
                            text(
                                f"INSERT INTO document_chunks "
                                f"(id, deal_id, document_id, {', '.join(rest)}) "
                                f"SELECT gen_random_uuid(), CAST(:new_deal AS UUID), "
                                f"CAST(:new_doc AS UUID), {', '.join(rest)} "
                                f"FROM document_chunks "
                                f"WHERE document_id = CAST(:old_doc AS UUID) "
                                f"AND deal_id = CAST(:src AS UUID) "
                                f"AND tenant_id = CAST(:tenant AS UUID)"
                            ),
                            {
                                "new_deal": new_deal,
                                "new_doc": new_id,
                                "old_doc": old_id,
                                "src": src,
                                "tenant": tenant,
                            },
                        )
                        n += res.rowcount or 0
                copied["document_chunks"] = n
            except Exception:  # noqa: BLE001 — search index is a nicety
                logger.exception(
                    "duplicate_deal: document_chunks copy failed for %s → %s",
                    src, new_deal,
                )
                copied["document_chunks"] = 0

    await log_audit(
        session,
        tenant_id=tenant,
        deal_id=new_deal,
        actor_id=auth.user_id,
        actor_email=auth.email,
        action="deal.duplicated",
        resource_type="deal",
        resource_id=new_deal,
        metadata={
            "source_deal_id": src,
            "source_name": source_row.get("name"),
            "name": new_name,
            "copied": copied,
            "skipped": list(SKIPPED_TABLES),
            "document_id_map": doc_map,
        },
    )
    await session.commit()

    from ..services.pipeline import invalidate as _pipeline_invalidate

    _pipeline_invalidate(tenant_id)

    refreshed = (
        await session.execute(
            text(
                f"SELECT {await _deal_columns(session)} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": new_deal, "tenant": tenant},
        )
    ).first()
    assert refreshed is not None
    logger.info(
        "deals.duplicate: %s → %s tenant=%s copied=%s", src, new_deal, tenant, copied
    )
    return DuplicateDealResponse(
        deal=_row_to_record(dict(refreshed._mapping)),
        source_deal_id=deal_id,
        copied=copied,
        skipped=list(SKIPPED_TABLES),
    )
