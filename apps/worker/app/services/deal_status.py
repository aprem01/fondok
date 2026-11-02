"""Wave 5 — the seven-status deal lifecycle: the run gate.

``IC Ready``, ``IC Approved`` and ``IC Not Approved`` need a completed model
run (``fondok_schemas.RUN_GATED_DEAL_STATUSES``). "Completed" means the deal
has a canonical run — the most recent full base-engine chain that is not a
non-base scenario's run (FON-73, :func:`get_canonical_run_id`, the same run
every deal-wide tab reads) — and that run has finished: no engine of it is
still ``running`` and at least one is ``complete``. A failed engine inside a
finished run does not block the status; the tabs already show that failure.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .engine_runner import CANONICAL_ENGINES, get_canonical_run_id

#: The engine-row status a run still in flight carries.
_RUNNING = "running"
_COMPLETE = "complete"


async def has_completed_model_run(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> bool:
    """True when the deal's canonical run exists and has finished."""
    run_id = await get_canonical_run_id(session, deal_id=deal_id, tenant_id=tenant_id)
    if run_id is None:
        return False
    placeholders = ", ".join(f":canon_{i}" for i in range(len(CANONICAL_ENGINES)))
    params: dict[str, Any] = {"deal": deal_id, "tenant": tenant_id, "run": run_id}
    for i, name in enumerate(CANONICAL_ENGINES):
        params[f"canon_{i}"] = name
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            f"""
            SELECT status
              FROM engine_outputs
             WHERE deal_id = :deal AND tenant_id = :tenant AND run_id = :run
               AND engine_name IN ({placeholders})
            """
        ),
        params,
    )
    statuses = {str(r._mapping["status"]) for r in rows.fetchall()}
    return _RUNNING not in statuses and _COMPLETE in statuses


__all__ = ["has_completed_model_run"]
