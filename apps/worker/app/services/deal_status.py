"""Wave 5 — the seven-status deal lifecycle: the run gate.

``IC Ready``, ``IC Approved`` and ``IC Not Approved`` need a completed model
run (``fondok_schemas.RUN_GATED_DEAL_STATUSES``). "Completed" means the deal's
latest run — its canonical run, the most recent full base-engine chain that is
not a non-base scenario's run (FON-73, :func:`get_canonical_run_id`, the same
run every deal-wide tab reads) — has finished with no engine failed: every
base engine of it is ``complete`` or ``not_applicable`` (an engine that does
not apply to the deal, e.g. Partnership without terms, is fine). A committee
never sees a deal whose Returns or Debt the model withheld.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .engine_runner import CANONICAL_ENGINES, STATUS_NOT_APPLICABLE, get_canonical_run_id

_COMPLETE = "complete"
_FINISHED_OK: frozenset[str] = frozenset({_COMPLETE, STATUS_NOT_APPLICABLE})


@dataclass(frozen=True)
class RunGate:
    """Whether the run gate is open, and why not."""

    ok: bool
    #: ``no_run`` | ``in_flight`` | ``failed_engines`` | None when ok.
    reason: str | None = None
    #: Base engines of the latest run that failed (``failed_engines``).
    failed_engines: tuple[str, ...] = ()


async def model_run_gate(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> RunGate:
    """The run gate for the three IC statuses (see module docstring)."""
    run_id = await get_canonical_run_id(session, deal_id=deal_id, tenant_id=tenant_id)
    if run_id is None:
        return RunGate(ok=False, reason="no_run")
    placeholders = ", ".join(f":canon_{i}" for i in range(len(CANONICAL_ENGINES)))
    params: dict[str, Any] = {"deal": deal_id, "tenant": tenant_id, "run": run_id}
    for i, name in enumerate(CANONICAL_ENGINES):
        params[f"canon_{i}"] = name
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            f"""
            SELECT engine_name, status
              FROM engine_outputs
             WHERE deal_id = :deal AND tenant_id = :tenant AND run_id = :run
               AND engine_name IN ({placeholders})
            """
        ),
        params,
    )
    by_engine = {str(r._mapping["engine_name"]): str(r._mapping["status"]) for r in rows.fetchall()}
    failed = tuple(e for e in CANONICAL_ENGINES if by_engine.get(e) == "failed")
    if failed:
        return RunGate(ok=False, reason="failed_engines", failed_engines=failed)
    if any(s not in _FINISHED_OK for s in by_engine.values()) or _COMPLETE not in by_engine.values():
        return RunGate(ok=False, reason="in_flight")
    return RunGate(ok=True)


__all__ = ["RunGate", "model_run_gate"]
