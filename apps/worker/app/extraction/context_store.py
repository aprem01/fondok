"""Context store — persist + search document chunks.

Phase 3 of the dynamic-extensibility refactor. Sits between the
parsing pipeline and the database / search endpoint:

  * ``index_parsed_document()`` — called from the parse + extract
    background task. Splits the parsed pages into chunks, embeds
    them via Voyage if configured, persists to ``document_chunks``.

  * ``search_chunks()`` — backs the GET /deals/{id}/search endpoint.
    Always runs Postgres FTS; ALSO does vector similarity when
    embeddings exist. Hybrid scoring is a simple weighted blend.

Robustness:
  * The chunk-write path is best-effort. A failed embed call (Voyage
    rate-limited, network blip) doesn't block the parse pipeline —
    chunks land with embedding=NULL and can be backfilled later.
  * On SQLite the ``document_chunks`` table doesn't exist (the
    pgvector + tsvector types are Postgres-only). All functions
    detect the missing table and no-op gracefully.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings
from .chunking import Chunk, chunk_pages
from .models import ParsedDocument

logger = logging.getLogger(__name__)


def _to_vector_literal(vec: list[float]) -> str:
    """Format a Python float list as the pgvector literal `[v1,v2,...]`.

    asyncpg + the vector type accept this as a string cast; we don't
    rely on a Python-side pgvector driver to keep the dep tree clean.
    """
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


async def _table_exists(session: AsyncSession) -> bool:
    """Cheap existence check — returns True only when document_chunks
    is present. SQLite test runs and Postgres deployments without
    pgvector both land here as False; the rest of the module no-ops.
    """
    try:
        await session.execute(text("SELECT 1 FROM document_chunks LIMIT 0"))
        return True
    except Exception:
        return False


async def index_parsed_document(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    document_id: str,
    parsed: ParsedDocument,
) -> int:
    """Chunk + persist a parsed document's pages.

    Returns the number of chunks written. 0 on SQLite (no table) or
    when the document has no text content.

    Embedding policy:
        * If ``embeddings.is_enabled()`` → embed each chunk batch
          and write the embedding column.
        * Else → write chunks with embedding=NULL. FTS still works.

    Idempotency: this function deletes any existing chunks for
    (deal_id, document_id) before writing new ones so reprocessing
    a document doesn't accumulate duplicates.
    """
    if not await _table_exists(session):
        logger.debug(
            "context_store: document_chunks table missing — skipping "
            "indexing for doc=%s",
            document_id,
        )
        return 0

    # Chunk source = (page_num, page_text) pairs from the parser.
    page_inputs: list[tuple[int, str]] = [
        (p.page_num, p.text or "") for p in parsed.pages
    ]
    chunks = chunk_pages(page_inputs)
    if not chunks:
        return 0

    # Wipe-and-rewrite for idempotent reprocessing.
    await session.execute(
        text(
            "DELETE FROM document_chunks WHERE deal_id = :deal "
            "AND document_id = :doc"
        ),
        {"deal": deal_id, "doc": document_id},
    )

    # Try to embed. Failures fall through to NULL embeddings — chunks
    # are still searchable via FTS, and a backfill job can repair later.
    vectors: list[list[float] | None] = [None] * len(chunks)
    if embeddings.is_enabled():
        try:
            texts = [c.text for c in chunks]
            embedded = await embeddings.embed_iter(texts, input_type="document")
            vectors = list(embedded)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "context_store: Voyage embed failed for doc=%s "
                "(%s); persisting chunks without embeddings",
                document_id,
                exc,
            )

    # Bulk insert. Use parameter binding for chunk_text (long, may
    # contain special chars) but format the vector literal inline
    # since asyncpg won't bind a Python list directly to the vector
    # type without an adapter.
    inserted = 0
    for chunk, vec in zip(chunks, vectors, strict=True):
        params: dict[str, Any] = {
            "id": str(uuid4()),
            "deal": deal_id,
            "doc": document_id,
            "tenant": tenant_id,
            "idx": chunk.chunk_index,
            "txt": chunk.text,
            "tokens": chunk.tokens,
            "page": chunk.source_page,
        }
        if vec is not None:
            vec_literal = _to_vector_literal(vec)
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id, deal_id, document_id, tenant_id,
                        chunk_index, chunk_text, tokens, source_page,
                        embedding
                    ) VALUES (
                        :id, :deal, :doc, :tenant,
                        :idx, :txt, :tokens, :page,
                        CAST(:vec AS vector)
                    )
                    """
                ),
                {**params, "vec": vec_literal},
            )
        else:
            await session.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id, deal_id, document_id, tenant_id,
                        chunk_index, chunk_text, tokens, source_page
                    ) VALUES (
                        :id, :deal, :doc, :tenant,
                        :idx, :txt, :tokens, :page
                    )
                    """
                ),
                params,
            )
        inserted += 1

    await session.commit()
    logger.info(
        "context_store: indexed doc=%s deal=%s chunks=%d embedded=%s",
        document_id,
        deal_id,
        inserted,
        "yes" if any(v is not None for v in vectors) else "no",
    )
    return inserted


async def search_chunks(
    session: AsyncSession,
    *,
    deal_id: str,
    query: str,
    k: int = 10,
) -> list[dict[str, Any]]:
    """Search a deal's chunks for the query string.

    Strategy:
        * Always do Postgres FTS using ``plainto_tsquery('english', :q)``
          + ``ts_rank_cd`` for the lexical score.
        * If embeddings are enabled AND the deal has at least one
          embedded chunk, ALSO compute cosine similarity against the
          query embedding and blend.
        * Hybrid score = 0.6 * vector + 0.4 * fts when both available;
          otherwise whichever signal we have.

    Returns up to k chunks: ``[{document_id, chunk_index, chunk_text,
    source_page, score}]`` sorted by score desc.
    """
    if not await _table_exists(session):
        return []
    q = (query or "").strip()
    if not q:
        return []

    # Pure-FTS baseline. Always works.
    fts_rows = (
        await session.execute(
            text(
                """
                SELECT id, document_id, chunk_index, chunk_text,
                       source_page,
                       ts_rank_cd(fts, plainto_tsquery('english', :q)) AS score
                  FROM document_chunks
                 WHERE deal_id = :deal
                   AND fts @@ plainto_tsquery('english', :q)
                 ORDER BY score DESC
                 LIMIT :k
                """
            ),
            {"deal": deal_id, "q": q, "k": k * 3},
        )
    ).fetchall()

    fts_scores: dict[str, float] = {}
    base: dict[str, dict[str, Any]] = {}
    for r in fts_rows:
        m = r._mapping
        cid = str(m["id"])
        fts_scores[cid] = float(m["score"] or 0.0)
        base[cid] = {
            "id": cid,
            "document_id": str(m["document_id"]),
            "chunk_index": int(m["chunk_index"]),
            "chunk_text": m["chunk_text"],
            "source_page": m.get("source_page"),
        }

    # Vector similarity layer — gated on Voyage AND on chunks having
    # been embedded for this deal.
    vec_scores: dict[str, float] = {}
    if embeddings.is_enabled():
        try:
            q_vec = (await embeddings.embed_batch([q], input_type="query"))[0]
            vec_literal = _to_vector_literal(q_vec)
            vec_rows = (
                await session.execute(
                    text(
                        """
                        SELECT id, document_id, chunk_index, chunk_text,
                               source_page,
                               1 - (embedding <=> CAST(:vec AS vector)) AS score
                          FROM document_chunks
                         WHERE deal_id = :deal AND embedding IS NOT NULL
                         ORDER BY embedding <=> CAST(:vec AS vector) ASC
                         LIMIT :k
                        """
                    ),
                    {"deal": deal_id, "vec": vec_literal, "k": k * 3},
                )
            ).fetchall()
            for r in vec_rows:
                m = r._mapping
                cid = str(m["id"])
                vec_scores[cid] = float(m["score"] or 0.0)
                if cid not in base:
                    base[cid] = {
                        "id": cid,
                        "document_id": str(m["document_id"]),
                        "chunk_index": int(m["chunk_index"]),
                        "chunk_text": m["chunk_text"],
                        "source_page": m.get("source_page"),
                    }
        except Exception as exc:  # noqa: BLE001 — fall back to FTS
            logger.warning(
                "context_store: vector search failed for deal=%s "
                "(%s); returning FTS-only results",
                deal_id,
                exc,
            )

    # Blend scores. Normalize within each channel so neither dominates
    # by raw magnitude. fts ranges 0..~0.1, cosine 0..1.
    def _norm(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return {}
        mx = max(d.values()) or 1.0
        return {k: v / mx for k, v in d.items()}

    norm_fts = _norm(fts_scores)
    norm_vec = _norm(vec_scores)
    final: list[dict[str, Any]] = []
    for cid, row in base.items():
        f = norm_fts.get(cid, 0.0)
        v = norm_vec.get(cid, 0.0)
        if vec_scores and fts_scores:
            score = 0.6 * v + 0.4 * f
        elif vec_scores:
            score = v
        else:
            score = f
        final.append({**row, "score": round(score, 4)})

    final.sort(key=lambda r: r["score"], reverse=True)
    return final[:k]


__all__ = ["index_parsed_document", "search_chunks"]
