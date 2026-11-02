"""A deal's AI Confidence — the ONE rollup of its extraction scores.

Wave 5 (Sam #5): every deal showed "AI Confidence 0%". Deal creation wrote
``deals.ai_confidence = 0.0`` until A11 and no code path wrote the column
after that; ``GET /deals/{id}/status`` averaged the extraction confidence but
never persisted it, so the header, the deal list and the exports (which read
the deal row) all showed the never-computed 0.

The rollup: the mean of ``confidence_report.overall`` over each EXTRACTED
document's LATEST extraction (a re-extraction supersedes the earlier row; a
FAILED document's empty extraction is not the deal's confidence). ``None``
when no extracted document carries a numeric score — rendered "—", never 0%.

``refresh_deal_ai_confidence`` writes it to ``deals.ai_confidence`` and is
called wherever the inputs change: an extraction lands, a field is corrected
or re-verified, a document is deleted. ``/status`` reads
``compute_deal_ai_confidence`` so the pill and the deal row cannot disagree.
The one-time backfill in ``app.migrations`` (``deals.ai_confidence_*``)
applies the same definition in SQL.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: ``documents.status`` of a document whose extraction counts.
_EXTRACTED = "EXTRACTED"


def _overall(blob: Any) -> float | None:
    """``confidence_report.overall`` as a float, or ``None`` when absent."""
    if isinstance(blob, str):
        try:
            blob = json.loads(blob) if blob else None
        except json.JSONDecodeError:
            return None
    if not isinstance(blob, dict):
        return None
    overall = blob.get("overall")
    # bool is an int subclass — a True/False is not a score.
    if isinstance(overall, bool) or not isinstance(overall, (int, float)):
        return None
    return float(overall)


async def compute_deal_ai_confidence(
    session: AsyncSession, *, deal_id: UUID | str, tenant_id: UUID | str
) -> float | None:
    """Mean extraction confidence over each extracted document's latest
    extraction; ``None`` when there is none."""
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            """
            SELECT er.document_id AS document_id,
                   er.confidence_report AS confidence_report
              FROM extraction_results er
              JOIN documents d
                ON d.id = er.document_id
               AND d.tenant_id = er.tenant_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.status = :extracted
             ORDER BY er.document_id, er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id), "extracted": _EXTRACTED},
    )
    seen: set[str] = set()
    scores: list[float] = []
    for r in rows.fetchall():
        doc = str(r._mapping["document_id"])
        if doc in seen:
            continue  # an older extraction of a document already counted
        seen.add(doc)
        score = _overall(r._mapping["confidence_report"])
        if score is not None:
            scores.append(score)
    if not scores:
        return None
    return sum(scores) / len(scores)


async def refresh_deal_ai_confidence(
    session: AsyncSession, *, deal_id: UUID | str, tenant_id: UUID | str
) -> float | None:
    """Recompute the rollup and write it to ``deals.ai_confidence`` (NULL when
    there is none). The caller commits."""
    confidence = await compute_deal_ai_confidence(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    await session.execute(
        text(
            "UPDATE deals SET ai_confidence = :c "
            "WHERE id = :deal AND tenant_id = :tenant"
        ),
        {"c": confidence, "deal": str(deal_id), "tenant": str(tenant_id)},
    )
    return confidence


async def refresh_deal_ai_confidence_best_effort(
    session: AsyncSession, *, deal_id: UUID | str, tenant_id: UUID | str
) -> None:
    """Refresh and commit; a failure is logged, never raised — the extraction
    or edit that triggered it has already been committed."""
    try:
        await refresh_deal_ai_confidence(session, deal_id=deal_id, tenant_id=tenant_id)
        await session.commit()
    except Exception:  # a rollup must not fail its caller
        logger.warning(
            "deal ai_confidence refresh failed: deal=%s", deal_id, exc_info=True
        )
        with contextlib.suppress(Exception):
            await session.rollback()


__all__ = [
    "compute_deal_ai_confidence",
    "refresh_deal_ai_confidence",
    "refresh_deal_ai_confidence_best_effort",
]
