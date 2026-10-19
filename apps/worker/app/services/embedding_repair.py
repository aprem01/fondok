"""Embedding repair — every stored chunk ends up with a current-model vector.

Why (2026-10-19): chunk embeddings were best-effort and silent. The Voyage
account behind them was rate-limited to 3 RPM / 10K TPM, so every full
batch 429'd and 11,601 of 11,792 production chunks were stored with
``embedding IS NULL`` — search quietly ran full-text only. Switching the
provider (``extraction/embeddings.py``) also means the 191 Voyage vectors
are in the wrong space for OpenAI query vectors.

A chunk NEEDS embedding when ``embedding IS NULL`` or ``embedding_model``
is not the current ``EMBEDDING_MODEL`` (the pre-switch rows carry NULL).

* ``embed_pending_chunks`` — the one implementation of "embed what is
  missing", for one tenant (optionally one deal), capped per call. Used
  by ``POST /deals/{id}/backfill_embeddings`` and by the loop below.
* ``tick_once`` / ``start_embedding_repair`` / ``stop_embedding_repair``
  — a background loop on the ``extraction_watchdog`` pattern: first
  tick at startup, then every ``EMBEDDING_REPAIR_TICK_SECONDS``, at most
  ``EMBEDDING_REPAIR_MAX_CHUNKS_PER_TICK`` chunks per tick, tenant by
  tenant (``services/tenant_scan.py``) so it works as ``fondok_app``
  under RLS. A no-op when ``OPENAI_API_KEY`` is unset or the chunk table
  is absent (SQLite / no pgvector).

Single-replica caveat as for the watchdog: N replicas would each run the
loop; harmless — the UPDATE is idempotent (same text → same vector).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.context import current_tenant_id
from ..config import get_settings
from ..database import get_session_factory
from ..extraction import embeddings
from .tenant_scan import plan_tenant_scan

logger = logging.getLogger(__name__)

#: Rows that need a (current-model) vector. ``:model`` is bound to
#: ``embeddings.EMBEDDING_MODEL``.
NEEDS_EMBEDDING_SQL = "(embedding IS NULL OR embedding_model IS DISTINCT FROM :model)"

_LOOP_TASK: asyncio.Task[Any] | None = None
_STOP_EVENT: asyncio.Event | None = None

_STATS: dict[str, Any] = {
    "ticks": 0,
    "last_tick_at": None,
    "last_tick_embedded": 0,
    "last_tick_failed": 0,
    "embedded_since_boot": 0,
    "failed_since_boot": 0,
    "last_error": None,
}


@dataclass
class RepairResult:
    embedded: int = 0
    failed: int = 0


def repair_stats() -> dict[str, Any]:
    """Counters for ``GET /admin/health`` (process-local)."""
    return {
        **_STATS,
        "provider": embeddings.EMBEDDING_PROVIDER,
        "model": embeddings.EMBEDDING_MODEL,
        "enabled": embeddings.is_enabled(),
    }


async def count_chunks(
    session: AsyncSession, *, tenant_id: str, deal_id: str | None = None
) -> tuple[int, int]:
    """``(total, needing_embedding)`` for a tenant (optionally one deal)."""
    deal_sql = " AND deal_id = :deal" if deal_id else ""
    params: dict[str, Any] = {"tenant": str(tenant_id), "model": embeddings.EMBEDDING_MODEL}
    if deal_id:
        params["deal"] = str(deal_id)
    row = (
        await session.execute(
            text(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN {NEEDS_EMBEDDING_SQL} THEN 1 ELSE 0 END) AS missing
                  FROM document_chunks
                 WHERE tenant_id = :tenant{deal_sql}
                """
            ),
            params,
        )
    ).first()
    if row is None:
        return 0, 0
    m = row._mapping
    return int(m["total"] or 0), int(m["missing"] or 0)


async def embed_pending_chunks(
    session: AsyncSession,
    *,
    tenant_id: str,
    deal_id: str | None = None,
    max_chunks: int,
    batch_size: int = embeddings.EMBEDDING_MAX_BATCH,
) -> RepairResult:
    """Embed up to ``max_chunks`` chunks of one tenant that need it.

    Commits after every batch, so progress survives a later failure. A
    batch that still fails after the client's retries stops this call
    (the next call / tick resumes); its chunks are counted as failed. The
    caller must have published ``tenant_id`` on ``current_tenant_id`` so
    the RLS GUC hook scopes these transactions.
    """
    from ..extraction.context_store import _to_vector_literal

    result = RepairResult()
    batch_size = max(1, min(int(batch_size), embeddings.EMBEDDING_MAX_BATCH))
    deal_sql = " AND deal_id = :deal" if deal_id else ""
    seen: set[str] = set()
    while result.embedded + result.failed < max_chunks:
        n = min(batch_size, max_chunks - result.embedded - result.failed)
        params: dict[str, Any] = {
            "tenant": str(tenant_id),
            "model": embeddings.EMBEDDING_MODEL,
            "n": n,
        }
        if deal_id:
            params["deal"] = str(deal_id)
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT id, chunk_text
                      FROM document_chunks
                     WHERE tenant_id = :tenant{deal_sql}
                       AND {NEEDS_EMBEDDING_SQL}
                     ORDER BY created_at, document_id, chunk_index
                     LIMIT :n
                    """
                ),
                params,
            )
        ).fetchall()
        ids = [str(r._mapping["id"]) for r in rows]
        # Guard against re-selecting rows an UPDATE did not change.
        if not ids or seen.issuperset(ids):
            break
        seen.update(ids)
        texts = [r._mapping["chunk_text"] or "" for r in rows]
        try:
            vectors = await embeddings.embed_batch(texts, input_type="document")
        except Exception as exc:  # noqa: BLE001 — resume on the next call
            result.failed += len(ids)
            _STATS["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(
                "embedding_repair: batch of %d failed for tenant=%s deal=%s (%s) — "
                "will retry on the next tick",
                len(ids), tenant_id, deal_id, exc,
            )
            await session.rollback()
            break
        for cid, vec in zip(ids, vectors, strict=True):
            await session.execute(
                text(
                    "UPDATE document_chunks "
                    "SET embedding = CAST(:vec AS vector), embedding_model = :model "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {
                    "id": cid,
                    "vec": _to_vector_literal(vec),
                    "model": embeddings.EMBEDDING_MODEL,
                    "tenant": str(tenant_id),
                },
            )
        await session.commit()
        result.embedded += len(ids)
    return result


async def tick_once() -> RepairResult:
    """One repair pass across tenants, capped at
    ``EMBEDDING_REPAIR_MAX_CHUNKS_PER_TICK``. Test seam + loop body."""
    total = RepairResult()
    if not embeddings.is_enabled():
        return total
    settings = get_settings()
    budget = int(settings.EMBEDDING_REPAIR_MAX_CHUNKS_PER_TICK)
    factory = get_session_factory()
    tenant_token = current_tenant_id.set(current_tenant_id.get())
    try:
        async with factory() as session:
            from ..extraction.context_store import _table_exists

            if not await _table_exists(session):
                return total
            await session.commit()
            needs_params = {"model": embeddings.EMBEDDING_MODEL}
            distinct_sql = (
                f"SELECT DISTINCT tenant_id FROM document_chunks WHERE {NEEDS_EMBEDDING_SQL}"
            )
            plan = await plan_tenant_scan(
                distinct_tenants_sql=distinct_sql,
                params=needs_params,
                job="embedding_repair",
            )
            if plan.tenants is not None:
                tenants = list(plan.tenants)
            else:
                rows = (await session.execute(text(distinct_sql), needs_params)).fetchall()
                await session.commit()
                tenants = sorted({str(r[0]) for r in rows if r[0] is not None})
            for tenant in tenants:
                remaining = budget - total.embedded - total.failed
                if remaining <= 0:
                    break
                current_tenant_id.set(tenant)
                try:
                    r = await embed_pending_chunks(
                        session, tenant_id=tenant, max_chunks=remaining
                    )
                except Exception as exc:  # noqa: BLE001 — next tenant
                    logger.exception("embedding_repair: tenant=%s failed: %s", tenant, exc)
                    await session.rollback()
                    continue
                finally:
                    await session.commit()
                total.embedded += r.embedded
                total.failed += r.failed
    finally:
        current_tenant_id.reset(tenant_token)
        _STATS["ticks"] += 1
        _STATS["last_tick_at"] = datetime.now(UTC).isoformat()
        _STATS["last_tick_embedded"] = total.embedded
        _STATS["last_tick_failed"] = total.failed
        _STATS["embedded_since_boot"] += total.embedded
        _STATS["failed_since_boot"] += total.failed
    return total


async def _run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(5.0, float(settings.EMBEDDING_REPAIR_TICK_SECONDS))
    logger.info(
        "embedding_repair: started (tick=%.0fs, max_chunks_per_tick=%d, model=%s, enabled=%s)",
        interval,
        int(settings.EMBEDDING_REPAIR_MAX_CHUNKS_PER_TICK),
        embeddings.EMBEDDING_MODEL,
        embeddings.is_enabled(),
    )
    while not stop_event.is_set():
        try:
            r = await tick_once()
            if r.embedded or r.failed:
                logger.info(
                    "embedding_repair: embedded=%d failed=%d this tick", r.embedded, r.failed
                )
        except Exception as ex:
            _STATS["last_error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
            logger.exception("embedding_repair: tick failed: %s", ex)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("embedding_repair: stopped")


def start_embedding_repair() -> bool:
    """Start the loop. Idempotent; no-op when
    ``EMBEDDING_REPAIR_ENABLED=False`` (tests drive ``tick_once``)."""
    global _LOOP_TASK, _STOP_EVENT
    if not get_settings().EMBEDDING_REPAIR_ENABLED:
        logger.info("embedding_repair: disabled via settings")
        return False
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return False
    _STOP_EVENT = asyncio.Event()
    _LOOP_TASK = asyncio.get_event_loop().create_task(
        _run_loop(_STOP_EVENT), name="embedding_repair"
    )
    return True


async def stop_embedding_repair() -> None:
    global _LOOP_TASK, _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _LOOP_TASK is not None:
        try:
            await asyncio.wait_for(_LOOP_TASK, timeout=5.0)
        except TimeoutError:
            _LOOP_TASK.cancel()
        except Exception:
            pass
        finally:
            _LOOP_TASK = None
            _STOP_EVENT = None


__all__ = [
    "NEEDS_EMBEDDING_SQL",
    "RepairResult",
    "count_chunks",
    "embed_pending_chunks",
    "repair_stats",
    "start_embedding_repair",
    "stop_embedding_repair",
    "tick_once",
]
